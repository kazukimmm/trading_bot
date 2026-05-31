"""Risk-management decisions.

Pure decision helpers: drawdown checks, per-order validation, and conversion of
target weights into capped dollar amounts. No I/O, no broker calls.
"""

from __future__ import annotations

from core.exceptions import RiskValidationError

DEFAULT_MAX_DRAWDOWN = 0.15
DEFAULT_MAX_POSITION_SIZE = 0.40
DEFAULT_MAX_ORDER_RATIO = 0.95


def check_drawdown(
    current_value: float,
    peak_value: float,
    threshold: float = DEFAULT_MAX_DRAWDOWN,
) -> bool:
    """Decide whether the drawdown limit has been breached.

    Args:
        current_value: Current portfolio equity.
        peak_value: Highest equity observed so far (running peak).
        threshold: Peak-to-now drop fraction that triggers de-risking.

    Returns:
        True if positions should be force-closed (drawdown >= threshold).

    Raises:
        RiskValidationError: If inputs are non-positive/invalid.
    """
    if peak_value <= 0:
        raise RiskValidationError("peak_value must be positive.")
    if current_value < 0:
        raise RiskValidationError("current_value cannot be negative.")
    drawdown = 1.0 - (current_value / peak_value)
    return drawdown >= threshold


def validate_order(
    symbol: str,
    amount: float,
    account_balance: float,
    max_order_ratio: float = DEFAULT_MAX_ORDER_RATIO,
) -> bool:
    """Validate a single order against balance constraints.

    Args:
        symbol: Ticker being ordered (used in error messages).
        amount: Order notional in dollars (must be > 0).
        account_balance: Available cash/buying power in dollars.
        max_order_ratio: Max fraction of balance a single order may consume.

    Returns:
        True if the order passes all checks.

    Raises:
        RiskValidationError: If the order is invalid or exceeds limits.
    """
    if amount <= 0:
        raise RiskValidationError(f"{symbol}: order amount must be positive.")
    if account_balance <= 0:
        raise RiskValidationError(f"{symbol}: account balance is non-positive.")
    if amount > account_balance:
        raise RiskValidationError(
            f"{symbol}: order ${amount:.2f} exceeds balance ${account_balance:.2f}."
        )
    if amount > account_balance * max_order_ratio:
        raise RiskValidationError(
            f"{symbol}: order ${amount:.2f} exceeds "
            f"{max_order_ratio:.0%} of balance (${account_balance:.2f})."
        )
    return True


def adjust_position_sizes(
    signals: dict[str, float],
    account_balance: float,
    max_position_size: float = DEFAULT_MAX_POSITION_SIZE,
    exempt_symbols: frozenset[str] = frozenset(),
) -> dict[str, float]:
    """Convert target weights into capped dollar allocations.

    Each weight is capped at ``max_position_size`` of the balance to enforce the
    concentration limit. Capping reduces total deployed capital rather than
    redistributing it (conservative: prefer holding cash over concentration).
    Symbols in ``exempt_symbols`` (typically the defensive safe asset, which is
    meant to be held at 100% in risk-off mode) bypass the cap.

    Args:
        signals: Mapping of ticker -> target weight (should sum to ~1.0).
        account_balance: Total equity to allocate in dollars.
        max_position_size: Max fraction of equity per single symbol.
        exempt_symbols: Symbols exempt from the concentration cap.

    Returns:
        Mapping of ticker -> dollar amount to allocate.

    Raises:
        RiskValidationError: If inputs are invalid.
    """
    if account_balance <= 0:
        raise RiskValidationError("account_balance must be positive.")
    if not signals:
        return {}

    allocations: dict[str, float] = {}
    for ticker, weight in signals.items():
        if weight < 0:
            raise RiskValidationError(f"{ticker}: weight cannot be negative.")
        if ticker in exempt_symbols:
            capped_weight = weight
        else:
            capped_weight = min(weight, max_position_size)
        allocations[ticker] = round(account_balance * capped_weight, 2)
    return allocations
