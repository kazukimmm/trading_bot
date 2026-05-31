"""Logging configuration.

Provides a single :func:`get_logger` entry point that configures both a rotating
daily file handler (``logs/YYYY-MM-DD.log``, DEBUG and above) and a console
handler (INFO and above). Handlers are attached once per process.
"""

from __future__ import annotations

import logging
import sys
from datetime import date
from pathlib import Path

_LOG_FORMAT = "[%(asctime)s] [%(levelname)s] [%(name)s] %(message)s"
_DATE_FORMAT = "%Y-%m-%d %H:%M:%S"
_LOG_DIR = Path(__file__).resolve().parent.parent / "logs"

_configured = False


def _configure_root(level: str) -> None:
    """Attach file and console handlers to the root logger (idempotent).

    Args:
        level: Console/file root level name (e.g. ``"INFO"``).
    """
    global _configured
    if _configured:
        return

    _LOG_DIR.mkdir(parents=True, exist_ok=True)
    root = logging.getLogger()
    root.setLevel(logging.DEBUG)

    formatter = logging.Formatter(_LOG_FORMAT, datefmt=_DATE_FORMAT)

    file_handler = logging.FileHandler(
        _LOG_DIR / f"{date.today().isoformat()}.log", encoding="utf-8"
    )
    file_handler.setLevel(logging.DEBUG)
    file_handler.setFormatter(formatter)

    console_handler = logging.StreamHandler(stream=sys.stdout)
    console_level = getattr(logging, level.upper(), logging.INFO)
    console_handler.setLevel(console_level)
    console_handler.setFormatter(formatter)

    root.addHandler(file_handler)
    root.addHandler(console_handler)
    _configured = True


def get_logger(name: str, level: str = "INFO") -> logging.Logger:
    """Return a module-scoped logger with handlers configured.

    Args:
        name: Logger name, conventionally ``__name__`` of the caller.
        level: Console log level applied on first configuration.

    Returns:
        A ready-to-use :class:`logging.Logger`.
    """
    _configure_root(level)
    return logging.getLogger(name)
