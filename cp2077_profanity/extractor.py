"""Extract English localization JSON files from Cyberpunk 2077 archives using WolvenKit CLI."""

import subprocess
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

from .config import Config


def find_locale_archives(game_dir: Path) -> list[Path]:
    """Find .archive files that contain English localization data.

    Searches both base game (archive/pc/content/) and Phantom Liberty
    expansion (archive/pc/ep1/) directories.
    """
    search_dirs = [
        ("base game", game_dir / "archive" / "pc" / "content"),
        ("Phantom Liberty", game_dir / "archive" / "pc" / "ep1"),
    ]

    archives: list[Path] = []
    for label, archive_dir in search_dirs:
        if not archive_dir.exists():
            continue

        found = list(archive_dir.glob("lang_en_text.archive"))
        if not found:
            # Fallback: any archive with 'lang_en' in the name
            found = list(archive_dir.glob("*lang_en*.archive"))

        if found:
            print(f"  Found {len(found)} locale archive(s) in {label}: {archive_dir}")
            archives.extend(found)

    if not archives:
        raise FileNotFoundError(
            f"No English locale archive found in {game_dir / 'archive' / 'pc'}. "
            "Expected 'lang_en_text.archive' in content/ and/or ep1/. "
            "Check that game_dir points to your Cyberpunk 2077 installation."
        )

    return sorted(archives)


def unbundle_archives(config: Config, extract_dir: Path) -> None:
    """Run WolvenKit unbundle on each locale archive."""
    archives = find_locale_archives(config.game_dir)

    for archive in archives:
        print(f"  Unbundling: {archive.name}")
        cmd = [
            str(config.wolvenkit_cli),
            "unbundle",
            "-p", str(archive),
            "-o", str(extract_dir),
        ]
        result = subprocess.run(cmd, capture_output=True, text=True)
        if result.returncode != 0:
            print(f"  Warning: WolvenKit returned non-zero for {archive.name}")
            if result.stderr:
                print(f"  stderr: {result.stderr.strip()}")


def convert_cr2w_to_json(config: Config, extract_dir: Path) -> list[Path]:
    """Convert CR2W locale files to plain JSON using WolvenKit cr2w -s.

    WolvenKit unbundle produces CR2W binary files with a .json extension.
    Running 'cr2w -s' on each file produces a .json.json file containing
    the actual human-readable JSON that can be scanned and patched.

    Returns the list of .json.json files produced.
    """
    cr2w_files = [
        p for p in extract_dir.rglob("*.json")
        if not p.name.endswith(".json.json")
        and _is_en_us_path(p)
    ]

    if not cr2w_files:
        print("  Warning: no CR2W locale files found to convert.")
        return []

    def _convert_one(cr2w_file: Path) -> Path | None:
        expected_output = cr2w_file.parent / (cr2w_file.name + ".json")
        cmd = [str(config.wolvenkit_cli), "cr2w", "-s", str(cr2w_file)]
        result = subprocess.run(cmd, capture_output=True, text=True)
        if result.returncode != 0:
            print(f"  Warning: cr2w conversion failed for {cr2w_file.name}")
            if result.stderr:
                print(f"  stderr: {result.stderr.strip()}")
            return None
        return expected_output if expected_output.exists() else None

    print(f"  Converting {len(cr2w_files)} file(s) with {config.workers} worker(s)...")
    produced: list[Path] = []
    with ThreadPoolExecutor(max_workers=config.workers) as executor:
        futures = {executor.submit(_convert_one, f): f for f in cr2w_files}
        for future in as_completed(futures):
            result = future.result()
            if result:
                produced.append(result)

    return sorted(produced)


def extract_archives(config: Config) -> Path:
    """Run the full extraction pipeline: unbundle archives then convert CR2W to JSON.

    Returns the path to the extraction output directory.
    """
    extract_dir = config.work_dir / "extracted"
    extract_dir.mkdir(parents=True, exist_ok=True)

    unbundle_archives(config, extract_dir)
    convert_cr2w_to_json(config, extract_dir)

    return extract_dir


def _is_en_us_path(path: Path) -> bool:
    """Return True if the path is under an en-us locale directory."""
    parts_lower = [p.lower() for p in path.parts]
    return "en-us" in parts_lower or "en_us" in parts_lower


def collect_locale_jsons(extract_dir: Path) -> list[Path]:
    """Collect all converted locale JSON files (.json.json) for scanning/patching.

    These are the plain-JSON outputs produced by 'cr2w -s'.
    Only includes files under en-us paths.
    """
    json_files = [
        p for p in extract_dir.rglob("*.json.json")
        if _is_en_us_path(p)
    ]
    return sorted(json_files)
