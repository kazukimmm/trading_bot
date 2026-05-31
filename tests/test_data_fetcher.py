"""Unit tests for DataFetcher using a stubbed yfinance.download."""

from __future__ import annotations

import pandas as pd
import pytest

import core.data_fetcher as data_fetcher_module
from core.data_fetcher import DataFetcher
from core.exceptions import DataFetchError


def _fake_frame() -> pd.DataFrame:
    idx = pd.date_range("2023-01-02", periods=5, freq="B")
    return pd.DataFrame({"Close": [100.0, 101.0, 102.0, 103.0, 104.0]}, index=idx)


def test_get_historical_prices_returns_frame(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        data_fetcher_module.yf, "download", lambda *a, **k: _fake_frame()
    )
    fetcher = DataFetcher(retry_attempts=1)
    df = fetcher.get_historical_prices("SPY", period="1y")
    assert not df.empty
    assert "Close" in df.columns


def test_cache_avoids_second_download(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = {"n": 0}

    def _counted(*_a: object, **_k: object) -> pd.DataFrame:
        calls["n"] += 1
        return _fake_frame()

    monkeypatch.setattr(data_fetcher_module.yf, "download", _counted)
    fetcher = DataFetcher(retry_attempts=1)
    fetcher.get_historical_prices("SPY", period="1y")
    fetcher.get_historical_prices("SPY", period="1y")
    assert calls["n"] == 1  # second call served from cache


def test_empty_download_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        data_fetcher_module.yf, "download", lambda *a, **k: pd.DataFrame()
    )
    fetcher = DataFetcher(retry_attempts=1)
    with pytest.raises(DataFetchError):
        fetcher.get_historical_prices("ZZZZ", period="1y")


def test_get_current_price(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        data_fetcher_module.yf, "download", lambda *a, **k: _fake_frame()
    )
    fetcher = DataFetcher(retry_attempts=1)
    assert fetcher.get_current_price("SPY") == pytest.approx(104.0)


def test_market_open_requires_client() -> None:
    fetcher = DataFetcher(retry_attempts=1)
    with pytest.raises(DataFetchError):
        fetcher.is_market_open()
