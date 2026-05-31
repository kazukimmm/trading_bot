"""Centralized, typed configuration for the trading bot.

All tunable values live here. Secrets and environment-specific values are read
from environment variables (loaded from a ``.env`` file via ``python-dotenv``);
strategy/risk parameters are defined as typed constants so the rest of the code
base never hard-codes magic numbers.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from functools import lru_cache

from dotenv import load_dotenv

from core.exceptions import ConfigError

# Load .env once at import time. Real environment variables take precedence.
load_dotenv()


def _get_env(key: str, default: str | None = None, *, required: bool = False) -> str:
    """Read an environment variable.

    Args:
        key: Environment variable name.
        default: Value returned when the variable is unset.
        required: When True, raise if the variable is unset/empty.

    Returns:
        The environment variable value (or the default).

    Raises:
        ConfigError: If ``required`` is True and the value is missing.
    """
    value = os.getenv(key, default)
    if required and (value is None or value.strip() == ""):
        raise ConfigError(f"Required environment variable '{key}' is not set.")
    return value if value is not None else ""


@dataclass(frozen=True)
class Settings:
    """Immutable, fully-typed application configuration.

    Attributes:
        alpaca_api_key: Alpaca API key id.
        alpaca_secret_key: Alpaca API secret.
        alpaca_base_url: REST endpoint (paper vs live).
        is_paper: True when trading against the paper endpoint.
        slack_webhook_url: Incoming-webhook URL for notifications (may be empty).
        environment: ``"paper"`` or ``"live"``.
        log_level: Root log level name (e.g. ``"INFO"``).
        watchlist: ETFs evaluated for relative momentum.
        safe_asset: Ticker used in the defensive ("risk-off") mode.
        benchmark: Ticker used for the absolute-momentum gate and B&H compare.
        momentum_lookback_months: Lookback window for momentum (months).
        rebalance_day: Calendar nth-business-day of month to rebalance.
        top_n_holdings: Number of top relative-momentum ETFs to hold.
        require_positive_momentum: Reallocate any selected ETF with non-positive
            momentum to the safe asset (downside-protection filter).
        max_drawdown_threshold: Peak-to-now drop that triggers a full de-risk.
        max_position_size: Max fraction of equity allowed in one symbol.
        max_order_balance_ratio: Max fraction of cash a single order may consume.
        transaction_cost: Per-trade cost fraction applied in backtests.
        initial_capital: Starting capital used by the backtest.
        backtest_start: ISO date the backtest begins.
        retry_attempts: Number of API retries on failure.
        retry_backoff_base: Base seconds for exponential backoff.
        request_timeout: Network timeout (seconds) for data requests.
    """

    # --- Secrets / environment (from .env) ---
    alpaca_api_key: str
    alpaca_secret_key: str
    alpaca_base_url: str
    is_paper: bool
    slack_webhook_url: str
    environment: str
    log_level: str

    # --- Strategy universe ---
    watchlist: tuple[str, ...] = ("SPY", "QQQ", "IWM", "EFA", "GLD", "TLT", "BIL")
    safe_asset: str = "BIL"
    benchmark: str = "SPY"

    # --- Strategy parameters (tuned for "hard to lose" via backtest sweep) ---
    momentum_lookback_months: int = 9
    rebalance_day: int = 1
    top_n_holdings: int = 3
    require_positive_momentum: bool = True

    # --- Risk parameters ---
    max_drawdown_threshold: float = 0.15
    max_position_size: float = 0.40
    max_order_balance_ratio: float = 0.95

    # --- Backtest parameters ---
    transaction_cost: float = 0.001
    initial_capital: float = 1000.0
    backtest_start: str = "2010-01-01"

    # --- 元本（最初に投資したドル金額。ダッシュボードの円建て表示に使用） ---
    initial_investment: float = 1000.0

    # --- 円建て積立シミュレーション（毎月いくらずつ積み立てるか） ---
    monthly_contribution_jpy: float = 100000.0
    investment_start_date: str = ""  # YYYY-MM-DD。空なら今月から。

    # --- Networking / resilience ---
    retry_attempts: int = 3
    retry_backoff_base: float = 2.0
    request_timeout: int = 30

    def validate(self) -> None:
        """Validate cross-field invariants.

        Raises:
            ConfigError: If any parameter is out of its sane range.
        """
        if self.environment not in {"paper", "live"}:
            raise ConfigError(
                f"ENVIRONMENT must be 'paper' or 'live', got '{self.environment}'."
            )
        if not 0.0 < self.max_drawdown_threshold < 1.0:
            raise ConfigError("MAX_DRAWDOWN_THRESHOLD must be between 0 and 1.")
        if not 0.0 < self.max_position_size <= 1.0:
            raise ConfigError("MAX_POSITION_SIZE must be in (0, 1].")
        if not 0.0 < self.max_order_balance_ratio <= 1.0:
            raise ConfigError("MAX_ORDER_BALANCE_RATIO must be in (0, 1].")
        if self.top_n_holdings < 1:
            raise ConfigError("TOP_N_HOLDINGS must be >= 1.")
        if self.top_n_holdings > len(self.watchlist):
            raise ConfigError("TOP_N_HOLDINGS cannot exceed the watchlist size.")
        if self.momentum_lookback_months < 1:
            raise ConfigError("MOMENTUM_LOOKBACK_MONTHS must be >= 1.")
        if self.safe_asset not in self.watchlist:
            raise ConfigError("SAFE_ASSET must be present in the watchlist.")
        if self.environment == "live" and "paper" in self.alpaca_base_url:
            raise ConfigError(
                "ENVIRONMENT is 'live' but ALPACA_BASE_URL points at paper."
            )


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Build (and cache) the :class:`Settings` singleton from the environment.

    Returns:
        A validated, immutable :class:`Settings` instance.

    Raises:
        ConfigError: If required secrets are missing or values are invalid.
    """
    environment = _get_env("ENVIRONMENT", "paper").strip().lower()
    base_url = _get_env(
        "ALPACA_BASE_URL", "https://paper-api.alpaca.markets"
    ).strip()

    settings = Settings(
        alpaca_api_key=_get_env("ALPACA_API_KEY", required=True),
        alpaca_secret_key=_get_env("ALPACA_SECRET_KEY", required=True),
        alpaca_base_url=base_url,
        is_paper=("paper" in base_url) or environment == "paper",
        slack_webhook_url=_get_env("SLACK_WEBHOOK_URL", ""),
        environment=environment,
        log_level=_get_env("LOG_LEVEL", "INFO").strip().upper(),
        initial_investment=float(_get_env("INITIAL_INVESTMENT", "1000") or "1000"),
        monthly_contribution_jpy=float(
            _get_env("MONTHLY_CONTRIBUTION_JPY", "100000") or "100000"
        ),
        investment_start_date=_get_env("INVESTMENT_START_DATE", "").strip(),
    )
    settings.validate()
    return settings
