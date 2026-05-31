"""月次レポートの生成と Slack 送信。

毎月末に、今月のリターン・累計リターン・保有銘柄・来月の予想リバランス・
ドローダウン状況・バックテストとの乖離をまとめたレポートを作成し、
Slack に送信します。

月をまたいだ比較のため、月末の総資産を ``logs/monthly_snapshots.json`` に
スナップショットとして保存します（初回はベースラインとして記録）。

実行方法:
    python utils/monthly_report.py
"""

from __future__ import annotations

import json
import sys
from datetime import date
from pathlib import Path

# プロジェクトルートを import パスに追加（`python utils/monthly_report.py`
# のように直接実行してもパッケージを解決できるようにする）。
_PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from config.settings import Settings, get_settings  # noqa: E402
from core.data_fetcher import DataFetcher
from core.order_executor import OrderExecutor
from core.strategy import generate_signals
from utils.logger import get_logger
from utils.notifier import SlackNotifier

_logger = get_logger(__name__)

_SNAPSHOT_FILE = Path(__file__).resolve().parent.parent / "logs" / "monthly_snapshots.json"
_STATE_FILE = Path(__file__).resolve().parent.parent / "logs" / "state.json"
_RESULTS_FILE = (
    Path(__file__).resolve().parent.parent
    / "backtesting"
    / "results"
    / "backtest_results.json"
)

_MONTH_NAMES_JP = {
    1: "1月", 2: "2月", 3: "3月", 4: "4月", 5: "5月", 6: "6月",
    7: "7月", 8: "8月", 9: "9月", 10: "10月", 11: "11月", 12: "12月",
}


def _load_json(path: Path) -> dict:
    """JSON を読み込む（存在しない/壊れている場合は空 dict）。"""
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (ValueError, OSError):
        return {}


def _load_peak() -> float:
    """state.json から running-peak equity を読み込む（無ければ 0）。"""
    state = _load_json(_STATE_FILE)
    try:
        return float(state.get("peak_equity", 0.0))
    except (TypeError, ValueError):
        return 0.0


def _backtest_cagr_pct() -> float | None:
    """バックテスト結果の CAGR（%）を返す（無ければ None）。"""
    payload = _load_json(_RESULTS_FILE)
    try:
        return float(payload["summary"]["cagr"])
    except (KeyError, TypeError, ValueError):
        return None


class _SnapshotStore:
    """月末スナップショット（年月→総資産）の永続化を担う。"""

    def __init__(self, path: Path = _SNAPSHOT_FILE) -> None:
        self.path = path
        data = _load_json(path)
        self.baseline: float = float(data.get("baseline", 0.0) or 0.0)
        self.months: dict[str, float] = dict(data.get("months", {}))

    def previous_equity(self, current_key: str) -> float | None:
        """``current_key`` より前の最新スナップショットの総資産を返す。"""
        prior = sorted(k for k in self.months if k < current_key)
        return self.months[prior[-1]] if prior else None

    def record(self, key: str, equity: float) -> None:
        """今月のスナップショットを記録し、初回ならベースラインも設定する。"""
        if self.baseline <= 0.0:
            self.baseline = equity
        self.months[key] = round(equity, 2)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(
            json.dumps({"baseline": self.baseline, "months": self.months}, indent=2),
            encoding="utf-8",
        )


def _holdings_lines(positions: dict[str, dict[str, float]], equity: float) -> str:
    """保有銘柄を「- TICKER: xx.x% (+y.y%)」形式の複数行で返す。"""
    if not positions:
        return "  （現在ノーポジション / 現金）"
    lines: list[str] = []
    for symbol, p in sorted(
        positions.items(), key=lambda kv: kv[1]["market_value"], reverse=True
    ):
        mv = p["market_value"]
        weight = (mv / equity * 100.0) if equity > 0 else 0.0
        cost = mv - p["unrealized_pl"]
        pl_pct = (p["unrealized_pl"] / cost * 100.0) if cost > 0 else 0.0
        lines.append(f"- {symbol}: {weight:.1f}% ({pl_pct:+.1f}%)")
    return "\n".join(lines)


def _predicted_rebalance(
    fetcher: DataFetcher, settings: Settings, current: dict[str, dict[str, float]]
) -> str:
    """来月の予想リバランス銘柄を、現保有との差分付きで返す。"""
    try:
        closes = fetcher.get_historical_closes(list(settings.watchlist), period="2y")
        prices_dict = {t: closes[t].dropna() for t in settings.watchlist}
        signals = generate_signals(
            prices_dict,
            watchlist=settings.watchlist,
            benchmark=settings.benchmark,
            safe_asset=settings.safe_asset,
            lookback_months=settings.momentum_lookback_months,
            top_n=settings.top_n_holdings,
            require_positive_momentum=settings.require_positive_momentum,
        )
    except Exception as exc:  # noqa: BLE001 - 予測は補助情報なので失敗しても続行
        _logger.warning("Predicted rebalance unavailable: %s", exc)
        return "  （価格データ取得に失敗したため予測なし）"

    held = set(current)
    lines: list[str] = []
    for symbol in signals:
        if symbol in held:
            lines.append(f"- {symbol}継続保有予定")
        else:
            lines.append(f"- {symbol}を新規取得予定")
    for symbol in held - set(signals):
        lines.append(f"- {symbol}は売却予定")
    return "\n".join(lines) if lines else "  （予測なし）"


def build_report(
    executor: OrderExecutor,
    fetcher: DataFetcher,
    settings: Settings,
    *,
    today: date | None = None,
    store: _SnapshotStore | None = None,
) -> str:
    """月次レポート本文（Slack mrkdwn）を生成する。

    Args:
        executor: 口座情報・保有銘柄の取得に使う執行クライアント。
        fetcher: 来月予測のための価格取得クライアント。
        settings: アプリ設定。
        today: 基準日（省略時は本日）。テスト用に上書き可能。
        store: スナップショット永続化（省略時は既定ファイル）。

    Returns:
        Slack に送信できる整形済みの本文文字列。
    """
    today = today or date.today()
    store = store or _SnapshotStore()
    month_key = f"{today.year:04d}-{today.month:02d}"

    info = executor.get_account_info()
    equity = float(info["equity"])
    positions = executor.get_current_positions()

    # 今月のリターン（前回スナップショット比）。
    prev_equity = store.previous_equity(month_key)
    if prev_equity and prev_equity > 0:
        month_ret_pct = (equity / prev_equity - 1.0) * 100.0
        month_ret_str = f"{month_ret_pct:+.1f}%"
    else:
        month_ret_str = "—（初回のため基準なし）"

    # 累計リターン（ベースライン比）。
    baseline = store.baseline or equity
    cum_ret_pct = (equity / baseline - 1.0) * 100.0 if baseline > 0 else 0.0

    # ドローダウン状況。
    peak = _load_peak() or equity
    drawdown_pct = (1.0 - equity / peak) * 100.0 if peak > 0 else 0.0
    dd_limit_pct = settings.max_drawdown_threshold * 100.0

    # バックテストとの乖離（年率換算の実績 vs バックテスト CAGR）。
    bt_cagr = _backtest_cagr_pct()
    months_elapsed = max(1, len(store.months))  # 経過月数の概算
    annualized_actual = (
        ((equity / baseline) ** (12.0 / months_elapsed) - 1.0) * 100.0
        if baseline > 0 and equity > 0
        else 0.0
    )
    if bt_cagr is not None:
        divergence = annualized_actual - bt_cagr
        within = abs(divergence) <= 5.0
        divergence_str = (
            f"{divergence:+.1f}%"
            f"（{'正常範囲内' if within else '要確認: ±5%超'}）"
        )
    else:
        divergence_str = "—（バックテスト結果なし）"

    holdings = _holdings_lines(positions, equity)
    predicted = _predicted_rebalance(fetcher, settings, positions)

    report = (
        f"📊 *月次レポート - {today.year}年{_MONTH_NAMES_JP[today.month]}*\n"
        f"💰 今月のリターン: {month_ret_str}\n"
        f"📈 累計リターン: {cum_ret_pct:+.1f}%\n"
        f"💵 現在の総資産: ${equity:,.2f}\n"
        f"🏦 口座: {'ペーパー' if info['is_paper'] else 'ライブ'}\n"
        f"\n*現在の保有銘柄*\n{holdings}\n"
        f"\n*来月の予想リバランス*\n{predicted}\n"
        f"\n📉 現在のドローダウン: -{drawdown_pct:.1f}%（制限: -{dd_limit_pct:.0f}%）\n"
        f"✅ バックテストとの乖離: {divergence_str}"
    )

    # 今月のスナップショットを記録（次月の比較用）。
    store.record(month_key, equity)
    return report


def send_monthly_report(today: date | None = None) -> str:
    """設定を読み込み、レポートを生成して Slack に送信する。

    Args:
        today: 基準日（省略時は本日）。

    Returns:
        生成したレポート本文（送信内容と同一）。
    """
    settings = get_settings()
    executor = OrderExecutor(
        api_key=settings.alpaca_api_key,
        secret_key=settings.alpaca_secret_key,
        base_url=settings.alpaca_base_url,
        paper=settings.is_paper,
        retry_attempts=settings.retry_attempts,
        retry_backoff_base=settings.retry_backoff_base,
    )
    fetcher = DataFetcher(
        retry_attempts=settings.retry_attempts,
        retry_backoff_base=settings.retry_backoff_base,
        timeout=settings.request_timeout,
        trading_client=executor.trading_client,
    )
    report = build_report(executor, fetcher, settings, today=today)
    notifier = SlackNotifier(settings.slack_webhook_url)
    notifier.notify_monthly_summary(report)
    return report


def main() -> None:
    """コマンドラインから月次レポートを生成・送信する。"""
    import sys

    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8")  # type: ignore[union-attr]
        except (AttributeError, ValueError):
            pass
    report = send_monthly_report()
    print(report)


if __name__ == "__main__":
    main()
