"""Environment-backed application settings."""

from dataclasses import dataclass
import logging
import os

from sqlalchemy.engine import make_url


DEFAULT_DATABASE_CONNECT_TIMEOUT_SECONDS = 10
DEFAULT_PROVIDER_TIMEOUT_SECONDS = 30


@dataclass(frozen=True, slots=True)
class Settings:
    """Runtime settings loaded from environment variables.

    Values that may contain secrets are never written to logs by this module.
    """

    database_url: str | None
    llm_api_key: str | None
    log_level: str
    database_connect_timeout_seconds: int
    provider_timeout_seconds: int

    def require_database_url(self) -> str:
        """Return a validated database URL or raise a clear startup error."""
        if self.database_url is None:
            raise RuntimeError(
                "DATABASE_URL is required. Copy .env.example values into your "
                "shell or a private .env file, then run the command again."
            )
        try:
            make_url(self.database_url)
        except Exception as error:
            raise RuntimeError("DATABASE_URL is not a valid SQLAlchemy URL.") from error
        return self.database_url


def _positive_integer(name: str, default: int) -> int:
    raw = os.getenv(name, str(default))
    try:
        value = int(raw)
    except ValueError as error:
        raise RuntimeError(f"{name} must be a positive integer.") from error
    if value <= 0:
        raise RuntimeError(f"{name} must be a positive integer.")
    return value


def load_settings() -> Settings:
    """Load configuration from the current process environment."""
    log_level = os.getenv("LOG_LEVEL", "INFO").upper()
    if log_level not in logging.getLevelNamesMapping():
        raise RuntimeError(f"LOG_LEVEL is invalid: {log_level}.")
    return Settings(
        database_url=os.getenv("DATABASE_URL") or None,
        llm_api_key=os.getenv("LLM_API_KEY") or None,
        log_level=log_level,
        database_connect_timeout_seconds=_positive_integer(
            "DATABASE_CONNECT_TIMEOUT_SECONDS",
            DEFAULT_DATABASE_CONNECT_TIMEOUT_SECONDS,
        ),
        provider_timeout_seconds=_positive_integer(
            "PROVIDER_TIMEOUT_SECONDS", DEFAULT_PROVIDER_TIMEOUT_SECONDS
        ),
    )
