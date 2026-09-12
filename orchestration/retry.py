"""Bounded, explicit retry classification for workflow failures."""
from dataclasses import dataclass

from sqlalchemy.exc import OperationalError


@dataclass(frozen=True, slots=True)
class RetryPolicy:
    """Retry only known infrastructure failures and never exceed the bound."""

    max_attempts: int = 1

    @staticmethod
    def is_retryable(error: Exception) -> bool:
        return isinstance(error, (OperationalError, OSError, TimeoutError))

    def should_retry(self, error: Exception, attempt: int) -> bool:
        return self.is_retryable(error) and attempt < self.max_attempts
