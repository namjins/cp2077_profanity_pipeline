"""Apply profanity replacements to localization JSON files."""

import csv
import json
from dataclasses import dataclass
from pathlib import Path

import regex

from .fileutil import atomic_write
from .scanner import _extract_entries, build_pattern, load_wordlist, normalize_elongation


@dataclass
class PatchRecord:
    """Record of a single replacement applied."""

    filepath: str
    string_key: str
    string_id: str | None  # numeric stringId linking to voice audio files
    field: str
    original: str
    replacement: str
    words_replaced: list[str]


def patch_value(value: str, pattern: regex.Pattern) -> tuple[str, list[str]]:
    """Apply asterisk replacement to all profanity matches in a string value.

    Normalizes elongation before matching (e.g. "fuuuuuck" -> detected as "fuck"),
    then replaces the full original span with asterisks of equal character length
    (e.g. "fuuuuuck" -> "*********").

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

    Handles the WolvenKit CR2W export format produced by 'cr2w -s':
      { "Header": {...}, "Data": { "RootChunk": { "root": { "Data": { "entries": [...] } } } } }

    Only modifies "femaleVariant" and "maleVariant" string fields.
    Returns a list of patch records for the audit log.
    """
    with open(filepath, "r", encoding="utf-8-sig") as f:
        raw = f.read()

    data = json.loads(raw)
    records: list[PatchRecord] = []
    modified = False

    entries = _extract_entries(data)

    for entry in entries:
        if not isinstance(entry, dict):
            continue

        entry_key = entry.get("secondaryKey", entry.get("$type", "unknown"))
        string_id = entry.get("stringId")

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
                        string_id=str(string_id) if string_id is not None else None,
                        field=field_name,
                        original=value,
                        replacement=patched,
                        words_replaced=words,
                    )
                )
                entry[field_name] = patched
                modified = True

    if modified:
        # Verify the patched data is still valid JSON before writing
        serialized = json.dumps(data, ensure_ascii=False, indent=2)
        json.loads(serialized)  # sanity check: round-trip parse
        with atomic_write(filepath, encoding="utf-8") as f:
            f.write(serialized)

    return records


def load_patch_log(log_path: Path) -> list[PatchRecord]:
    """Load patch records from an existing CSV audit log.

    Used to resume a pipeline run without re-scanning already-patched files.
    words_replaced is left empty since it is not needed for the audio pipeline.

    Raises ValueError if the CSV is malformed (missing required columns).
    """
    if not log_path.exists():
        raise FileNotFoundError(f"Patch log not found: {log_path}")

    required_columns = {"filepath", "string_key", "string_id", "field", "original", "replacement"}

    records: list[PatchRecord] = []
    with open(log_path, newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)

        # Validate columns before iterating
        if reader.fieldnames is None:
            raise ValueError(f"Patch log is empty: {log_path}")
        missing = required_columns - set(reader.fieldnames)
        if missing:
            raise ValueError(f"Patch log missing columns: {missing}")

        for line_num, row in enumerate(reader, start=2):
            try:
                records.append(
                    PatchRecord(
                        filepath=row["filepath"],
                        string_key=row["string_key"],
                        string_id=row["string_id"] or None,
                        field=row["field"],
                        original=row["original"],
                        replacement=row["replacement"],
                        words_replaced=[],
                    )
                )
            except KeyError as e:
                print(f"  Warning: skipping malformed row {line_num} in patch log: {e}")

    return records


def write_patch_log(records: list[PatchRecord], output_path: Path) -> None:
    """Write patch records to a CSV audit log."""
    with atomic_write(output_path, newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(["filepath", "string_key", "string_id", "field", "original", "replacement"])
        for rec in records:
            writer.writerow([rec.filepath, rec.string_key, rec.string_id or "", rec.field, rec.original, rec.replacement])


def patch_all(
    json_files: list[Path], wordlist_path: Path, log_path: Path
) -> list[PatchRecord]:
    """Patch all locale JSON files and write the audit log.

    Returns all patch records.
    """
    # Idempotency guard: warn if a patch log already exists from a previous run
    if log_path.exists() and log_path.stat().st_size > 0:
        print(f"  Warning: existing patch log found at {log_path}.")
        print("  Re-patching already-patched files will find no matches (asterisks don't match words).")
        print("  Use --clean for a fresh run, or --skip-text-repack to reuse existing patches.")

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
