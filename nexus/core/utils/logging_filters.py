"""Reusable logging filters and setup helpers."""

from __future__ import annotations

import logging
from collections.abc import Iterable
from typing import Any


class SecretRedactingFilter(logging.Filter):
    """Redact sensitive values from log messages and args."""

    def __init__(self, secrets: Iterable[str]):
        super().__init__()
        self._secrets = [str(secret) for secret in secrets if str(secret)]

    def _redact(self, value: Any) -> Any:
        if isinstance(value, str):
            redacted = value
            for secret in self._secrets:
                redacted = redacted.replace(secret, "[REDACTED_SECRET]")
            return redacted
        if isinstance(value, tuple):
            return tuple(self._redact(item) for item in value)
        if isinstance(value, list):
            return [self._redact(item) for item in value]
        if isinstance(value, dict):
            return {key: self._redact(item) for key, item in value.items()}
        return value

    def filter(self, record: logging.LogRecord) -> bool:
        record.msg = self._redact(record.msg)
        record.args = self._redact(record.args)
        return True


def install_secret_redaction(secrets: Iterable[str], target_logger: logging.Logger | None = None) -> None:
    """Attach secret redaction filter to all handlers of target logger."""
    logger = target_logger or logging.getLogger()
    redaction_filter = SecretRedactingFilter(secrets)
    for handler in logger.handlers:
        handler.addFilter(redaction_filter)
