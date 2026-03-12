"""Assemble the final mod package and create a distributable zip."""

import re
import zipfile
from pathlib import Path

from .config import Config
from .fileutil import atomic_write


def create_zip(
    config: Config,
    packed_dir: Path,
    voice_packed_dir: Path | None = None,
    radio_packed_dir: Path | None = None,
) -> Path:
    """Create a distributable zip containing the repacked .archive file(s).

    The zip layout mirrors the game's archive directory so users can extract
    directly into the game folder:

        archive/pc/mod/<mod_name>.archive         (text)
        archive/pc/mod/<mod_name>_voice.archive   (audio, if present)
        archive/pc/mod/<mod_name>_radio.archive   (radio, if present)

    Install by extracting to the Cyberpunk 2077 game directory.
    """
    config.output_dir.mkdir(parents=True, exist_ok=True)
    zip_path = config.output_dir / f"{config.mod_name}.zip"

    archives = list(packed_dir.glob("*.archive"))
    if not archives:
        raise FileNotFoundError(f"No .archive files found in {packed_dir}")

    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
        for i, archive in enumerate(archives):
            suffix = f"_{i}" if i > 0 else ""
            arcname = Path("archive") / "pc" / "mod" / f"{config.mod_name}{suffix}.archive"
            zf.write(archive, arcname)

        if voice_packed_dir:
            voice_archives = list(voice_packed_dir.glob("*.archive"))
            for i, archive in enumerate(voice_archives):
                suffix = f"_{i}" if i > 0 else ""
                arcname = Path("archive") / "pc" / "mod" / f"{config.mod_name}_voice{suffix}.archive"
                zf.write(archive, arcname)
            if voice_archives:
                print(f"  Included {len(voice_archives)} voice archive(s) in package")

        if radio_packed_dir:
            radio_archives = list(radio_packed_dir.glob("*.archive"))
            for i, archive in enumerate(radio_archives):
                suffix = f"_{i}" if i > 0 else ""
                arcname = Path("archive") / "pc" / "mod" / f"{config.mod_name}_radio{suffix}.archive"
                zf.write(archive, arcname)
            if radio_archives:
                print(f"  Included {len(radio_archives)} radio archive(s) in package")

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

    with atomic_write(summary_path, encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")

    return summary_path


def package_mod(
    config: Config,
    packed_dir: Path,
    voice_packed_dir: Path | None,
    radio_packed_dir: Path | None,
    patch_records: list,
) -> Path:
    """Full packaging step: zip the repacked .archive(s) and write summary."""
    zip_path = create_zip(config, packed_dir, voice_packed_dir, radio_packed_dir)

    # Compute summary stats from patch records
    files_modified = len({r.filepath for r in patch_records})
    strings_changed = len(patch_records)
    unique_words: set[str] = set()
    for r in patch_records:
        if r.words_replaced:
            unique_words.update(w.lower() for w in r.words_replaced)
        else:
            # Loaded from CSV without words_replaced — extract originals from asterisk spans
            for m in re.finditer(r"\*+", r.replacement):
                original_word = r.original[m.start():m.end()]
                if original_word and not original_word.startswith("*"):
                    unique_words.add(original_word.lower())

    write_summary(config, files_modified, strings_changed, unique_words)

    return zip_path
