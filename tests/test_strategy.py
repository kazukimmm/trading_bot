"""Unit tests for the dual-momentum strategy (pure logic)."""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from core.exceptions import StrategyError
from core.strategy import compute_lookback_return, generate_signals

WATCHLIST = ("SPY", "QQQ", "IWM", "EFA", "GLD", "TLT", "BIL")


def _series(values: list[float], end: str = "2023-12-29") -> pd.Series:
    """Build a daily-indexed price series ending at ``end``."""
    idx = pd.date_range(end=end, periods=len(values), freq="B")
    return pd.Series(values, index=idx)


def _rising(start: float, growth: float, days: int = 400) -> pd.Series:
    """A monotonically rising series with a constant daily growth factor."""
    values = [start * (growth**i) for i in range(days)]
    return _series(values)


def test_compute_lookback_return_positive() -> None:
    s = _series([100.0] * 400 + [110.0])  # ~18 months flat, last jumps to 110
    # 12 months before the last point the price is 100 -> ~10% return.
    assert compute_lookback_return(s, 12) == pytest.approx(0.10, abs=0.01)


def test_compute_lookback_return_insufficient_history() -> None:
    s = _series([100.0, 101.0, 102.0])
    with pytest.raises(StrategyError):
        compute_lookback_return(s, 12)


def test_compute_lookback_return_empty() -> None:
    with pytest.raises(StrategyError):
        compute_lookback_return(pd.Series([], dtype=float), 12)


def test_risk_off_when_benchmark_negative() -> None:
    # SPY falling: benchmark return negative -> all to safe asset.
    falling = _rising(200.0, 0.999)  # decaying
    prices = {t: _rising(100.0, 1.001) for t in WATCHLIST}
    prices["SPY"] = falling
    signals = generate_signals(prices, watchlist=WATCHLIST)
    assert signals == {"BIL": 1.0}


def test_risk_on_selects_top_n_equal_weight() -> None:
    # Distinct growth rates so ranking is deterministic.
    growth = {
        "SPY": 1.0010,
        "QQQ": 1.0020,  # best
        "IWM": 1.0015,  # 2nd
        "EFA": 1.0012,  # 3rd
        "GLD": 1.0005,
        "TLT": 1.0003,
        "BIL": 1.0001,
    }
    prices = {t: _rising(100.0, g) for t, g in growth.items()}
    signals = generate_signals(prices, watchlist=WATCHLIST, top_n=3)
    assert set(signals) == {"QQQ", "IWM", "EFA"}
    for weight in signals.values():
        assert weight == pytest.approx(1 / 3, abs=1e-3)
    assert sum(signals.values()) == pytest.approx(1.0, abs=1e-2)


def test_safe_asset_excluded_from_relative_ranking() -> None:
    # Even if BIL had high momentum, the risk-on bucket ignores it.
    growth = {t: 1.0010 for t in WATCHLIST}
    growth["BIL"] = 1.05  # absurdly high, must still be excluded
    prices = {t: _rising(100.0, g) for t, g in growth.items()}
    signals = generate_signals(prices, watchlist=WATCHLIST, top_n=3)
    assert "BIL" not in signals


def test_missing_benchmark_raises() -> None:
    prices = {"QQQ": _rising(100.0, 1.001)}
    with pytest.raises(StrategyError):
        generate_signals(prices, watchlist=WATCHLIST)
