"""Retry helpers for AWS API calls."""

from __future__ import annotations

import logging
import time
from collections.abc import Callable
from typing import TypeVar

from botocore.exceptions import BotoCoreError, ClientError

T = TypeVar("T")

RETRYABLE_ERROR_CODES = frozenset(
    {
        "RequestLimitExceeded",
        "Throttling",
        "ThrottlingException",
        "TooManyRequestsException",
        "ServiceUnavailable",
        "InternalError",
        "InternalServerError",
        "ProvisionedThroughputExceededException",
    }
)

logger = logging.getLogger("ami_migration")


def is_retryable(exc: BaseException) -> bool:
    if isinstance(exc, ClientError):
        code = exc.response.get("Error", {}).get("Code", "")
        return code in RETRYABLE_ERROR_CODES
    return isinstance(exc, BotoCoreError)


def with_retry(
    func: Callable[[], T],
    *,
    max_attempts: int = 5,
    base_delay: float = 2.0,
    max_delay: float = 60.0,
    operation: str = "AWS API call",
) -> T:
    attempt = 0
    while True:
        attempt += 1
        try:
            return func()
        except Exception as exc:
            if attempt >= max_attempts or not is_retryable(exc):
                raise
            delay = min(max_delay, base_delay * (2 ** (attempt - 1)))
            logger.warning(
                "%s failed (attempt %s/%s): %s — retrying in %.1fs",
                operation,
                attempt,
                max_attempts,
                exc,
                delay,
            )
            time.sleep(delay)
