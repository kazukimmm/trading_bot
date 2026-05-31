"""Backtest engine for the dual-momentum strategy.

Simulates monthly (first-trading-day) rebalancing from a start date to today,
marking the portfolio to market daily, applying a per-trade transaction cost and
the same drawdown / concentration rules used in live trading.
"""

from __future__ import annotations

from dataclasses import dataclass

import pandas as pd

from core.data_fetcher import DataFetcher
from core.exceptions import StrategyError
from core.risk_manager import adjust_position_sizes, check_drawdown
from core.strategy import generate_signals
from utils.logger import get_logger

_logger = get_logger(__name__)


@dataclass(frozen=True)
class BacktestConfig:
    """Parameters controlling a backtest run.

    Attributes:
        watchlist: ETF universe.
        benchmark: Absolute-momentum gate / comparison ticker.
        safe_asset: Defensive ticker (exempt from concentration cap).
        lookback_months: Momentum lookback in months.
        top_n: Number of risky ETFs held in risk-on mode.
        initial_capital: Starting capital in dollars.
        transaction_cost: Per-trade cost as a fraction of traded notional.
        max_drawdown_threshold: Peak-to-now drop that forces de-risking.
        max_position_size: Concentration cap per risky symbol.
        start: ISO start date.
    """

    watchlist: tuple[str, ...] = ("SPY", "QQQ", "IWM", "EFA", "GLD", "TLT", "BIL")
    benchmark: str = "SPY"
    safe_asset: str = "BIL"
    lookback_months: int = 9
    top_n: int = 3
    require_positive_momentum: bool = True
    initial_capital: float = 1000.0
    transaction_cost: float = 0.001
    max_drawdown_threshold: float = 0.15
    max_position_size: float = 0.40
    start: str = "2010-01-01"


def _first_trading_days(index: pd.DatetimeIndex) -> list[pd.Timestamp]:
    """Return the first trading day of each calendar month in ``index``.

    Args:
        index: Sorted DatetimeIndex of trading days.

    Returns:
        List of timestamps, one per month (the earliest day present).
    """
    frame = pd.DataFrame(index=index)
    grouped = frame.groupby([index.year, index.month])
    return [pd.Timestamp(group.index.min()) for _, group in grouped]


class BacktestEngine:
    """Runs a dual-momentum backtest over historical close prices."""

    def __init__(self, config: BacktestConfig, fetcher: DataFetcher | None = None) -> None:
        """Initialize the engine.

        Args:
            config: Backtest parameters.
            fetcher: Optional DataFetcher; a default one is created if omitted.
        """
        self.config = config
        self.fetcher = fetcher or DataFetcher()

    def load_prices(self) -> pd.DataFrame:
        """Download and align daily close prices for the universe.

        Returns:
            DataFrame indexed by date, one column per watchlist ticker, sliced to
            the configured start date.
        """
        closes = self.fetcher.get_historical_closes(
            list(self.config.watchlist), period="max"
        )
        closes = closes.sort_index().ffill().dropna()
        closes = closes[closes.index >= pd.Timestamp(self.config.start)]
        return closes

    def run(self, closes: pd.DataFrame | None = None) -> pd.DataFrame:
        """Execute the backtest.

        Args:
            closes: Optional pre-loaded close-price DataFrame (used by tests).
                When omitted, prices are downloaded via the fetcher.

        Returns:
            DataFrame indexed by date with columns:
                ``portfolio_value`` -- strategy equity curve,
                ``benchmark_value`` -- buy & hold of the benchmark,
                ``mode`` -- ``"risk_on"`` / ``"risk_off"`` at last rebalance.
        """
        cfg = self.config
        if closes is None:
            closes = self.load_prices()
        if closes.empty:
            raise StrategyError("No price data available for backtest.")

        dates = closes.index
        rebalance_days = set(_first_trading_days(dates))

        shares: dict[str, float] = {t: 0.0 for t in cfg.watchlist}
        cash = cfg.initial_capital
        peak = cfg.initial_capital
        mode = "cash"

        records: list[dict[str, object]] = []

        for current in dates:
            prices_today = closes.loc[current]
            equity = cash + sum(
                shares[t] * float(prices_today[t]) for t in cfg.watchlist
            )
            peak = max(peak, equity)

            if current in rebalance_days:
                cash, shares, mode = self._rebalance(
                    closes, current, equity, peak, cash, shares
                )
            elif mode != "cash" and check_drawdown(
                equity, peak, cfg.max_drawdown_threshold
            ):
                # Daily drawdown monitor: force de-risk to the safe asset
                # between rebalances (mirrors the live 15:55 ET check).
                cash, shares = self._liquidate_to_safe(prices_today, equity, cash, shares)
                mode = "risk_off"

            equity = cash + sum(
                shares[t] * float(prices_today[t]) for t in cfg.watchlist
            )
            records.append(
                {"date": current, "portfolio_value": equity, "mode": mode}
            )

        result = pd.DataFrame(records).set_index("date")
        result["benchmark_value"] = self._benchmark_curve(closes)
        # Drop the warm-up period before the strategy could first trade.
        first_trade = result[result["mode"] != "cash"].index.min()
        if pd.notna(first_trade):
            result = result[result.index >= first_trade]
        return result

    def _rebalance(
        self,
        closes: pd.DataFrame,
        current: pd.Timestamp,
        equity: float,
        peak: float,
        cash: float,
        shares: dict[str, float],
    ) -> tuple[float, dict[str, float], str]:
        """Compute target holdings on a rebalance date and apply trades.

        Returns:
            Tuple of (new cash, new shares dict, mode label).
        """
        cfg = self.config
        history = closes.loc[:current]
        prices_dict = {t: history[t].dropna() for t in cfg.watchlist}

        de_risk = check_drawdown(equity, peak, cfg.max_drawdown_threshold)
        if de_risk:
            signals = {cfg.safe_asset: 1.0}
            mode = "risk_off"
        else:
            try:
                signals = generate_signals(
                    prices_dict,
                    watchlist=cfg.watchlist,
                    benchmark=cfg.benchmark,
                    safe_asset=cfg.safe_asset,
                    lookback_months=cfg.lookback_months,
                    top_n=cfg.top_n,
                    require_positive_momentum=cfg.require_positive_momentum,
                )
            except StrategyError:
                # Not enough history yet -- stay in cash this period.
                return cash, shares, "cash"
            mode = "risk_off" if signals == {cfg.safe_asset: 1.0} else "risk_on"

        target_dollars = adjust_position_sizes(
            signals,
            equity,
            max_position_size=cfg.max_position_size,
            exempt_symbols=frozenset({cfg.safe_asset}),
        )

        prices_today = closes.loc[current]
        new_shares = {t: 0.0 for t in cfg.watchlist}
        for ticker, dollars in target_dollars.items():
            price = float(prices_today[ticker])
            new_shares[ticker] = dollars / price if price > 0 else 0.0

        # Transaction cost on the absolute change in position value.
        turnover = sum(
            abs(new_shares[t] - shares[t]) * float(prices_today[t])
            for t in cfg.watchlist
        )
        cost = turnover * cfg.transaction_cost
        invested = sum(target_dollars.values())
        new_cash = equity - invested - cost
        return new_cash, new_shares, mode

    def _liquidate_to_safe(
        self,
        prices_today: pd.Series,
        equity: float,
        cash: float,
        shares: dict[str, float],
    ) -> tuple[float, dict[str, float]]:
        """Move the entire portfolio into the safe asset (drawdown response).

        Args:
            prices_today: Series of current close prices by ticker.
            equity: Current portfolio equity.
            cash: Current cash balance.
            shares: Current share holdings by ticker.

        Returns:
            Tuple of (new cash, new shares dict).
        """
        cfg = self.config
        safe = cfg.safe_asset
        new_shares = {t: 0.0 for t in cfg.watchlist}
        safe_price = float(prices_today[safe])
        new_shares[safe] = equity / safe_price if safe_price > 0 else 0.0

        turnover = sum(
            abs(new_shares[t] - shares[t]) * float(prices_today[t])
            for t in cfg.watchlist
        )
        cost = turnover * cfg.transaction_cost
        new_cash = equity - new_shares[safe] * safe_price - cost
        return new_cash, new_shares

    def _benchmark_curve(self, closes: pd.DataFrame) -> pd.Series:
        """Buy-and-hold equity curve of the benchmark ticker.

        Args:
            closes: Aligned close-price DataFrame.

        Returns:
            Series of benchmark portfolio value, same index as ``closes``.
        """
        bench = closes[self.config.benchmark]
        shares = self.config.initial_capital / float(bench.iloc[0])
        return bench * shares
