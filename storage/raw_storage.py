"""Content-addressed raw-source retention; object stores can implement this protocol."""

from __future__ import annotations

from pathlib import Path
from shutil import copyfile
from typing import Protocol
from uuid import uuid4


class RawStorage(Protocol):
    """Immutable storage boundary for original source files."""

    def retain(self, source_path: Path, content_hash: str) -> str:
        """Store source bytes once and return a durable URI."""


class FileSystemRawStorage:
    """Local development implementation using content-addressed files."""

    def __init__(self, root: Path) -> None:
        self._root = root

    def retain(self, source_path: Path, content_hash: str) -> str:
        """Copy a source to an immutable hash-addressed destination."""
        self._root.mkdir(parents=True, exist_ok=True)
        destination = self._root / f"{content_hash}{source_path.suffix.lower()}"
        if not destination.exists():
            temporary = destination.with_suffix(destination.suffix + f".{uuid4().hex}.tmp")
            copyfile(source_path, temporary)
            temporary.replace(destination)
        return destination.resolve().as_uri()
