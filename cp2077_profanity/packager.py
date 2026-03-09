"""Assemble the final REDmod package and create a distributable zip."""

import json
import shutil
import zipfile
from pathlib import Path

from .config import Config


def build_redmod_layout(config: Config, packed_dir: Path, modified_files: set[Path]) -> Path:
    """Assemble the REDmod folder layout for a localization mod.

    Structure:
        <mod_name>/
            info.json
            localization/
                en-us/
                    <patched CR2W .json files, preserving internal path structure>

    Only includes CR2W .json files whose corresponding .json.json was actually
    modified during patching. 'modified_files' contains resolved paths of the
    CR2W .json files that had profanity patched.
    """
    mod_dir = config.output_dir / config.mod_name
    locale_dir = mod_dir / "localization" / "en-us"

    # Clean and recreate
    if mod_dir.exists():
        shutil.rmtree(mod_dir)
    locale_dir.mkdir(parents=True)

    # Write info.json manifest (name must match folder name)
    info = {
        "name": config.mod_name,
        "version": config.mod_version,
        "description": config.mod_description,
    }
    with open(mod_dir / "info.json", "w", encoding="utf-8") as f:
        json.dump(info, f, indent=2)

    # Find the en-us source directory inside the extracted tree
    en_us_dirs = [
        p for p in packed_dir.rglob("en-us")
        if p.is_dir()
    ]
    if not en_us_dirs:
        raise FileNotFoundError(
            f"No en-us directory found under {packed_dir}. "
            "Ensure extraction completed successfully."
        )

    # Copy only modified CR2W .json files from all en-us directories
    count = 0
    for src_en_us in en_us_dirs:
        for src_file in src_en_us.rglob("*.json"):
            if src_file.name.endswith(".json.json"):
                continue
            if src_file.resolve() not in modified_files:
                continue
            rel = src_file.relative_to(src_en_us)
            dest = locale_dir / rel
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src_file, dest)
            count += 1

    print(f"  Copied {count} modified locale file(s) into REDmod layout")
    return mod_dir


def create_zip(config: Config, mod_dir: Path) -> Path:
    """Create a distributable zip file from the REDmod layout."""
    config.output_dir.mkdir(parents=True, exist_ok=True)
    zip_path = config.output_dir / f"{config.mod_name}.zip"

    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
        for file in mod_dir.rglob("*"):
            if file.is_file():
                arcname = file.relative_to(config.output_dir)
                zf.write(file, arcname)

    print(f"  Package created: {zip_path}")
    return zip_path


def write_summary(
    config: Config,
    files_modified: int,
    strings_changed: int,
    unique_words: set[str],
) -> Path:
    """Write a human-readable summary of the pipeline run."""
    summary_path = config.output_dir / "summary.txt"
    config.output_dir.mkdir(parents=True, exist_ok=True)

    lines = [
        f"CP2077 Profanity Filter - Run Summary",
        f"=====================================",
        f"Mod name:         {config.mod_name}",
        f"Mod version:      {config.mod_version}",
        f"Files modified:   {files_modified}",
        f"Strings changed:  {strings_changed}",
        f"Unique words:     {len(unique_words)}",
        f"",
        f"Words flagged:",
    ]
    for word in sorted(unique_words, key=str.lower):
        lines.append(f"  - {word}")

    with open(summary_path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")

    return summary_path


def package_mod(config: Config, extract_dir: Path, patch_records: list) -> Path:
    """Full packaging step: build REDmod layout from modified files only, create zip, write summary."""
    # Patch records store .json.json paths; the CR2W .json is the same path
    # without the trailing .json (e.g. foo.json.json → foo.json)
    modified_cr2w_files: set[Path] = set()
    for r in patch_records:
        json_json_path = Path(r.filepath)
        cr2w_path = json_json_path.with_suffix("")  # strip trailing .json
        modified_cr2w_files.add(cr2w_path.resolve())

    mod_dir = build_redmod_layout(config, extract_dir, modified_cr2w_files)
    zip_path = create_zip(config, mod_dir)

    # Compute summary stats from patch records
    files_modified = len({r.filepath for r in patch_records})
    strings_changed = len(patch_records)
    unique_words: set[str] = set()
    for r in patch_records:
        unique_words.update(w.lower() for w in r.words_replaced)

    write_summary(config, files_modified, strings_changed, unique_words)

    return zip_path
