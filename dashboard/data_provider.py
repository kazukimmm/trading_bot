"""Data access for the dashboard API.

Strategy:
    * Always read the cached backtest results JSON (written by ``run_backtest``).
    * If Alpaca credentials are present in the environment, overlay *live*
      account equity, positions and portfolio history on top of the cache.
    * If anything live fails, silently fall back to the cached values so the
      dashboard keeps working (the goal: it must render even with no broker).

Switching from paper to live requires no code change -- only the ALPACA_*
environment variables differ.
"""

from __future__ import annotations

import calendar
import json
import os
import time
from datetime import date, datetime
from functools import lru_cache
from pathlib import Path
from typing import Any

from utils.logger import get_logger

_logger = get_logger(__name__)
_CACHE_FILE = Path(__file__).resolve().parent.parent / "backtesting" / "results" / "backtest_results.json"

# USD/JPY レートの簡易キャッシュ（yfinance を毎リクエスト叩かない）。
_FX_CACHE: dict[str, Any] = {"rate": None, "ts": 0.0, "updated_at": ""}
_FX_TTL_SECONDS = 600  # 10 分
_FX_FALLBACK = 150.0  # 取得失敗時の保守的な既定値

# Alpaca ペーパー口座の初期資金（標準は $100,000）。
# 戦略リターン率 = 現在の口座資産 / この値 − 1 として算出する。
_PAPER_START_EQUITY = float(os.getenv("ALPACA_PAPER_START_EQUITY", "100000") or "100000")


# --------------------------------------------------------------------------- #
# Rebalance-date helpers (first trading day of the month, holidays ignored).
# --------------------------------------------------------------------------- #
def _first_trading_day(year: int, month: int) -> date:
    """Return the first weekday (Mon-Fri) of the given month."""
    for day in range(1, 8):
        d = date(year, month, day)
        if d.weekday() < 5:
            return d
    return date(year, month, 1)


def last_rebalance(today: date | None = None) -> date:
    """Return the most recent monthly rebalance date on or before ``today``."""
    today = today or date.today()
    this_month = _first_trading_day(today.year, today.month)
    if today >= this_month:
        return this_month
    prev_year, prev_month = (today.year - 1, 12) if today.month == 1 else (today.year, today.month - 1)
    return _first_trading_day(prev_year, prev_month)


def next_rebalance(today: date | None = None) -> date:
    """Return the next monthly rebalance date strictly after ``today``."""
    today = today or date.today()
    this_month = _first_trading_day(today.year, today.month)
    if today < this_month:
        return this_month
    next_year, next_month = (today.year + 1, 1) if today.month == 12 else (today.year, today.month + 1)
    return _first_trading_day(next_year, next_month)


# --------------------------------------------------------------------------- #
# Cache loading.
# --------------------------------------------------------------------------- #
def load_cache() -> dict[str, Any]:
    """Load the cached backtest results JSON.

    Returns:
        Parsed JSON dict, or an empty skeleton if the cache is missing.
    """
    if not _CACHE_FILE.exists():
        _logger.warning("Backtest cache not found at %s; run run_backtest.py.", _CACHE_FILE)
        return {"summary": {}, "history": [], "holdings": [], "market_regime": "NEUTRAL"}
    return json.loads(_CACHE_FILE.read_text(encoding="utf-8"))


# --------------------------------------------------------------------------- #
# Optional live broker overlay.
# --------------------------------------------------------------------------- #
@lru_cache(maxsize=1)
def _executor() -> Any | None:
    """Build an OrderExecutor if Alpaca creds exist, else return None (cached)."""
    api_key = os.getenv("ALPACA_API_KEY", "").strip()
    secret = os.getenv("ALPACA_SECRET_KEY", "").strip()
    if not api_key or not secret:
        return None
    try:
        from core.order_executor import OrderExecutor

        is_paper = "paper" in os.getenv("ALPACA_BASE_URL", "paper")
        return OrderExecutor(api_key, secret, paper=is_paper)
    except Exception as exc:  # noqa: BLE001 - live overlay is best-effort
        _logger.warning("Could not init Alpaca executor: %s", exc)
        return None


def _live_equity() -> float | None:
    """Return live account equity if available, else None."""
    ex = _executor()
    if ex is None:
        return None
    try:
        return ex.get_account_equity()
    except Exception as exc:  # noqa: BLE001
        _logger.warning("Live equity unavailable: %s", exc)
        return None


def _live_positions() -> list[dict[str, Any]] | None:
    """Return live holdings shaped for the API, or None if unavailable."""
    ex = _executor()
    if ex is None:
        return None
    try:
        positions = ex.get_current_positions()
    except Exception as exc:  # noqa: BLE001
        _logger.warning("Live positions unavailable: %s", exc)
        return None
    total = sum(p["market_value"] for p in positions.values()) or 1.0
    holdings: list[dict[str, Any]] = []
    for symbol, p in positions.items():
        cost_basis = p["market_value"] - p["unrealized_pl"]
        ret_pct = (p["unrealized_pl"] / cost_basis * 100) if cost_basis else 0.0
        holdings.append(
            {
                "symbol": symbol,
                "weight": round(p["market_value"] / total, 3),
                "value": round(p["market_value"], 2),
                "return_pct": round(ret_pct, 2),
            }
        )
    return holdings


# --------------------------------------------------------------------------- #
# Public API shapes consumed by the routes.
# --------------------------------------------------------------------------- #
def _months_invested(start: date, today: date) -> int:
    """開始月から今月までの積立回数（毎月1回・両端含む）を返す。最低1。"""
    n = (today.year - start.year) * 12 + (today.month - start.month) + 1
    return max(1, n)


def _yen_accumulation(strategy_return: float) -> dict[str, Any]:
    """円建ての積立シミュレーション値を計算する（為替に依存しない）。

    あなたは円で毎月一定額を積み立て、戦略は「リターン率」を生むだけなので、
    元本・評価額・損益はすべて円のまま算出できる（ペーパー口座の $100k や
    為替レートには左右されない）。評価額は各月の積立額に戦略リターンを
    時間按分（ドルコスト平均法の線形近似）して合算する。

    Args:
        strategy_return: 運用開始からの戦略リターン率（例: 0.05 = +5%）。

    Returns:
        円建ての元本・評価額・損益・積立回数などを含む dict。
    """
    monthly = float(os.getenv("MONTHLY_CONTRIBUTION_JPY", "100000") or "100000")
    start_raw = os.getenv("INVESTMENT_START_DATE", "").strip()
    today = date.today()
    try:
        start = datetime.strptime(start_raw, "%Y-%m-%d").date() if start_raw else today
    except ValueError:
        start = today

    n = _months_invested(start, today)
    principal = monthly * n
    # 線形 DCA 近似: Σ_{k=0}^{n-1} monthly*(1 + r*(n-k)/n) = monthly*(n + r*(n+1)/2)
    value = monthly * (n + strategy_return * (n + 1) / 2.0)
    pl = value - principal
    return {
        "monthly_contribution_jpy": round(monthly),
        "months_invested": n,
        "principal_jpy": round(principal),
        "value_jpy": round(value),
        "pl_jpy": round(pl),
        "yen_return_pct": round(strategy_return * 100, 2),
    }


def portfolio_summary() -> dict[str, Any]:
    """Build the /api/portfolio/summary payload (live overlay if available)."""
    cache = load_cache()
    history = cache.get("history", [])
    values = [pt["value"] for pt in history] or [0.0]

    cache_total = values[-1]
    initial = values[0] if values else 1.0
    peak = max(values)

    live_equity = _live_equity()
    total_value = live_equity if live_equity is not None else cache_total
    peak = max(peak, total_value)

    today_return = 0.0
    if len(values) >= 2 and values[-2]:
        today_return = (values[-1] / values[-2] - 1.0) * 100

    total_return = (total_value / initial - 1.0) * 100 if initial else 0.0
    drawdown = (1.0 - total_value / peak) * 100 if peak else 0.0

    # 元本（最初に投資したドル金額）。.env の INITIAL_INVESTMENT を優先。
    try:
        initial_investment = float(os.getenv("INITIAL_INVESTMENT", "") or initial)
    except ValueError:
        initial_investment = initial

    # 円建ての積立シミュレーション。戦略リターン率は「ライブのペーパー口座が
    # 開始時($100k)から何%増えたか」。ライブが取れないときは 0%（元本のみ表示）。
    if live_equity is not None and _PAPER_START_EQUITY > 0:
        strategy_return = live_equity / _PAPER_START_EQUITY - 1.0
    else:
        strategy_return = 0.0
    yen = _yen_accumulation(strategy_return)

    return {
        "total_value": round(total_value, 2),
        "total_return_pct": round(total_return, 2),
        "today_return_pct": round(today_return, 2),
        "peak_value": round(peak, 2),
        "current_drawdown_pct": round(drawdown, 2),
        "initial_investment": round(initial_investment, 2),
        "market_regime": cache.get("market_regime", "NEUTRAL"),
        "last_rebalance": last_rebalance().isoformat(),
        "next_rebalance": next_rebalance().isoformat(),
        "data_source": "live" if live_equity is not None else "backtest",
        **yen,
    }


def portfolio_holdings() -> list[dict[str, Any]]:
    """Build the /api/portfolio/holdings payload (live overlay if available)."""
    live = _live_positions()
    if live:
        return live
    return load_cache().get("holdings", [])


def portfolio_history() -> list[dict[str, Any]]:
    """Build the /api/portfolio/history payload (daily equity points)."""
    return load_cache().get("history", [])


def recent_decisions(limit: int = 10) -> list[dict[str, Any]]:
    """Build the /api/portfolio/decisions payload (judgment review cards).

    Reads the local decision-history log. Returns an empty list if nothing has
    been recorded yet, so the dashboard renders a friendly empty state.

    Args:
        limit: Max number of recent decisions to return.

    Returns:
        List of decision dicts (newest first) shaped for the UI.
    """
    try:
        from core.decision_logger import DecisionLogger

        records = DecisionLogger().get_recent_decisions(limit)
    except Exception as exc:  # noqa: BLE001 - review panel is best-effort
        _logger.warning("Could not read decision history: %s", exc)
        return []
    return [
        {
            "date": r.get("date", ""),
            "symbol": r.get("symbol", ""),
            "action": r.get("action", ""),
            "reason": r.get("reason", ""),
            "momentum_score": r.get("momentum_score"),
            "price_at_decision": r.get("price_at_decision"),
            "price_now": r.get("price_now"),
            "return_pct": r.get("return_pct"),
            "result_label": r.get("result_label", "継続中"),
            "market_regime": r.get("market_regime", "NEUTRAL"),
        }
        for r in records
    ]


def _default_advice() -> dict[str, Any]:
    """まだ提案が生成されていないときに表示するデフォルト診断（常時表示用）。"""
    return {
        "has_advice": True,
        "triggered_at": "",
        "trigger_reason": "まだ十分な運用データがありません",
        "diagnosis": (
            "初回の戦略レビュー（毎月末）がまだ実行されていません。"
            "現時点では設定どおりの保守的なルールで運用されており、"
            "特別な対応は不要です。"
        ),
        "suggestions": [
            {
                "title": "現状維持を推奨",
                "detail": "運用実績が貯まるまでは設定を変えずに様子を見るのが安全です。"
                "毎月末のレビューで最新の診断に自動更新されます。",
                "risk_level": "低",
            }
        ],
        "keep_current": True,
        "urgency": "low",
    }


def strategy_advice() -> dict[str, Any]:
    """Build the /api/strategy/advice payload.

    The advice is *always* shown (理由付きで常に表示): when no advice has been
    generated yet, a neutral "keep current" default is returned. The
    ``dismissed`` flag no longer hides the banner -- it only suppresses the
    one-off "確認済み" highlight client-side if needed.
    """
    try:
        from core.strategy_advisor import load_advice

        advice = load_advice()
    except Exception as exc:  # noqa: BLE001 - advice panel is best-effort
        _logger.warning("Could not read strategy advice: %s", exc)
        advice = None
    if not advice:
        return _default_advice()
    return {
        "has_advice": True,
        "triggered_at": advice.get("triggered_at", ""),
        "trigger_reason": advice.get("trigger_reason", ""),
        "diagnosis": advice.get("diagnosis", ""),
        "suggestions": advice.get("suggestions", []),
        "keep_current": bool(advice.get("keep_current", False)),
        "urgency": advice.get("urgency", "low"),
        "dismissed": bool(advice.get("dismissed", False)),
    }


def dismiss_strategy_advice() -> dict[str, Any]:
    """Mark the current strategy advice as acknowledged (hidden until next run)."""
    try:
        from core.strategy_advisor import dismiss_advice

        ok = dismiss_advice()
    except Exception as exc:  # noqa: BLE001
        _logger.warning("Could not dismiss strategy advice: %s", exc)
        ok = False
    return {"dismissed": ok}


def usdjpy_rate() -> dict[str, Any]:
    """Return the current USD/JPY rate for the dashboard's yen display.

    Uses yfinance (cached for a few minutes). Falls back to a conservative
    default if the network call fails, so the dashboard always renders.

    Returns:
        ``{"rate": float, "updated_at": "YYYY-MM-DD HH:MM"}``.
    """
    now = time.time()
    if _FX_CACHE["rate"] is not None and (now - _FX_CACHE["ts"]) < _FX_TTL_SECONDS:
        return {"rate": _FX_CACHE["rate"], "updated_at": _FX_CACHE["updated_at"]}

    rate: float | None = None
    try:
        import yfinance as yf

        # 仕様どおり JPYUSD=X（1円=Xドル）を取得して反転。
        jpyusd = yf.Ticker("JPYUSD=X").fast_info["last_price"]
        if jpyusd:
            rate = 1.0 / float(jpyusd)
    except Exception as exc:  # noqa: BLE001 - FX is best-effort
        _logger.warning("USDJPY via JPYUSD=X failed: %s", exc)

    if rate is None:
        try:
            import yfinance as yf

            usdjpy = yf.Ticker("USDJPY=X").fast_info["last_price"]
            if usdjpy:
                rate = float(usdjpy)
        except Exception as exc:  # noqa: BLE001
            _logger.warning("USDJPY via USDJPY=X failed: %s", exc)

    if rate is None or not (50.0 < rate < 500.0):
        # キャッシュがあれば古い値を、無ければ既定値を返す。
        rate = _FX_CACHE["rate"] or _FX_FALLBACK

    updated_at = datetime.now().strftime("%Y-%m-%d %H:%M")
    _FX_CACHE.update({"rate": round(rate, 2), "ts": now, "updated_at": updated_at})
    return {"rate": _FX_CACHE["rate"], "updated_at": updated_at}


def backtest_results() -> dict[str, Any]:
    """Build the /api/backtest/results payload from the cached summary."""
    summary = load_cache().get("summary", {})
    return {
        "cagr": summary.get("cagr", 0.0),
        "max_drawdown": summary.get("max_drawdown", 0.0),
        "sharpe_ratio": summary.get("sharpe_ratio", 0.0),
        "win_rate": summary.get("win_rate", 0.0),
        "total_return": summary.get("total_return", 0.0),
        "vs_spy_alpha": summary.get("vs_spy_alpha", 0.0),
    }
