"""Unit tests for the risk manager (pure logic)."""

from __future__ import annotations

import pytest

from core.exceptions import RiskValidationError
from core.risk_manager import (
    adjust_position_sizes,
    check_drawdown,
    validate_order,
)


def test_check_drawdown_triggers_at_threshold() -> None:
    # 15% drop from peak with default 0.15 threshold -> True.
    assert check_drawdown(85.0, 100.0) is True


def test_check_drawdown_below_threshold() -> None:
    assert check_drawdown(90.0, 100.0) is False


def test_check_drawdown_invalid_peak() -> None:
    with pytest.raises(RiskValidationError):
        check_drawdown(50.0, 0.0)


def test_validate_order_ok() -> None:
    assert validate_order("SPY", 90.0, 100.0) is True


def test_validate_order_exceeds_ratio() -> None:
    # 96 > 95% of 100 -> rejected.
    with pytest.raises(RiskValidationError):
        validate_order("SPY", 96.0, 100.0)


def test_validate_order_exceeds_balance() -> None:
    with pytest.raises(RiskValidationError):
        validate_order("SPY", 150.0, 100.0)


def test_validate_order_non_positive_amount() -> None:
    with pytest.raises(RiskValidationError):
        validate_order("SPY", 0.0, 100.0)


def test_adjust_position_sizes_caps_concentration() -> None:
    # Single 100% signal must be capped at 40% of balance.
    allocations = adjust_position_sizes({"QQQ": 1.0}, 1000.0)
    assert allocations == {"QQQ": 400.0}


def test_adjust_position_sizes_equal_weights() -> None:
    signals = {"QQQ": 1 / 3, "IWM": 1 / 3, "EFA": 1 / 3}
    allocations = adjust_position_sizes(signals, 900.0)
    for amount in allocations.values():
        assert amount == pytest.approx(300.0, abs=0.01)


def test_adjust_position_sizes_empty() -> None:
    assert adjust_position_sizes({}, 1000.0) == {}


def test_adjust_position_sizes_invalid_balance() -> None:
    with pytest.raises(RiskValidationError):
        adjust_position_sizes({"QQQ": 1.0}, 0.0)
