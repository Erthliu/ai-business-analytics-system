"""Centralized, context-friendly and secret-safe application logging."""

import logging
import re


_SECRET_PATTERNS = (
    re.compile(r"(?i)(postgres(?:ql)?(?:\+\w+)?://[^:\s]+:)([^@\s]+)(@)"),
    re.compile(r"(?i)((?:api[_-]?key|token|password)\s*[=:]\s*)([^\s,;]+)"),
)


class SecretRedactionFilter(logging.Filter):
    """Redact common credential shapes before a record reaches a handler."""

    def filter(self, record: logging.LogRecord) -> bool:
        message = record.getMessage()
        for pattern in _SECRET_PATTERNS:
            message = pattern.sub(r"\1[REDACTED]\3" if pattern.groups == 3 else r"\1[REDACTED]", message)
        record.msg = message
        record.args = ()
        return True


def configure_logging(level: str = "INFO") -> None:
    """Configure the root logger with a consistent console format.

    Configuration is idempotent so callers can safely invoke it more than once.
    """
    resolved_level = logging.getLevelNamesMapping().get(level.upper())
    if resolved_level is None:
        raise ValueError(f"Unsupported log level: {level}.")
    logging.basicConfig(
        level=resolved_level,
        format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
        force=True,
    )
    for handler in logging.getLogger().handlers:
        handler.addFilter(SecretRedactionFilter())
