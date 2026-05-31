"""Backtest API routes."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter

from dashboard import data_provider

router = APIRouter(prefix="/api/backtest", tags=["backtest"])


@router.get("/results")
def get_results() -> dict[str, Any]:
    """Return the cached backtest summary metrics."""
    return data_provider.backtest_results()
