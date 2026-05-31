"""Strategy advisor API routes (AI改善提案)."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter

from dashboard import data_provider

router = APIRouter(prefix="/api/strategy", tags=["strategy"])


@router.get("/advice")
def get_advice() -> dict[str, Any]:
    """Return the latest improvement advice (``has_advice: false`` if none)."""
    return data_provider.strategy_advice()


@router.post("/advice/dismiss")
def dismiss_advice() -> dict[str, Any]:
    """Mark the current advice as acknowledged so it stops showing."""
    return data_provider.dismiss_strategy_advice()
