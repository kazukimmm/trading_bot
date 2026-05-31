"""Unit tests for OrderExecutor (Alpaca client fully mocked)."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest

from core.exceptions import OrderExecutionError
from core.order_executor import OrderExecutor, validate_api_keys


class _FakeOrder:
    """Minimal stand-in for an Alpaca order object."""

    def __init__(self) -> None:
        self.id = "order-123"
        self.status = SimpleNamespace(value="filled")
        self.filled_avg_price = "528.42"
        self.filled_qty = "0.01892"


class _FakePosition:
    def __init__(self, symbol: str, qty: float, market_value: float, pl: float) -> None:
        self.symbol = symbol
        self.qty = qty
        self.market_value = market_value
        self.unrealized_pl = pl


class _FakeClient:
    """Records calls and returns canned responses (no network)."""

    def __init__(self) -> None:
        self.submitted: list[Any] = []
        self.closed = False
        self._account = SimpleNamespace(
            equity="1000.50", buying_power="2000.00", cash="500.25"
        )
        self._positions: list[Any] = []

    def submit_order(self, order_data: Any) -> _FakeOrder:
        self.submitted.append(order_data)
        return _FakeOrder()

    def get_account(self) -> Any:
        return self._account

    def get_all_positions(self) -> list[Any]:
        return self._positions

    def get_clock(self) -> Any:
        return SimpleNamespace(is_open=True)

    def close_all_positions(self, cancel_orders: bool = True) -> None:
        self.closed = True


@pytest.fixture
def executor() -> OrderExecutor:
    """An OrderExecutor whose underlying Alpaca client is faked."""
    ex = OrderExecutor("real_key", "real_secret", paper=True, retry_attempts=1)
    ex.client = _FakeClient()
    return ex


# --- validate_api_keys -------------------------------------------------


def test_validate_api_keys_rejects_empty() -> None:
    with pytest.raises(ValueError):
        validate_api_keys("", "secret")


def test_validate_api_keys_rejects_placeholder() -> None:
    with pytest.raises(ValueError):
        validate_api_keys("【ここにAPI Keyを貼り付け】", "secret")


def test_validate_api_keys_rejects_example_placeholder() -> None:
    with pytest.raises(ValueError):
        validate_api_keys("your_api_key_here", "your_secret_key_here")


def test_validate_api_keys_accepts_real_values() -> None:
    validate_api_keys("PKABCDEF123", "abcdefgEXAMPLEsecret")


# --- __init__ paper inference -----------------------------------------


def test_base_url_infers_paper() -> None:
    ex = OrderExecutor("k", "s", base_url="https://paper-api.alpaca.markets")
    assert ex.is_paper is True


def test_base_url_infers_live() -> None:
    ex = OrderExecutor("k", "s", base_url="https://api.alpaca.markets")
    assert ex.is_paper is False


# --- submit_order ------------------------------------------------------


def test_submit_order_returns_dict(executor: OrderExecutor) -> None:
    result = executor.submit_order("SPY", 10.0, side="buy")
    assert result["id"] == "order-123"
    assert result["symbol"] == "SPY"
    assert result["side"] == "buy"
    assert result["notional"] == 10.0
    assert result["status"] == "filled"
    assert result["filled_avg_price"] == pytest.approx(528.42)
    assert result["filled_qty"] == pytest.approx(0.01892)


def test_submit_order_rejects_nonpositive(executor: OrderExecutor) -> None:
    with pytest.raises(OrderExecutionError):
        executor.submit_order("SPY", 0.0, side="buy")


def test_submit_order_wraps_client_error(executor: OrderExecutor) -> None:
    def _boom(order_data: Any) -> Any:
        raise RuntimeError("rejected")

    executor.client.submit_order = _boom  # type: ignore[assignment]
    with pytest.raises(OrderExecutionError):
        executor.submit_order("SPY", 10.0, side="buy")


# --- account / positions ----------------------------------------------


def test_get_account_info(executor: OrderExecutor) -> None:
    info = executor.get_account_info()
    assert info["is_paper"] is True
    assert info["equity"] == pytest.approx(1000.50)
    assert info["buying_power"] == pytest.approx(2000.00)
    assert info["cash"] == pytest.approx(500.25)
    assert info["position_count"] == 0


def test_get_current_positions(executor: OrderExecutor) -> None:
    executor.client._positions = [_FakePosition("SPY", 0.5, 264.0, 3.5)]  # type: ignore[attr-defined]
    positions = executor.get_current_positions()
    assert positions["SPY"]["qty"] == pytest.approx(0.5)
    assert positions["SPY"]["market_value"] == pytest.approx(264.0)


def test_is_market_open(executor: OrderExecutor) -> None:
    assert executor.is_market_open() is True


def test_close_all_positions(executor: OrderExecutor) -> None:
    executor.close_all_positions()
    assert executor.client.closed is True  # type: ignore[attr-defined]
