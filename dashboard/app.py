"""FastAPI application for the mobile trading dashboard.

Serves a static mobile-first HTML page at ``/`` and JSON APIs under ``/api``.
CORS is open so the page can be hit directly from an iPhone on the LAN (or from
a Railway deployment).

Run locally::

    uvicorn dashboard.app:app --host 0.0.0.0 --port 8000
"""

from __future__ import annotations

from pathlib import Path

from dotenv import load_dotenv
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

# .env を読み込む（VAPID キーなど）。インポート順の都合でルーター読み込み前に行う。
load_dotenv()

from dashboard.routes import backtest, notifications, portfolio, strategy  # noqa: E402

_STATIC_DIR = Path(__file__).resolve().parent / "static"

app = FastAPI(title="増井FIREへの道 Dashboard", version="1.0.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["GET"],
    allow_headers=["*"],
)

app.include_router(portfolio.router)
app.include_router(portfolio.fx_router)
app.include_router(backtest.router)
app.include_router(strategy.router)
app.include_router(notifications.router)


@app.get("/health")
def health() -> dict[str, str]:
    """Liveness probe."""
    return {"status": "ok"}


@app.get("/")
def index() -> FileResponse:
    """Serve the dashboard single-page app."""
    return FileResponse(_STATIC_DIR / "index.html")


@app.get("/sw.js")
def service_worker() -> FileResponse:
    """Serve the Service Worker from root scope so it can control ``/``."""
    return FileResponse(
        _STATIC_DIR / "sw.js",
        media_type="application/javascript",
        headers={"Cache-Control": "no-cache", "Service-Worker-Allowed": "/"},
    )


@app.get("/manifest.json")
def manifest() -> FileResponse:
    """Serve the PWA manifest from root scope."""
    return FileResponse(_STATIC_DIR / "manifest.json", media_type="application/manifest+json")


# Expose static assets (charts, etc.) under /static.
app.mount("/static", StaticFiles(directory=_STATIC_DIR), name="static")
