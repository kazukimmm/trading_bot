"""Alpaca order execution layer.

Thin wrapper around the official ``alpaca-py`` ``TradingClient``. Responsible
only for broker communication (submitting notional market orders, reading
positions, flattening) -- strategy and risk decisions live elsewhere.
"""

from __future__ import annotations

from typing import Any

from alpaca.trading.client import TradingClient
from alpaca.trading.enums import OrderSide, TimeInForce
from alpaca.trading.requests import MarketOrderRequest

from core.exceptions import OrderExecutionError
from utils.logger import get_logger
from utils.retry import retry_with_backoff

_logger = get_logger(__name__)

# Fragments that indicate an un-edited placeholder value left in .env.
_PLACEHOLDER_MARKERS = ("ここに入力", "ここに", "your_api_key", "your_secret", "【", "】")


def validate_api_keys(api_key: str, secret_key: str) -> None:
    """Ensure the Alpaca keys are present and not left as placeholders.

    Args:
        api_key: Alpaca API key id.
        secret_key: Alpaca API secret.

    Raises:
        ValueError: If a key is empty or still contains a placeholder marker.
    """
    for label, value in (("ALPACA_API_KEY", api_key), ("ALPACA_SECRET_KEY", secret_key)):
        if value is None or value.strip() == "":
            raise ValueError(
                "APIキーが設定されていません。.envファイルを確認してください。"
                f"（{label} が空です）"
            )
        if any(marker in value for marker in _PLACEHOLDER_MARKERS):
            raise ValueError(
                "APIキーが未入力（プレースホルダのまま）です。"
                f".envファイルの {label} を実際のキーに置き換えてください。"
            )


class OrderExecutor:
    """Executes orders and queries positions via the Alpaca trading API."""

    def __init__(
        self,
        api_key: str,
        secret_key: str,
        base_url: str | None = None,
        paper: bool = True,
        retry_attempts: int = 3,
        retry_backoff_base: float = 2.0,
    ) -> None:
        """Initialize the executor and its underlying trading client.

        Args:
            api_key: Alpaca API key id.
            secret_key: Alpaca API secret.
            base_url: Optional Alpaca REST URL. When given, paper/live is
                inferred from it (overrides ``paper``).
            paper: Whether to use the paper-trading endpoint (used if
                ``base_url`` is not supplied).
            retry_attempts: Max attempts for each API call.
            retry_backoff_base: Base seconds for exponential backoff.

        Raises:
            ValueError: If the API keys are missing or still placeholders.
        """
        validate_api_keys(api_key, secret_key)
        if base_url is not None:
            paper = "paper" in base_url
        self.is_paper = paper
        self.client = TradingClient(api_key, secret_key, paper=paper)
        self.retry_attempts = retry_attempts
        self.retry_backoff_base = retry_backoff_base

    @property
    def trading_client(self) -> TradingClient:
        """The underlying Alpaca ``TradingClient`` (for clock/account reads)."""
        return self.client

    def get_account_info(self) -> dict[str, Any]:
        """Return a summary of the account.

        Returns:
            Dict with ``is_paper``, ``equity``, ``buying_power``, ``cash`` and
            ``position_count``.

        Raises:
            OrderExecutionError: If the account cannot be read.
        """

        def _info() -> dict[str, Any]:
            account = self.client.get_account()
            positions = self.client.get_all_positions()
            return {
                "is_paper": self.is_paper,
                "equity": float(account.equity),
                "buying_power": float(account.buying_power),
                "cash": float(account.cash),
                "position_count": len(positions),
            }

        try:
            return retry_with_backoff(
                _info,
                attempts=self.retry_attempts,
                backoff_base=self.retry_backoff_base,
                description="get_account_info",
            )
        except Exception as exc:  # noqa: BLE001
            raise OrderExecutionError(f"Failed to read account info: {exc}") from exc

    def get_portfolio_value(self) -> float:
        """Return the current total portfolio value (account equity).

        Returns:
            Equity as a float.
        """
        return self.get_account_equity()

    def is_market_open(self) -> bool:
        """Return whether the US equity market is currently open.

        Returns:
            True if Alpaca reports the market open.

        Raises:
            OrderExecutionError: If the clock cannot be read.
        """

        def _clock() -> bool:
            return bool(self.client.get_clock().is_open)

        try:
            return retry_with_backoff(
                _clock,
                attempts=self.retry_attempts,
                backoff_base=self.retry_backoff_base,
                description="is_market_open",
            )
        except Exception as exc:  # noqa: BLE001
            raise OrderExecutionError(f"Failed to read market clock: {exc}") from exc

    def submit_order(self, symbol: str, notional: float, side: str = "buy") -> dict[str, Any]:
        """Submit a notional (dollar-amount) market order.

        Notional orders support fractional shares, which suits small accounts.

        Args:
            symbol: Ticker to trade.
            notional: Dollar amount to buy/sell (> 0).
            side: ``"buy"`` or ``"sell"``.

        Returns:
            Dict summarizing the submitted order: ``id``, ``symbol``, ``side``,
            ``notional``, ``status``, ``filled_avg_price`` and ``filled_qty``.

        Raises:
            OrderExecutionError: If the order is invalid or the API rejects it.
        """
        if notional <= 0:
            raise OrderExecutionError(f"{symbol}: notional must be positive.")
        order_side = OrderSide.BUY if side.lower() == "buy" else OrderSide.SELL
        request = MarketOrderRequest(
            symbol=symbol,
            notional=round(notional, 2),
            side=order_side,
            time_in_force=TimeInForce.DAY,
        )

        def _submit() -> Any:
            return self.client.submit_order(order_data=request)

        try:
            order = retry_with_backoff(
                _submit,
                attempts=self.retry_attempts,
                backoff_base=self.retry_backoff_base,
                description=f"submit_order({symbol}, {side}, ${notional:.2f})",
            )
        except Exception as exc:  # noqa: BLE001 - wrap into domain error
            raise OrderExecutionError(
                f"Failed to submit {side} order for {symbol}: {exc}"
            ) from exc
        _logger.info("Submitted %s $%.2f of %s", side, notional, symbol)
        return self._order_to_dict(order, symbol=symbol, side=side, notional=notional)

    @staticmethod
    def _order_to_dict(
        order: Any, *, symbol: str, side: str, notional: float
    ) -> dict[str, Any]:
        """Convert an Alpaca order object into a plain summary dict.

        Args:
            order: The Alpaca order object returned by the API.
            symbol: Ticker the order was placed for.
            side: ``"buy"`` or ``"sell"``.
            notional: Dollar amount requested.

        Returns:
            Dict with ``id``, ``symbol``, ``side``, ``notional``, ``status``,
            ``filled_avg_price`` and ``filled_qty``. Missing/None fields are
            normalized (status -> ``"unknown"``, numeric -> ``None``).
        """

        def _num(value: Any) -> float | None:
            if value is None:
                return None
            try:
                return float(value)
            except (TypeError, ValueError):
                return None

        status = getattr(order, "status", None)
        return {
            "id": str(getattr(order, "id", "")),
            "symbol": symbol,
            "side": side,
            "notional": round(notional, 2),
            "status": str(status.value) if hasattr(status, "value") else str(status or "unknown"),
            "filled_avg_price": _num(getattr(order, "filled_avg_price", None)),
            "filled_qty": _num(getattr(order, "filled_qty", None)),
        }

    def get_current_positions(self) -> dict[str, dict[str, float]]:
        """Return current open positions keyed by symbol.

        Returns:
            Mapping of symbol -> ``{"qty": float, "market_value": float,
            "unrealized_pl": float}``.

        Raises:
            OrderExecutionError: If positions cannot be retrieved.
        """

        def _positions() -> list[Any]:
            return self.client.get_all_positions()

        try:
            positions = retry_with_backoff(
                _positions,
                attempts=self.retry_attempts,
                backoff_base=self.retry_backoff_base,
                description="get_all_positions",
            )
        except Exception as exc:  # noqa: BLE001
            raise OrderExecutionError(f"Failed to read positions: {exc}") from exc

        return {
            p.symbol: {
                "qty": float(p.qty),
                "market_value": float(p.market_value),
                "unrealized_pl": float(p.unrealized_pl),
            }
            for p in positions
        }

    def close_all_positions(self) -> None:
        """Liquidate every open position (used by the drawdown rule).

        Raises:
            OrderExecutionError: If the flatten request fails.
        """

        def _close() -> None:
            self.client.close_all_positions(cancel_orders=True)

        try:
            retry_with_backoff(
                _close,
                attempts=self.retry_attempts,
                backoff_base=self.retry_backoff_base,
                description="close_all_positions",
            )
        except Exception as exc:  # noqa: BLE001
            raise OrderExecutionError(f"Failed to close positions: {exc}") from exc
        _logger.warning("Closed all open positions.")

    def get_account_equity(self) -> float:
        """Return total account equity (cash + positions).

        Returns:
            Equity as a float.

        Raises:
            OrderExecutionError: If the account cannot be read.
        """

        def _equity() -> float:
            return float(self.client.get_account().equity)

        try:
            return retry_with_backoff(
                _equity,
                attempts=self.retry_attempts,
                backoff_base=self.retry_backoff_base,
                description="get_account_equity",
            )
        except Exception as exc:  # noqa: BLE001
            raise OrderExecutionError(f"Failed to read account: {exc}") from exc
