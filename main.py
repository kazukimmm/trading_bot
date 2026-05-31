"""Live trading entry point.

Wires the strategy, risk manager, broker executor and notifier together behind
an APScheduler timetable (US/Eastern):

    * Monthly, first trading day, 09:35 -- rebalance.
    * Daily, 15:55 -- drawdown monitor (force de-risk if breached).
    * Monthly, last business day, 16:10 -- performance summary.

Run with::

    python main.py
"""

from __future__ import annotations

import calendar
import json
from datetime import date, datetime
from pathlib import Path

from apscheduler.schedulers.blocking import BlockingScheduler
from apscheduler.triggers.cron import CronTrigger

from config.settings import Settings, get_settings
from dashboard.push_sender import PushSender
from core.data_fetcher import DataFetcher
from core.exceptions import TradingBotError
from core.order_executor import OrderExecutor
from core.risk_manager import adjust_position_sizes, check_drawdown, validate_order
from core.strategy import generate_signals
from utils.logger import get_logger
from utils.notifier import SlackNotifier

_TIMEZONE = "America/New_York"
_STATE_FILE = Path(__file__).resolve().parent / "logs" / "state.json"


def _is_first_trading_day(today: date) -> bool:
    """Return whether ``today`` is the first weekday (Mon-Fri) of its month.

    Note: this ignores exchange holidays; the rebalance job additionally checks
    that the market is open before trading, so a holiday simply skips trading.

    Args:
        today: The date to test.

    Returns:
        True if no earlier weekday exists in the month.
    """
    for day in range(1, today.day):
        if date(today.year, today.month, day).weekday() < 5:
            return False
    return today.weekday() < 5


def _is_last_business_day(today: date) -> bool:
    """Return whether ``today`` is the last weekday of its month.

    Args:
        today: The date to test.

    Returns:
        True if no later weekday exists in the month.
    """
    last_day = calendar.monthrange(today.year, today.month)[1]
    for day in range(today.day + 1, last_day + 1):
        if date(today.year, today.month, day).weekday() < 5:
            return False
    return today.weekday() < 5


class TradingBot:
    """Coordinates scheduled rebalancing and risk monitoring."""

    def __init__(self, settings: Settings) -> None:
        """Build the bot and its collaborators from settings.

        Args:
            settings: Validated application configuration.
        """
        self.settings = settings
        self.logger = get_logger(__name__, settings.log_level)
        self.executor = OrderExecutor(
            api_key=settings.alpaca_api_key,
            secret_key=settings.alpaca_secret_key,
            paper=settings.is_paper,
            retry_attempts=settings.retry_attempts,
            retry_backoff_base=settings.retry_backoff_base,
        )
        self.fetcher = DataFetcher(
            retry_attempts=settings.retry_attempts,
            retry_backoff_base=settings.retry_backoff_base,
            timeout=settings.request_timeout,
            trading_client=self.executor.trading_client,
        )
        self.notifier = SlackNotifier(settings.slack_webhook_url)
        self.push = PushSender()
        self._peak_equity = self._load_peak()
        # 「DD警告は 1 日 1 回まで」のため最後に警告した日付を保持する。
        self._last_dd_warn_date: str | None = None

    # --- State persistence -------------------------------------------------

    def _load_peak(self) -> float:
        """Load the persisted running-peak equity (0.0 if absent)."""
        if _STATE_FILE.exists():
            try:
                return float(json.loads(_STATE_FILE.read_text())["peak_equity"])
            except (ValueError, KeyError, json.JSONDecodeError):
                self.logger.warning("Corrupt state file; resetting peak.")
        return 0.0

    def _save_peak(self, peak: float) -> None:
        """Persist the running-peak equity to disk."""
        _STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
        _STATE_FILE.write_text(json.dumps({"peak_equity": peak}))

    def _update_peak(self, equity: float) -> float:
        """Update and persist the running peak, returning the new value."""
        self._peak_equity = max(self._peak_equity, equity)
        self._save_peak(self._peak_equity)
        return self._peak_equity

    # --- Push notifications ------------------------------------------------
    def _safe_push(self, title: str, body: str, urgency: str = "normal") -> None:
        """プッシュ通知を送る（失敗しても取引ループを止めない）。"""
        try:
            self.push.broadcast(title, body, urgency=urgency)
        except Exception as exc:  # noqa: BLE001
            self.logger.error("Push broadcast failed: %s", exc)

    def _maybe_push_drawdown(self, equity: float, peak: float, *, derisked: bool) -> None:
        """ドローダウン状況に応じてプッシュ通知を送る。

        - 15% 以上（自動売却が発動）: 🔴 高緊急度を毎回通知。
        - 10% 以上 15% 未満（警告）: 🔴 高緊急度を 1 日 1 回だけ通知。

        Args:
            equity: 現在の資産額。
            peak: 直近ピーク資産額。
            derisked: 自動売却（全ポジション退避）が発動したか。
        """
        dd = (1.0 - equity / peak) if peak else 0.0
        if derisked or dd >= 0.15:
            self._safe_push(
                "🔴 自動売却を実行しました",
                f"ドローダウン {dd:.1%} に達したため全ポジションを退避しました。"
                f"（資産 ${equity:,.0f} / ピーク ${peak:,.0f}）",
                urgency="high",
            )
            return
        if dd >= 0.10:
            today = date.today().isoformat()
            if self._last_dd_warn_date == today:
                return  # 当日はすでに警告済み
            self._last_dd_warn_date = today
            self._safe_push(
                "🔴 ドローダウン警告",
                f"現在のドローダウンは {dd:.1%} です（15% で自動売却）。"
                f"資産 ${equity:,.0f} / ピーク ${peak:,.0f}。",
                urgency="high",
            )

    # --- Startup -----------------------------------------------------------

    def verify_connectivity(self) -> None:
        """Confirm broker connectivity at startup.

        Raises:
            TradingBotError: If the account cannot be reached.
        """
        equity = self.executor.get_account_equity()
        self.logger.info(
            "Connected to Alpaca (%s). Equity: $%.2f",
            "paper" if self.settings.is_paper else "LIVE",
            equity,
        )
        self._update_peak(equity)

    # --- Scheduled jobs ----------------------------------------------------

    def rebalance(self) -> None:
        """Monthly rebalance: only runs on the first trading day."""
        today = datetime.now().date()
        if not _is_first_trading_day(today):
            self.logger.debug("Not the first trading day (%s); skip rebalance.", today)
            return
        try:
            if not self.fetcher.is_market_open():
                self.logger.info("Market closed; skipping rebalance for %s.", today)
                return
            self._do_rebalance()
        except TradingBotError as exc:
            self.logger.exception("Rebalance failed.")
            self.notifier.notify_error("rebalance", exc)

    def _do_rebalance(self) -> None:
        """Compute targets and execute the rebalance trades."""
        s = self.settings
        equity = self.executor.get_account_equity()
        peak = self._update_peak(equity)

        closes = self.fetcher.get_historical_closes(list(s.watchlist), period="2y")
        prices_dict = {t: closes[t].dropna() for t in s.watchlist}

        if check_drawdown(equity, peak, s.max_drawdown_threshold):
            self.logger.warning("Drawdown breached at rebalance; going risk-off.")
            self.notifier.notify_drawdown(equity, peak)
            self._maybe_push_drawdown(equity, peak, derisked=True)
            signals = {s.safe_asset: 1.0}
            mode = "risk_off"
        else:
            self._maybe_push_drawdown(equity, peak, derisked=False)
            signals = generate_signals(
                prices_dict,
                watchlist=s.watchlist,
                benchmark=s.benchmark,
                safe_asset=s.safe_asset,
                lookback_months=s.momentum_lookback_months,
                top_n=s.top_n_holdings,
                require_positive_momentum=s.require_positive_momentum,
            )
            mode = "risk_off" if signals == {s.safe_asset: 1.0} else "risk_on"

        allocations = adjust_position_sizes(
            signals,
            equity,
            max_position_size=s.max_position_size,
            exempt_symbols=frozenset({s.safe_asset}),
        )

        # Flatten, then enter target positions with fresh cash.
        self.executor.close_all_positions()
        cash = self.executor.get_account_equity()
        for symbol, amount in allocations.items():
            order_amount = min(amount, cash * s.max_order_balance_ratio)
            try:
                validate_order(symbol, order_amount, cash, s.max_order_balance_ratio)
            except TradingBotError as exc:
                self.logger.error("Order validation failed for %s: %s", symbol, exc)
                self.notifier.notify_error(f"order validation ({symbol})", exc)
                continue
            self.executor.submit_order(symbol, order_amount, side="buy")

        self.logger.info("Rebalance complete (mode=%s): %s", mode, allocations)
        self.notifier.notify_rebalance(allocations, mode)
        _mode_jp = "リスクオフ（安全資産へ退避）" if mode == "risk_off" else "リスクオン"
        _holds = "、".join(allocations.keys()) or "なし"
        self._safe_push(
            "🔄 リバランス完了",
            f"モード: {_mode_jp}\n保有: {_holds}",
            urgency="normal",
        )

        # 判断ログを記録（振り返り用）。失敗しても取引は止めない。
        try:
            self._log_decisions(closes, signals, mode)
        except Exception:  # noqa: BLE001 - logging is non-critical
            self.logger.exception("Decision logging failed (non-critical).")

    def _log_decisions(
        self, closes: object, signals: dict[str, float], mode: str
    ) -> None:
        """Record this rebalance's per-symbol decisions for later review.

        Args:
            closes: Aligned close-price DataFrame used for the decision.
            signals: Target weights by symbol from the strategy.
            mode: ``"risk_on"`` or ``"risk_off"``.
        """
        from backtesting.performance_metrics import market_regime
        from core.decision_logger import DecisionLogger
        from core.strategy import compute_lookback_return

        s = self.settings
        today = datetime.now().date().isoformat()
        store = DecisionLogger()

        scores: dict[str, float] = {}
        for ticker in s.watchlist:
            try:
                scores[ticker] = (
                    compute_lookback_return(
                        closes[ticker].dropna(), s.momentum_lookback_months
                    )
                    * 100.0
                )
            except Exception:  # noqa: BLE001 - score is a display value
                scores[ticker] = 0.0
        regime = market_regime(scores.get(s.benchmark, 0.0) / 100.0)
        ranking = [t for t, _ in sorted(scores.items(), key=lambda kv: kv[1], reverse=True)]

        for symbol in signals:
            try:
                price = float(closes[symbol].dropna().iloc[-1])
            except Exception:  # noqa: BLE001
                price = 0.0
            score = scores.get(symbol, 0.0)
            if symbol == s.safe_asset and mode == "risk_off":
                action = "retreat"
                reason = (
                    f"{s.benchmark}の絶対モメンタムがマイナス、またはドローダウン"
                    f"制限に達したため安全資産{symbol}へ退避"
                    f"（{s.benchmark} {s.momentum_lookback_months}ヶ月: "
                    f"{scores.get(s.benchmark, 0.0):+.1f}%）"
                )
            else:
                action = "buy"
                rank = ranking.index(symbol) + 1 if symbol in ranking else 0
                reason = (
                    f"モメンタムスコアが{s.momentum_lookback_months}ヶ月リターン"
                    f"{score:+.1f}%で上位{rank}位"
                )
            store.log_decision(
                date=today,
                symbol=symbol,
                action=action,
                reason=reason,
                momentum_score=score,
                price_at_decision=price,
                market_regime=regime,
            )

    def drawdown_check(self) -> None:
        """Daily drawdown monitor: force de-risk if the limit is breached."""
        s = self.settings
        try:
            equity = self.executor.get_account_equity()
            peak = self._update_peak(equity)
            if check_drawdown(equity, peak, s.max_drawdown_threshold):
                self.logger.warning("Daily drawdown breach: equity=%.2f peak=%.2f", equity, peak)
                self.notifier.notify_drawdown(equity, peak)
                self._maybe_push_drawdown(equity, peak, derisked=True)
                self.executor.close_all_positions()
                if self.fetcher.is_market_open():
                    cash = self.executor.get_account_equity()
                    amount = cash * s.max_order_balance_ratio
                    self.executor.submit_order(s.safe_asset, amount, side="buy")
            else:
                self._maybe_push_drawdown(equity, peak, derisked=False)
        except TradingBotError as exc:
            self.logger.exception("Drawdown check failed.")
            self.notifier.notify_error("drawdown check", exc)

    def monthly_summary(self) -> None:
        """Send a monthly performance report on the last business day."""
        today = datetime.now().date()
        if not _is_last_business_day(today):
            return
        try:
            # Rich month-over-month report (returns, holdings, predicted
            # rebalance, drawdown, backtest divergence). Falls back to a basic
            # summary if anything in the report pipeline fails.
            from utils.monthly_report import build_report

            report = build_report(
                self.executor, self.fetcher, self.settings, today=today
            )
            self.logger.info("Monthly report generated.")
            self.notifier.notify_monthly_summary(report)
        except Exception as exc:  # noqa: BLE001 - report is non-critical
            self.logger.warning("Rich monthly report failed (%s); using basic.", exc)
            try:
                equity = self.executor.get_account_equity()
                positions = self.executor.get_current_positions()
                holdings = ", ".join(positions) if positions else "cash / none"
                summary = (
                    f"  • Equity: ${equity:,.2f}\n"
                    f"  • Peak: ${self._peak_equity:,.2f}\n"
                    f"  • Holdings: {holdings}"
                )
                self.notifier.notify_monthly_summary(summary)
            except TradingBotError as basic_exc:
                self.logger.exception("Monthly summary failed.")
                self.notifier.notify_error("monthly summary", basic_exc)

    def _recent_monthly_returns(self) -> list[float]:
        """Return recent monthly returns (%) computed from saved snapshots."""
        snap_file = _STATE_FILE.parent / "monthly_snapshots.json"
        if not snap_file.exists():
            return []
        try:
            months = json.loads(snap_file.read_text(encoding="utf-8")).get("months", {})
        except (ValueError, OSError):
            return []
        keys = sorted(months)
        values = [float(months[k]) for k in keys]
        returns: list[float] = []
        for i in range(1, len(values)):
            if values[i - 1]:
                returns.append((values[i] / values[i - 1] - 1.0) * 100.0)
        return returns

    def strategy_review(self) -> None:
        """Monthly AI strategy review: generate advice if performance degrades."""
        today = datetime.now().date()
        if not _is_last_business_day(today):
            return
        try:
            from backtesting.performance_metrics import market_regime
            from core.decision_logger import DecisionLogger
            from core.strategy import compute_lookback_return
            from core.strategy_advisor import StrategyAdvisor

            s = self.settings
            monthly_returns = self._recent_monthly_returns()
            equity = self.executor.get_account_equity()
            peak = self._update_peak(equity)
            current_dd = (1.0 - equity / peak) if peak else 0.0

            advisor = StrategyAdvisor()
            # 提案は「常に」生成・保存してダッシュボードに表示する。
            # 通知（Slack/プッシュ）は成績悪化（トリガー成立）時のみ送る。
            triggered = advisor.should_trigger_review(
                monthly_returns, current_drawdown=current_dd
            )

            closes = self.fetcher.get_historical_closes(list(s.watchlist), period="2y")
            bench_ret = compute_lookback_return(
                closes[s.benchmark].dropna(), s.momentum_lookback_months
            )
            performance_data = {
                "monthly_returns": [round(r, 2) for r in monthly_returns],
                "current_drawdown": round(current_dd, 4),
                "equity": round(equity, 2),
            }
            market_conditions = {
                "regime": market_regime(bench_ret),
                "benchmark_lookback_return_pct": round(bench_ret * 100, 2),
            }
            decisions = DecisionLogger().get_recent_decisions(10)

            advice = advisor.run_and_save(performance_data, decisions, market_conditions)
            self.logger.info(
                "Strategy advice generated: %s (urgency=%s, triggered=%s).",
                advice["trigger_reason"],
                advice["urgency"],
                triggered,
            )
            # 成績良好（トリガー未成立）のときは通知しない（毎月の「異常なし」通知を避ける）。
            if triggered:
                self.notifier.notify_strategy_advice(
                    advice["trigger_reason"], advice["urgency"]
                )
                if advice["urgency"] in ("medium", "high"):
                    self._safe_push(
                        "💡 戦略改善の提案があります",
                        f"きっかけ: {advice['trigger_reason']}\n"
                        "ダッシュボードで詳細を確認してください（自動変更はしません）。",
                        urgency="normal",
                    )
        except Exception as exc:  # noqa: BLE001 - review is non-critical
            self.logger.exception("Strategy review failed (non-critical).")
            self.notifier.notify_error("strategy review", exc)

    # --- Scheduler ---------------------------------------------------------

    def build_scheduler(self) -> BlockingScheduler:
        """Create and configure the APScheduler timetable.

        Returns:
            A ready-to-start :class:`BlockingScheduler`.
        """
        scheduler = BlockingScheduler(timezone=_TIMEZONE)
        scheduler.add_job(
            self.rebalance,
            CronTrigger(day_of_week="mon-fri", hour=9, minute=35, timezone=_TIMEZONE),
            id="rebalance",
            name="Monthly rebalance (first trading day)",
        )
        scheduler.add_job(
            self.drawdown_check,
            CronTrigger(day_of_week="mon-fri", hour=15, minute=55, timezone=_TIMEZONE),
            id="drawdown_check",
            name="Daily drawdown monitor",
        )
        scheduler.add_job(
            self.monthly_summary,
            CronTrigger(day_of_week="mon-fri", hour=16, minute=10, timezone=_TIMEZONE),
            id="monthly_summary",
            name="Monthly performance summary",
        )
        scheduler.add_job(
            self.strategy_review,
            CronTrigger(day_of_week="mon-fri", hour=20, minute=0, timezone=_TIMEZONE),
            id="strategy_review",
            name="Monthly AI strategy review",
        )
        return scheduler


def main() -> None:
    """Bootstrap configuration, verify connectivity, and start the scheduler."""
    settings = get_settings()
    logger = get_logger("main", settings.log_level)
    logger.info("Starting trading bot (environment=%s).", settings.environment)

    bot = TradingBot(settings)
    bot.verify_connectivity()

    scheduler = bot.build_scheduler()
    logger.info("Scheduler configured. Jobs: %s", [j.name for j in scheduler.get_jobs()])
    try:
        scheduler.start()
    except (KeyboardInterrupt, SystemExit):
        logger.info("Scheduler stopped.")


if __name__ == "__main__":
    main()
