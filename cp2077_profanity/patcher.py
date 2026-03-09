"""Apply profanity replacements to localization JSON files."""

import csv
import json
from dataclasses import dataclass
from pathlib import Path

import regex

from .scanner import build_pattern, load_wordlist, normalize_elongation


@dataclass
class PatchRecord:
    """Record of a single replacement applied."""

    filepath: str
    string_key: str
    field: str
    original: str
    replacement: str
    words_replaced: list[str]


def patch_value(value: str, pattern: regex.Pattern) -> tuple[str, list[str]]:
    """Apply asterisk replacement to all profanity matches in a string value.

    Normalizes elongation before matching (e.g. "fuuuuuck" → detected as "fuck"),
    then replaces the full original span with asterisks of equal character length.

    Returns the patched string and a list of normalized words that were replaced.
    """
    normalized, span_starts, span_ends = normalize_elongation(value)
    words_found = [m.group() for m in pattern.finditer(normalized)]
    if not words_found:
        return value, []

    result = list(value)
    for match in pattern.finditer(normalized):
        orig_start = span_starts[match.start()]
        orig_end = span_ends[match.end() - 1]
        for k in range(orig_start, orig_end):
            result[k] = "*"

    return "".join(result), words_found


def patch_json_file(
    filepath: Path, pattern: regex.Pattern
) -> list[PatchRecord]:
    """Patch a single locale JSON file in-place, replacing profanity with asterisks.

    Handles the WolvenKit CR2W export format:
      { "$type": "...", "entries": [ { "femaleVariant": "...", "maleVariant": "...", ... } ] }

    Only modifies "femaleVariant" and "maleVariant" string fields.
    Returns a list of patch records for the audit log.
    """
    with open(filepath, "r", encoding="utf-8-sig") as f:
        raw = f.read()

    data = json.loads(raw)
    records: list[PatchRecord] = []
    modified = False

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

        entry_key = entry.get("secondaryKey", entry.get("$type", "unknown"))

        for field_name in ("femaleVariant", "maleVariant"):
            value = entry.get(field_name)
            if not isinstance(value, str) or not value:
                continue

            patched, words = patch_value(value, pattern)
            if words:
                records.append(
                    PatchRecord(
                        filepath=str(filepath),
                        string_key=str(entry_key),
                        field=field_name,
                        original=value,
                        replacement=patched,
                        words_replaced=words,
                    )
                )
                entry[field_name] = patched
                modified = True

    if modified:
        with open(filepath, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)

    return records


def write_patch_log(records: list[PatchRecord], output_path: Path) -> None:
    """Write patch records to a CSV audit log."""
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with open(output_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(["filepath", "string_key", "field", "original", "replacement"])
        for rec in records:
            writer.writerow([rec.filepath, rec.string_key, rec.field, rec.original, rec.replacement])


def patch_all(
    json_files: list[Path], wordlist_path: Path, log_path: Path
) -> list[PatchRecord]:
    """Patch all locale JSON files and write the audit log.

    Returns all patch records.
    """
    words = load_wordlist(wordlist_path)
    if not words:
        print("  Warning: wordlist is empty, nothing to patch.")
        return []

    pattern = build_pattern(words)
    all_records: list[PatchRecord] = []

    for filepath in json_files:
        try:
            records = patch_json_file(filepath, pattern)
            all_records.extend(records)
            if records:
                print(f"  Patched {len(records)} string(s) in {filepath.name}")
        except (json.JSONDecodeError, UnicodeDecodeError) as e:
            print(f"  Warning: skipping {filepath.name}: {e}")

    write_patch_log(all_records, log_path)
    return all_records
