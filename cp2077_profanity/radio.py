"""Radio music pipeline: extract, multi-pass filter, and repack radio song audio."""

import json
import shutil
import subprocess
from concurrent.futures import ThreadPoolExecutor, as_completed
from importlib import resources
from pathlib import Path

from rich.progress import BarColumn, MofNCompleteColumn, Progress, TextColumn, TimeRemainingColumn

from .config import Config
from .wsl_utils import check_monkeyplug, convert_ogg_to_wem, detect_channels, run_monkeyplug_on_file, to_wsl_path


RADIO_ARCHIVE_NAME = "audio_2_soundbanks.archive"

# 3-pass monkeyplug configuration: larger models first for best detection,
# each pass feeds its output into the next.
RADIO_PASSES = [
    {"whisper_model": "large-v3", "max_workers": 1},
    {"whisper_model": "medium",   "max_workers": 2},
    {"whisper_model": "base",     "max_workers": 6},
]


def find_radio_archive(game_dir: Path) -> Path:
    """Locate audio_2_soundbanks.archive in the game directory."""
    search_dirs = [
        game_dir / "archive" / "pc" / "content",
        game_dir / "archive" / "pc" / "ep1",
    ]
    for d in search_dirs:
        candidate = d / RADIO_ARCHIVE_NAME
        if candidate.exists():
            return candidate
    raise FileNotFoundError(
        f"{RADIO_ARCHIVE_NAME} not found under {game_dir / 'archive' / 'pc'}\n"
        "Check your game_dir in config.toml [paths]"
    )


def load_radio_tracks(tracks_file: Path | None) -> list[dict]:
    """Load the curated radio track list.

    If tracks_file is None, falls back to the bundled data/radio_tracks.json.
    Returns a list of dicts with 'hash', 'station', and 'title' keys.
    """
    if tracks_file is not None:
        if not tracks_file.exists():
            raise FileNotFoundError(f"Radio tracks file not found: {tracks_file}")
        with open(tracks_file, encoding="utf-8") as f:
            return json.load(f)

    # Use bundled default
    pkg = resources.files("cp2077_profanity") / "data" / "radio_tracks.json"
    with resources.as_file(pkg) as p:
        with open(p, encoding="utf-8") as f:
            return json.load(f)


def extract_radio_wem_files(
    config: Config,
    radio_dir: Path,
    track_hashes: list[str],
) -> Path:
    """Extract target .wem files (and their .Ogg conversions) from the radio archive.

    Uses WolvenKit uncook with --regex to extract matching files in batches.
    Returns the directory containing the extracted files.
    """
    wem_dir = radio_dir / "wem_files"
    wem_dir.mkdir(parents=True, exist_ok=True)

    archive = find_radio_archive(config.game_dir)
    print(f"  Radio archive: {archive}")

    # Filter out placeholder hashes
    real_hashes = [h for h in track_hashes if h != "0"]
    if not real_hashes:
        print("  Warning: no real track hashes in radio_tracks.json (only placeholders).")
        return wem_dir

    print(f"  Extracting {len(real_hashes)} radio track(s)...")

    batch_size = 50
    batches = [real_hashes[i : i + batch_size] for i in range(0, len(real_hashes), batch_size)]

    def _extract_batch(batch: list[str]) -> None:
        regex_pattern = "(" + "|".join(batch) + r")\.wem$"
        cmd = [
            str(config.wolvenkit_cli),
            "uncook",
            str(archive),
            "-o", str(wem_dir),
            "--regex", regex_pattern,
        ]
        result = subprocess.run(cmd, capture_output=True, text=True)
        if result.returncode != 0 and result.stderr:
            print(f"  Warning: uncook batch failed: {result.stderr.strip()[:200]}")

    with Progress(
        TextColumn("  [bold]{task.description}"),
        BarColumn(),
        MofNCompleteColumn(),
        TimeRemainingColumn(),
    ) as progress:
        task = progress.add_task(
            f"Extracting radio files ({config.workers} workers)", total=len(batches)
        )
        with ThreadPoolExecutor(max_workers=config.workers) as executor:
            futures = {executor.submit(_extract_batch, b): b for b in batches}
            for future in as_completed(futures):
                future.result()
                progress.advance(task)

    return wem_dir


def process_radio_multipass(
    config: Config,
    radio_dir: Path,
    ogg_files: list[Path],
) -> list[Path]:
    """Run 3 sequential monkeyplug passes on radio .Ogg files.

    Pass 1: large-v3, 1 worker  → radio_dir/pass_1/
    Pass 2: medium,   2 workers → radio_dir/pass_2/ (input = pass_1 output)
    Pass 3: base,     6 workers → radio_dir/pass_3/ (input = pass_2 output)

    Channel count is detected from the first .Ogg file and applied to all calls.
    Returns list of final processed .Ogg paths (from pass_3/).
    """
    check_monkeyplug()

    wsl_wordlist = to_wsl_path(config.wordlist_path)

    # Detect channel count from the first file (radio songs are typically stereo)
    channels = 2
    if ogg_files:
        channels = detect_channels(ogg_files[0])
        print(f"  Detected {channels} audio channel(s) from first track")

    current_inputs = list(ogg_files)

    for pass_num, pass_cfg in enumerate(RADIO_PASSES, start=1):
        whisper_model = pass_cfg["whisper_model"]
        max_workers = pass_cfg["max_workers"]

        pass_dir = radio_dir / f"pass_{pass_num}"
        pass_dir.mkdir(parents=True, exist_ok=True)

        label = f"Radio pass {pass_num}/{len(RADIO_PASSES)} ({whisper_model}, {max_workers} worker{'s' if max_workers != 1 else ''})"
        print(f"  {label}")

        pass_outputs: list[Path] = []

        def _run_one(ogg: Path) -> Path | None:
            out_file = pass_dir / ogg.name
            return run_monkeyplug_on_file(ogg, out_file, wsl_wordlist, whisper_model, channels)

        with Progress(
            TextColumn("  [bold]{task.description}"),
            BarColumn(),
            MofNCompleteColumn(),
            TimeRemainingColumn(),
        ) as progress:
            task = progress.add_task(label, total=len(current_inputs))
            with ThreadPoolExecutor(max_workers=max_workers) as executor:
                futures = {executor.submit(_run_one, ogg): ogg for ogg in current_inputs}
                for future in as_completed(futures):
                    result = future.result()
                    if result:
                        pass_outputs.append(result)
                    progress.advance(task)

        if not pass_outputs:
            print(f"  Warning: pass {pass_num} produced no output files.")
            break

        print(f"  Pass {pass_num} complete: {len(pass_outputs)}/{len(current_inputs)} file(s) processed")
        current_inputs = pass_outputs

    return current_inputs


def pack_radio_archive(
    config: Config,
    wem_dir: Path,
    processed_wem_files: list[Path],
) -> Path:
    """Replace original .wem files with processed ones and repack into a radio archive.

    Copies processed .wem files into the extracted directory tree, removes all
    non-.wem files, then runs WolvenKit pack.
    Returns the directory containing the repacked .archive.
    """
    replaced = 0
    for wem_file in processed_wem_files:
        originals = list(wem_dir.rglob(wem_file.name))
        for orig in originals:
            if orig.suffix == ".wem":
                shutil.copy2(wem_file, orig)
                replaced += 1

    print(f"  Replaced {replaced} radio .wem file(s) in extraction tree")

    # Remove .Ogg files and any non-.wem files uncook may have produced
    for f in wem_dir.rglob("*"):
        if f.is_file() and f.suffix not in (".wem",):
            f.unlink()

    print(f"  Repacking radio archive from: {wem_dir}")
    cmd = [str(config.wolvenkit_cli), "pack", "-p", str(wem_dir)]
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        raise RuntimeError(f"WolvenKit pack failed for radio archive: {result.stderr.strip()}")

    packed_dir = wem_dir.parent
    archives = list(packed_dir.glob("*.archive"))
    if not archives:
        raise RuntimeError(f"No .archive produced in {packed_dir}")

    print(f"  Radio archive(s): {[a.name for a in archives]}")
    return packed_dir


def run_radio_pipeline(config: Config) -> Path | None:
    """Full radio music pipeline: load tracks → extract → multi-pass filter → convert → repack.

    Returns the directory containing the radio .archive, or None if no tracks
    were found or the track list contains only placeholders.
    """
    radio_dir = config.work_dir / "radio"
    radio_dir.mkdir(parents=True, exist_ok=True)

    # Load curated track list
    tracks = load_radio_tracks(config.radio_tracks_file)
    track_hashes = [str(t["hash"]) for t in tracks]
    real_hashes = [h for h in track_hashes if h != "0"]

    if not real_hashes:
        print("  No real radio track hashes configured — skipping radio pipeline.")
        print("  Edit data/radio_tracks.json or set radio_tracks_file in config.toml [radio]")
        return None

    print(f"  Loaded {len(real_hashes)} radio track hash(es)")

    # Step A: extract .wem files (resume-friendly: skip if already extracted)
    wem_dir = radio_dir / "wem_files"
    existing_ogg = list(wem_dir.rglob("*.Ogg")) + list(wem_dir.rglob("*.ogg")) if wem_dir.exists() else []
    if existing_ogg:
        print(f"  Reusing {len(existing_ogg)} existing radio .Ogg file(s)")
        ogg_files = existing_ogg
    else:
        wem_dir = extract_radio_wem_files(config, radio_dir, real_hashes)
        ogg_files = list(wem_dir.rglob("*.Ogg")) + list(wem_dir.rglob("*.ogg"))

    if not ogg_files:
        print("  Warning: no .Ogg files produced by uncook — check track hashes.")
        return None

    print(f"  Found {len(ogg_files)} radio .Ogg file(s) to process")

    # Step B: 3-pass monkeyplug
    processed_ogg = process_radio_multipass(config, radio_dir, ogg_files)
    if not processed_ogg:
        print("  Warning: radio multi-pass processing produced no output.")
        return None

    # Step C: convert processed .Ogg → .wem
    wem_out_dir = radio_dir / "processed_wem"
    processed_wem = convert_ogg_to_wem(config, processed_ogg, wem_out_dir)
    if not processed_wem:
        print("  Warning: no .wem files produced from radio conversion.")
        return None

    # Step D: repack radio archive
    packed_dir = pack_radio_archive(config, wem_dir, processed_wem)
    return packed_dir
