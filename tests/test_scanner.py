"""Tests for scanner.py: elongation normalization, pattern building, and scanning."""

import pytest

from cp2077_profanity.scanner import (
    build_pattern,
    normalize_elongation,
    _extract_entries,
)


# -- normalize_elongation -------------------------------------------------

class TestNormalizeElongation:
    def test_empty_string(self):
        text, starts, ends = normalize_elongation("")
        assert text == ""
        assert starts == []
        assert ends == []

    def test_no_elongation(self):
        text, starts, ends = normalize_elongation("hello")
        assert text == "hello"

    def test_double_letters_preserved(self):
        """Double letters (aa, oo, ll) are legitimate and should NOT be collapsed."""
        text, _, _ = normalize_elongation("good")
        assert text == "good"

        text, _, _ = normalize_elongation("all")
        assert text == "all"

        text, _, _ = normalize_elongation("class")
        assert text == "class"

    def test_triple_collapses_to_one(self):
        """Runs of 3+ identical chars collapse to 1 char."""
        text, _, _ = normalize_elongation("fuuuck")
        assert text == "fuck"

    def test_long_elongation(self):
        text, _, _ = normalize_elongation("fuuuuuuuuck")
        assert text == "fuck"

    def test_multiple_elongated_segments(self):
        text, _, _ = normalize_elongation("shiiiiit")
        assert text == "shit"

    def test_span_mapping_preserves_original_length(self):
        """The span mapping must map back to the full original positions."""
        original = "fuuuuuck"
        text, span_starts, span_ends = normalize_elongation(original)
        assert text == "fuck"
        # The 'u' in normalized text maps to original positions 1..5
        # span_starts[1] should be 1 (start of the 'u' run)
        # span_ends[1] should be 6 (end of the 'u' run, exclusive)
        assert span_starts[1] == 1
        assert span_ends[1] == 6

    def test_mixed_elongation_and_normal(self):
        text, _, _ = normalize_elongation("gooood morning")
        assert text == "god morning"

    def test_single_char(self):
        text, _, _ = normalize_elongation("a")
        assert text == "a"


# -- build_pattern --------------------------------------------------------

class TestBuildPattern:
    def test_empty_wordlist_raises(self):
        with pytest.raises(ValueError, match="empty"):
            build_pattern([])

    def test_single_word(self):
        pattern = build_pattern(["fuck"])
        assert pattern.search("what the fuck")
        assert not pattern.search("what the heck")

    def test_word_boundaries(self):
        """Whole-word matching: 'ass' should NOT match inside 'class' or 'assassin'."""
        pattern = build_pattern(["ass"])
        assert pattern.search("you ass")
        assert not pattern.search("classic")
        assert not pattern.search("assassin")

    def test_case_insensitive(self):
        pattern = build_pattern(["damn"])
        assert pattern.search("DAMN it")
        assert pattern.search("Damn it")
        assert pattern.search("damn it")

    def test_longest_match_first(self):
        """Longer phrases should match before shorter substrings."""
        pattern = build_pattern(["ass", "asshole"])
        match = pattern.search("you asshole")
        assert match.group().lower() == "asshole"

    def test_multiple_words(self):
        pattern = build_pattern(["fuck", "shit", "damn"])
        text = "fuck this shit"
        matches = [m.group().lower() for m in pattern.finditer(text)]
        assert "fuck" in matches
        assert "shit" in matches


# -- _extract_entries -----------------------------------------------------

class TestExtractEntries:
    def test_wolvenkit_cr2w_format(self):
        """Standard WolvenKit cr2w -s JSON structure."""
        data = {
            "Data": {
                "RootChunk": {
                    "root": {
                        "Data": {
                            "entries": [{"femaleVariant": "hello"}]
                        }
                    }
                }
            }
        }
        entries = _extract_entries(data)
        assert len(entries) == 1
        assert entries[0]["femaleVariant"] == "hello"

    def test_flat_entries_fallback(self):
        data = {"entries": [{"femaleVariant": "test"}]}
        entries = _extract_entries(data)
        assert len(entries) == 1

    def test_list_input(self):
        data = [{"femaleVariant": "test"}]
        entries = _extract_entries(data)
        assert len(entries) == 1

    def test_no_entries(self):
        data = {"something": "else"}
        entries = _extract_entries(data)
        assert entries == []
