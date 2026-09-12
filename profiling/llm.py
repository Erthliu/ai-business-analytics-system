"""Optional, provider-neutral LLM boundary for profile enrichment."""

from typing import Protocol


class LLMProvider(Protocol):
    """Provider that returns validated supplemental finding strings.

    Implementations must enforce the configured provider timeout before this
    synchronous method returns or raises. The application never assumes an
    unbounded network wait is acceptable.
    """

    name: str
    model_name: str

    def generate_structured(self, metadata: dict[str, object]) -> object:
        """Generate typed data that the calling boundary must validate."""
