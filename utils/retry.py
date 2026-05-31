"""Retry helper with exponential backoff."""

from __future__ import annotations

import time
from collections.abc import Callable
from typing import TypeVar

from utils.logger import get_logger

T = TypeVar("T")
_logger = get_logger(__name__)


def retry_with_backoff(
    func: Callable[[], T],
    *,
    attempts: int = 3,
    backoff_base: float = 2.0,
    exceptions: tuple[type[Exception], ...] = (Exception,),
    description: str = "operation",
) -> T:
    """Call ``func`` retrying on failure with exponential backoff.

    Args:
        func: Zero-argument callable to execute.
        attempts: Maximum number of attempts (>= 1).
        backoff_base: Base for the exponential delay ``base ** (attempt-1)`` seconds.
        exceptions: Exception types that trigger a retry.
        description: Human-readable label used in log messages.

    Returns:
        Whatever ``func`` returns on its first successful call.

    Raises:
        Exception: Re-raises the last caught exception if all attempts fail.
    """
    last_exc: Exception | None = None
    for attempt in range(1, attempts + 1):
        try:
            return func()
        except exceptions as exc:  # noqa: BLE001 - intentional broad retry
            last_exc = exc
            if attempt == attempts:
                break
            delay = backoff_base ** (attempt - 1)
            _logger.warning(
                "%s failed (attempt %d/%d): %s. Retrying in %.1fs.",
                description,
                attempt,
                attempts,
                exc,
                delay,
            )
            time.sleep(delay)
    assert last_exc is not None
    _logger.error("%s failed after %d attempts: %s", description, attempts, last_exc)
    raise last_exc
