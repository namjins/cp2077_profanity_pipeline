"""Safe file I/O utilities for the profanity filter pipeline."""

import os
import tempfile
from contextlib import contextmanager
from pathlib import Path


@contextmanager
def atomic_write(filepath: Path, mode: str = "w", **open_kwargs):
    """Context manager for atomic file writes.

    Writes to a temporary file in the same directory, then atomically replaces
    the target file on successful close.  If an exception occurs inside the
    ``with`` block the temp file is removed and the original is untouched.

    Usage::

        with atomic_write(path, encoding="utf-8") as f:
            json.dump(data, f)
    """
    filepath = Path(filepath)
    filepath.parent.mkdir(parents=True, exist_ok=True)

    tmp_fd, tmp_path = tempfile.mkstemp(
        dir=filepath.parent, suffix=".tmp", prefix=f".{filepath.name}."
    )
    try:
        with os.fdopen(tmp_fd, mode, **open_kwargs) as f:
            yield f
        os.replace(tmp_path, filepath)
    except BaseException:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        raise
