"""Portfolio API routes."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter

from dashboard import data_provider

router = APIRouter(prefix="/api/portfolio", tags=["portfolio"])

# 為替（USD/JPY）専用ルーター。パスは /api/fx/usdjpy。
fx_router = APIRouter(prefix="/api/fx", tags=["fx"])


@fx_router.get("/usdjpy")
def get_usdjpy() -> dict[str, Any]:
    """現在のドル円レートを返す（{"rate": 149.5, "updated_at": "..."}）。"""
    return data_provider.usdjpy_rate()


@router.get("/summary")
def get_summary() -> dict[str, Any]:
    """Return high-level portfolio metrics for the dashboard header/cards."""
    return data_provider.portfolio_summary()


@router.get("/holdings")
def get_holdings() -> list[dict[str, Any]]:
    """Return the current holdings list."""
    return data_provider.portfolio_holdings()


@router.get("/history")
def get_history() -> list[dict[str, Any]]:
    """Return the daily equity history (date/value points)."""
    return data_provider.portfolio_history()


@router.get("/decisions")
def get_decisions() -> list[dict[str, Any]]:
    """Return the most recent trading decisions with their outcomes."""
    return data_provider.recent_decisions(limit=10)
