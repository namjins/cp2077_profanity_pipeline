"""Repack modified files back into .archive format using WolvenKit CLI."""

import subprocess
from pathlib import Path

from .config import Config


def convert_json_to_cr2w(config: Config, extract_dir: Path) -> None:
    """Convert patched .json.json files back to CR2W binary using WolvenKit cr2w -d.

    After patching, locale files exist as .json.json (plain JSON).
    Running 'cr2w -d' deserializes each back into its CR2W binary .json form
    so it can be repacked into an .archive.
    """
    json_json_files = list(extract_dir.rglob("*.json.json"))

    if not json_json_files:
        print("  Warning: no .json.json files found to deserialize.")
        return

    for jj_file in json_json_files:
        print(f"  Deserializing: {jj_file.name}")
        cmd = [str(config.wolvenkit_cli), "cr2w", "-d", str(jj_file)]
        result = subprocess.run(cmd, capture_output=True, text=True)
        if result.returncode != 0:
            print(f"  Warning: cr2w deserialization failed for {jj_file.name}")
            if result.stderr:
                print(f"  stderr: {result.stderr.strip()}")


def repack_archives(config: Config) -> Path:
    """Repack the extracted (and patched) files back into .archive format.

    Steps:
    1. Deserialize .json.json files back to CR2W binary (.json)
    2. Run WolvenKit pack on the extracted directory

    WolvenKit 'pack' outputs the .archive alongside the input folder,
    not into a separate output directory.

    Returns the path to the directory containing the repacked archive(s).
    """
    extract_dir = config.work_dir / "extracted"

    if not extract_dir.exists():
        raise FileNotFoundError(
            f"Extracted directory not found: {extract_dir}. Run extract step first."
        )

    # Step 1: deserialize patched .json.json → CR2W .json
    convert_json_to_cr2w(config, extract_dir)

    # Step 2: pack — output lands next to the input folder (WolvenKit behaviour)
    print(f"  Repacking from: {extract_dir}")
    cmd = [
        str(config.wolvenkit_cli),
        "pack",
        "-p", str(extract_dir),
    ]
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        msg = f"WolvenKit pack failed (exit {result.returncode})"
        if result.stderr:
            msg += f": {result.stderr.strip()}"
        raise RuntimeError(msg)

    # WolvenKit places the .archive alongside the input folder
    packed_dir = extract_dir.parent
    archives = list(packed_dir.glob("*.archive"))
    if not archives:
        raise RuntimeError(
            f"WolvenKit pack succeeded but no .archive found in {packed_dir}"
        )

    print(f"  Repacked archive(s): {[a.name for a in archives]}")
    return packed_dir
