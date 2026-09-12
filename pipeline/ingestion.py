"""CSV ingestion for the analytics pipeline."""

import logging
from pathlib import Path
import hashlib

import pandas as pd

logger = logging.getLogger(__name__)


class DataIngestionError(RuntimeError):
    """Raised when a CSV cannot be safely loaded."""


def hash_file(path: str | Path) -> str:
    """Return the SHA-256 content hash used to identify an immutable source."""
    csv_path = Path(path).expanduser()
    if not csv_path.is_file():
        raise FileNotFoundError(f"CSV file was not found: {csv_path}")

    digest = hashlib.sha256()
    with csv_path.open("rb") as source_file:
        for chunk in iter(lambda: source_file.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def load_csv(path: str | Path) -> pd.DataFrame:
    """Load a CSV file into a DataFrame with explicit, actionable failures.

    Args:
        path: Path to the source CSV file.

    Raises:
        FileNotFoundError: If the provided path does not exist or is not a file.
        DataIngestionError: If pandas cannot parse the CSV.
    """
    csv_path = Path(path).expanduser()
    if not csv_path.is_file():
        raise FileNotFoundError(f"CSV file was not found: {csv_path}")

    logger.info("event=csv_load_started file_name=%s", csv_path.name)
    try:
        dataframe = pd.read_csv(csv_path)
    except (OSError, UnicodeDecodeError, pd.errors.ParserError) as error:
        logger.error(
            "event=csv_load_failed file_name=%s error_type=%s",
            csv_path.name,
            type(error).__name__,
        )
        raise DataIngestionError(f"Unable to load CSV '{csv_path}': {error}") from error

    logger.info(
        "event=csv_load_completed file_name=%s row_count=%d column_count=%d",
        csv_path.name,
        len(dataframe),
        len(dataframe.columns),
    )
    return dataframe
