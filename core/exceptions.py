"""Custom exception hierarchy for the trading bot.

All application-specific errors derive from :class:`TradingBotError` so callers
can catch the whole family with a single ``except`` clause when desired.
"""

from __future__ import annotations


class TradingBotError(Exception):
    """Base class for every error raised by this application."""


class ConfigError(TradingBotError):
    """Raised when configuration is missing or invalid."""


class DataFetchError(TradingBotError):
    """Raised when market/price data cannot be retrieved."""


class StrategyError(TradingBotError):
    """Raised when the strategy cannot produce valid signals."""


class RiskValidationError(TradingBotError):
    """Raised when an order fails a risk-management check."""


class OrderExecutionError(TradingBotError):
    """Raised when an order cannot be submitted to the broker."""


class NotificationError(TradingBotError):
    """Raised when a notification fails to send."""
