"""Dual-momentum strategy signal generation.

This module is intentionally **pure**: functions take price data plus parameters
and return target weights. They perform no I/O and mutate no state, which keeps
the trading logic deterministic and trivially unit-testable.

Strategy (Antonacci dual momentum):
    1. Absolute momentum gate -- if the benchmark's lookback return is not
       positive, move fully to the safe asset (risk-off).
    2. Relative momentum -- otherwise hold the ``top_n`` risky ETFs with the
       highest lookback return, equally weighted (risk-on).
"""

from __future__ import annotations

import pandas as pd

from core.exceptions import StrategyError

# Defaults mirror config.settings; callers normally pass explicit values.
DEFAULT_WATCHLIST: tuple[str, ...] = ("SPY", "QQQ", "IWM", "EFA", "GLD", "TLT", "BIL")
DEFAULT_BENCHMARK = "SPY"
DEFAULT_SAFE_ASSET = "BIL"
DEFAULT_LOOKBACK_MONTHS = 12
DEFAULT_TOP_N = 3


def compute_lookback_return(series: pd.Series, lookback_months: int) -> float:
    """Compute the total return of a price series over a trailing window.

    The window is measured by calendar offset from the last observation, so the
    function works on either daily or monthly price series.

    Args:
        series: Price series indexed by a sorted ``DatetimeIndex``.
        lookback_months: Trailing window length in months.

    Returns:
        Simple return ``last / past - 1`` over the window.

    Raises:
        StrategyError: If the series is empty or lacks enough history.
    """
    clean = series.dropna()
    if clean.empty:
        raise StrategyError("Cannot compute return on an empty series.")
    if not isinstance(clean.index, pd.DatetimeIndex):
        raise StrategyError("Price series must have a DatetimeIndex.")

    last_date = clean.index[-1]
    past_date = last_date - pd.DateOffset(months=lookback_months)
    if clean.index[0] > past_date:
        raise StrategyError(
            f"Insufficient history: need {lookback_months}m before {last_date.date()}."
        )

    past_idx = clean.index.asof(past_date)
    if pd.isna(past_idx):
        raise StrategyError("No price at or before the lookback start date.")

    last_price = float(clean.iloc[-1])
    past_price = float(clean.loc[past_idx])
    if past_price <= 0:
        raise StrategyError("Non-positive historical price encountered.")
    return last_price / past_price - 1.0


def generate_signals(
    prices: dict[str, pd.Series],
    *,
    watchlist: tuple[str, ...] = DEFAULT_WATCHLIST,
    benchmark: str = DEFAULT_BENCHMARK,
    safe_asset: str = DEFAULT_SAFE_ASSET,
    lookback_months: int = DEFAULT_LOOKBACK_MONTHS,
    top_n: int = DEFAULT_TOP_N,
    require_positive_momentum: bool = False,
) -> dict[str, float]:
    """Generate target portfolio weights from price history.

    Args:
        prices: Mapping of ticker -> price series (DatetimeIndex, sorted).
        watchlist: Full ETF universe being evaluated.
        benchmark: Ticker used for the absolute-momentum gate.
        safe_asset: Ticker held when the absolute gate fails.
        lookback_months: Momentum lookback window in months.
        top_n: Number of risky ETFs to hold in risk-on mode.
        require_positive_momentum: When True, any selected ETF whose own
            momentum is not positive has its slice reallocated to ``safe_asset``
            ("don't hold losers"). Reduces downside at the cost of upside.

    Returns:
        Mapping of ticker -> target weight summing to ~1.0. Risk-off returns
        ``{safe_asset: 1.0}``.

    Raises:
        StrategyError: If required price series are missing or invalid.
    """
    if benchmark not in prices:
        raise StrategyError(f"Benchmark '{benchmark}' missing from price data.")
    if safe_asset not in watchlist:
        raise StrategyError(f"Safe asset '{safe_asset}' must be in the watchlist.")

    # 1. Absolute momentum gate.
    benchmark_return = compute_lookback_return(prices[benchmark], lookback_months)
    if benchmark_return <= 0:
        return {safe_asset: 1.0}

    # 2. Relative momentum among the risky universe (exclude the safe asset).
    risky_universe = [t for t in watchlist if t != safe_asset]
    momentum: dict[str, float] = {}
    for ticker in risky_universe:
        if ticker not in prices:
            raise StrategyError(f"Missing price data for '{ticker}'.")
        momentum[ticker] = compute_lookback_return(prices[ticker], lookback_months)

    ranked = sorted(momentum.items(), key=lambda kv: kv[1], reverse=True)
    selected = [ticker for ticker, _ in ranked[:top_n]]
    if not selected:
        return {safe_asset: 1.0}

    weight = round(1.0 / len(selected), 4)
    weights: dict[str, float] = {}
    safe_fill = 0.0
    for ticker in selected:
        if require_positive_momentum and momentum[ticker] <= 0:
            safe_fill += weight
        else:
            weights[ticker] = weights.get(ticker, 0.0) + weight
    if safe_fill > 0:
        weights[safe_asset] = weights.get(safe_asset, 0.0) + round(safe_fill, 4)
    return weights
