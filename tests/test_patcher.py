"""Tests for patcher.py: patch replacement, record creation, and patch log I/O."""

import json
import pytest
from pathlib import Path

from cp2077_profanity.scanner import build_pattern
from cp2077_profanity.patcher import patch_value, patch_json_file, write_patch_log, load_patch_log, PatchRecord


# -- patch_value ----------------------------------------------------------

class TestPatchValue:
    def setup_method(self):
        self.pattern = build_pattern(["fuck", "shit", "damn"])

    def test_simple_replacement(self):
        patched, words = patch_value("what the fuck", self.pattern)
        assert patched == "what the ****"
        assert "fuck" in words

    def test_asterisk_count_matches_original_length(self):
        patched, words = patch_value("shit happens", self.pattern)
        assert patched == "**** happens"
        assert len("****") == len("shit")

    def test_elongated_replacement_preserves_length(self):
        """Elongated words get asterisks matching the ORIGINAL length, not normalized."""
        patched, words = patch_value("fuuuuuck you", self.pattern)
        # "fuuuuuck" = f(1) u(2) u(3) u(4) u(5) u(6) c(7) k(8) = 8 chars → 8 asterisks
        assert patched == "******** you"
        assert len("********") == len("fuuuuuck") == 8

    def test_multiple_matches(self):
        patched, words = patch_value("fuck this shit", self.pattern)
        assert patched == "**** this ****"

    def test_no_match(self):
        patched, words = patch_value("hello world", self.pattern)
        assert patched == "hello world"
        assert words == []

    def test_case_insensitive(self):
        patched, words = patch_value("FUCK this", self.pattern)
        assert patched == "**** this"

    def test_preserves_surrounding_text(self):
        patched, _ = patch_value("well damn, that's rough", self.pattern)
        assert patched == "well ****, that's rough"


# -- patch_json_file ------------------------------------------------------

class TestPatchJsonFile:
    def test_patches_femalevariant(self, tmp_path):
        data = {
            "Data": {"RootChunk": {"root": {"Data": {"entries": [
                {"secondaryKey": "test_key", "stringId": 12345,
                 "femaleVariant": "what the fuck", "maleVariant": ""}
            ]}}}}
        }
        filepath = tmp_path / "test.json.json"
        filepath.write_text(json.dumps(data), encoding="utf-8")

        pattern = build_pattern(["fuck"])
        records = patch_json_file(filepath, pattern)

        assert len(records) == 1
        assert records[0].field == "femaleVariant"
        assert records[0].string_id == "12345"
        assert "****" in records[0].replacement

        # Verify file was modified
        patched_data = json.loads(filepath.read_text(encoding="utf-8"))
        entry = patched_data["Data"]["RootChunk"]["root"]["Data"]["entries"][0]
        assert "****" in entry["femaleVariant"]

    def test_no_match_no_modification(self, tmp_path):
        data = {
            "Data": {"RootChunk": {"root": {"Data": {"entries": [
                {"secondaryKey": "clean", "stringId": 99,
                 "femaleVariant": "hello there", "maleVariant": ""}
            ]}}}}
        }
        filepath = tmp_path / "clean.json.json"
        filepath.write_text(json.dumps(data), encoding="utf-8")

        pattern = build_pattern(["fuck"])
        records = patch_json_file(filepath, pattern)

        assert len(records) == 0


# -- patch log I/O -------------------------------------------------------

class TestPatchLog:
    def test_round_trip(self, tmp_path):
        records = [
            PatchRecord(
                filepath="test.json.json",
                string_key="key1",
                string_id="12345",
                field="femaleVariant",
                original="what the fuck",
                replacement="what the ****",
                words_replaced=["fuck"],
            ),
            PatchRecord(
                filepath="test2.json.json",
                string_key="key2",
                string_id=None,
                field="maleVariant",
                original="damn it",
                replacement="**** it",
                words_replaced=["damn"],
            ),
        ]
        log_path = tmp_path / "patch_log.csv"
        write_patch_log(records, log_path)
        loaded = load_patch_log(log_path)

        assert len(loaded) == 2
        assert loaded[0].string_id == "12345"
        assert loaded[0].field == "femaleVariant"
        assert loaded[1].string_id is None  # None preserved through CSV round-trip

    def test_load_missing_file_raises(self, tmp_path):
        with pytest.raises(FileNotFoundError):
            load_patch_log(tmp_path / "nonexistent.csv")
