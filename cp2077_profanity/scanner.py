"""Scan localization JSON files for profane words using whole-word regex matching."""

import json
from dataclasses import dataclass
from pathlib import Path

import regex


@dataclass
class ScanHit:
    """A single profanity match found in a locale file."""

    filepath: Path
    string_key: str
    field: str  # "femaleVariant" or "maleVariant"
    matched_word: str
    original_value: str


def load_wordlist(wordlist_path: Path) -> list[str]:
    """Load profanity words from a wordlist file.

    Skips blank lines and lines starting with #.
    """
    if not wordlist_path.exists():
        raise FileNotFoundError(f"Wordlist not found: {wordlist_path}")

    words = []
    with open(wordlist_path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line and not line.startswith("#"):
                words.append(line)
    return words


def normalize_elongation(text: str) -> tuple[str, list[int], list[int]]:
    """Collapse runs of 3+ identical characters down to a single character.

    Runs of 1-2 identical characters are left untouched, preserving legitimate
    double letters (e.g. "good" has "oo" = 2 chars, stays as-is).
    Runs of 3+ are elongation (e.g. "gooood" → "god", "fuuuuuck" → "fuck").

    Returns:
        normalized   — the collapsed string
        span_starts  — span_starts[i] is the original index where normalized char i begins
        span_ends    — span_ends[i] is the original index (exclusive) where the run ends
    """
    if not text:
        return text, [], []

    normalized: list[str] = []
    span_starts: list[int] = []
    span_ends: list[int] = []

    i = 0
    while i < len(text):
        char = text[i]
        run_start = i
        while i < len(text) and text[i] == char:
            i += 1
        run_len = i - run_start

        if run_len >= 3:
            # Elongation: collapse entire run to one character
            normalized.append(char)
            span_starts.append(run_start)
            span_ends.append(i)
        else:
            # Legitimate 1- or 2-char sequence: keep as-is
            for j in range(run_len):
                normalized.append(char)
                span_starts.append(run_start + j)
                span_ends.append(run_start + j + 1)

    return "".join(normalized), span_starts, span_ends


def build_pattern(words: list[str]) -> regex.Pattern:
    """Build a compiled regex pattern for whole-word matching of all profanity words.

    Uses word boundaries to avoid matching substrings (e.g., "ass" in "classic").
    Elongation is handled by normalizing the input text before matching, not by
    modifying the pattern — so patterns remain exact and false positives are avoided.
    Case-insensitive.
    """
    # Sort by length descending so longer phrases match first
    sorted_words = sorted(words, key=len, reverse=True)
    escaped = [regex.escape(w) for w in sorted_words]
    pattern_str = r"\b(?:" + "|".join(escaped) + r")\b"
    return regex.compile(pattern_str, regex.IGNORECASE)


def scan_json_file(
    filepath: Path, pattern: regex.Pattern
) -> list[ScanHit]:
    """Scan a single locale JSON file for profanity matches.

    Handles the WolvenKit CR2W export format:
      { "$type": "...", "entries": [ { "femaleVariant": "...", "maleVariant": "...", ... } ] }

    Only inspects "femaleVariant" and "maleVariant" string fields.
    Input is normalized before matching to catch elongated words (e.g. "fuuuuuck").
    """
    with open(filepath, "r", encoding="utf-8-sig") as f:
        data = json.load(f)

    hits = []

    # CR2W export: root object with an "entries" list
    if isinstance(data, dict) and "entries" in data:
        entries = data["entries"]
    elif isinstance(data, list):
        entries = data
    else:
        entries = [data]

    for entry in entries:
        if not isinstance(entry, dict):
            continue

        # Use secondaryKey as the human-readable identifier, fall back to $type
        entry_key = entry.get("secondaryKey", entry.get("$type", "unknown"))

        for field_name in ("femaleVariant", "maleVariant"):
            value = entry.get(field_name)
            if not isinstance(value, str) or not value:
                continue

            normalized, _, _ = normalize_elongation(value)
            for match in pattern.finditer(normalized):
                hits.append(
                    ScanHit(
                        filepath=filepath,
                        string_key=str(entry_key),
                        field=field_name,
                        matched_word=match.group(),
                        original_value=value,
                    )
                )

    return hits


def scan_all(json_files: list[Path], wordlist_path: Path) -> list[ScanHit]:
    """Scan all locale JSON files for profanity. Returns all hits found."""
    words = load_wordlist(wordlist_path)
    if not words:
        print("  Warning: wordlist is empty, nothing to scan.")
        return []

    pattern = build_pattern(words)
    all_hits: list[ScanHit] = []

    for filepath in json_files:
        try:
            hits = scan_json_file(filepath, pattern)
            all_hits.extend(hits)
        except (json.JSONDecodeError, UnicodeDecodeError) as e:
            print(f"  Warning: skipping {filepath.name}: {e}")

    return all_hits
