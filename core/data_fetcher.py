"""Market-data access layer.

Wraps ``yfinance`` for historical/spot prices (with a simple same-day cache and
retry) and the Alpaca trading client for live account/market-state queries. The
Alpaca client is imported lazily so backtests run without broker credentials.
"""

from __future__ import annotations

from datetime import date
from typing import Any

import pandas as pd
import yfinance as yf

from core.exceptions import DataFetchError
from utils.logger import get_logger
from utils.retry import retry_with_backoff

_logger = get_logger(__name__)


class DataFetcher:
    """Fetches price and account data with caching and retries.

    Attributes:
        retry_attempts: Number of attempts for network calls.
        retry_backoff_base: Base seconds for exponential backoff.
        timeout: Network timeout in seconds.
    """

    def __init__(
        self,
        retry_attempts: int = 3,
        retry_backoff_base: float = 2.0,
        timeout: int = 30,
        trading_client: Any | None = None,
    ) -> None:
        """Initialize the fetcher.

        Args:
            retry_attempts: Max attempts for each network operation.
            retry_backoff_base: Base for exponential backoff delay.
            timeout: Per-request timeout in seconds.
            trading_client: Optional pre-built Alpaca ``TradingClient`` used for
                account/clock queries. When omitted, account methods raise.
        """
        self.retry_attempts = retry_attempts
        self.retry_backoff_base = retry_backoff_base
        self.timeout = timeout
        self._trading_client = trading_client
        # Cache keyed by (ticker, period, today) -> DataFrame
        self._price_cache: dict[tuple[str, str, str], pd.DataFrame] = {}

    def get_historical_prices(self, ticker: str, period: str = "5y") -> pd.DataFrame:
        """Return daily OHLCV history for a single ticker.

        Args:
            ticker: Symbol, e.g. ``"SPY"``.
            period: yfinance period string (e.g. ``"1y"``, ``"5y"``, ``"max"``).

        Returns:
            A DataFrame indexed by date with at least a ``Close`` column.

        Raises:
            DataFetchError: If no data is returned after retries.
        """
        cache_key = (ticker, period, date.today().isoformat())
        if cache_key in self._price_cache:
            return self._price_cache[cache_key]

        def _download() -> pd.DataFrame:
            df = yf.download(
                ticker,
                period=period,
                interval="1d",
                auto_adjust=True,
                progress=False,
                timeout=self.timeout,
            )
            if df is None or df.empty:
                raise DataFetchError(f"No price data returned for '{ticker}'.")
            # yfinance may return a column MultiIndex for a single ticker.
            if isinstance(df.columns, pd.MultiIndex):
                df.columns = df.columns.get_level_values(0)
            return df

        df = retry_with_backoff(
            _download,
            attempts=self.retry_attempts,
            backoff_base=self.retry_backoff_base,
            exceptions=(Exception,),
            description=f"get_historical_prices({ticker})",
        )
        self._price_cache[cache_key] = df
        return df

    def get_historical_closes(
        self, tickers: list[str], period: str = "5y"
    ) -> pd.DataFrame:
        """Return adjusted close prices for several tickers as one DataFrame.

        Args:
            tickers: List of symbols.
            period: yfinance period string.

        Returns:
            A DataFrame indexed by date, one column per ticker (``Close``).

        Raises:
            DataFetchError: If no usable data is returned.
        """
        closes: dict[str, pd.Series] = {}
        for ticker in tickers:
            df = self.get_historical_prices(ticker, period=period)
            closes[ticker] = df["Close"]
        result = pd.DataFrame(closes).dropna(how="all")
        if result.empty:
            raise DataFetchError("No overlapping close data across tickers.")
        return result

    def get_current_price(self, ticker: str) -> float:
        """Return the most recent close price for a ticker.

        Args:
            ticker: Symbol to price.

        Returns:
            Latest available close price as a float.

        Raises:
            DataFetchError: If a price cannot be determined.
        """
        df = self.get_historical_prices(ticker, period="5d")
        close = df["Close"].dropna()
        if close.empty:
            raise DataFetchError(f"No recent close for '{ticker}'.")
        return float(close.iloc[-1])

    def is_market_open(self) -> bool:
        """Return whether the US equity market is currently open.

        Returns:
            True if Alpaca reports the market open, else False.

        Raises:
            DataFetchError: If no trading client was provided.
        """
        if self._trading_client is None:
            raise DataFetchError("Trading client not configured for is_market_open().")

        def _clock() -> bool:
            clock = self._trading_client.get_clock()
            return bool(clock.is_open)

        return retry_with_backoff(
            _clock,
            attempts=self.retry_attempts,
            backoff_base=self.retry_backoff_base,
            description="is_market_open",
        )

    def get_portfolio_value(self) -> float:
        """Return current total account equity from Alpaca.

        Returns:
            Account equity (cash + positions) as a float.

        Raises:
            DataFetchError: If no trading client was provided.
        """
        if self._trading_client is None:
            raise DataFetchError(
                "Trading client not configured for get_portfolio_value()."
            )

        def _equity() -> float:
            account = self._trading_client.get_account()
            return float(account.equity)

        return retry_with_backoff(
            _equity,
            attempts=self.retry_attempts,
            backoff_base=self.retry_backoff_base,
            description="get_portfolio_value",
        )
