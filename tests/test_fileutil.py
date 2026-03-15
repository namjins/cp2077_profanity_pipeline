"""Tests for fileutil.py: atomic_write context manager."""

import pytest
from pathlib import Path

from cp2077_profanity.fileutil import atomic_write


class TestAtomicWrite:
    def test_writes_content(self, tmp_path):
        filepath = tmp_path / "test.txt"
        with atomic_write(filepath) as f:
            f.write("hello world")
        assert filepath.read_text() == "hello world"

    def test_no_partial_write_on_error(self, tmp_path):
        filepath = tmp_path / "test.txt"
        filepath.write_text("original content")

        with pytest.raises(RuntimeError):
            with atomic_write(filepath) as f:
                f.write("partial")
                raise RuntimeError("simulated crash")

        # Original content should be preserved
        assert filepath.read_text() == "original content"

    def test_no_temp_file_left_on_error(self, tmp_path):
        filepath = tmp_path / "test.txt"
        with pytest.raises(RuntimeError):
            with atomic_write(filepath) as f:
                f.write("data")
                raise RuntimeError("crash")

        # No .tmp files should remain
        tmp_files = list(tmp_path.glob("*.tmp"))
        assert tmp_files == []

    def test_creates_parent_dirs(self, tmp_path):
        filepath = tmp_path / "sub" / "dir" / "file.txt"
        with atomic_write(filepath) as f:
            f.write("nested")
        assert filepath.read_text() == "nested"

    def test_overwrites_existing(self, tmp_path):
        filepath = tmp_path / "test.txt"
        filepath.write_text("old")
        with atomic_write(filepath) as f:
            f.write("new")
        assert filepath.read_text() == "new"
