"""Auto-discovery of radio track hashes from audio_2_soundbanks.archive.

Extracts all .wem files (via WolvenKit uncook, which converts to .Ogg),
measures each file's duration with ffprobe, and writes a radio_tracks.json
containing only files long enough to be songs (>= min_duration_seconds).
"""

import json
import shutil
import subprocess
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

from .fileutil import atomic_write

from rich.progress import BarColumn, MofNCompleteColumn, Progress, SpinnerColumn, TextColumn, TimeRemainingColumn
from rich import print as rprint

from .config import Config
from .radio import find_radio_archive
from .wsl_utils import detect_duration


def _uncook_full_archive(config: Config, archive: Path, out_dir: Path) -> None:
    """Run WolvenKit uncook on the full archive (no regex filter) to extract all .Ogg files.

    Streams WolvenKit output line-by-line and updates a live file counter so the
    user can see progress during what may be a multi-minute extraction.
    """
    out_dir.mkdir(parents=True, exist_ok=True)
    cmd = [
        str(config.wolvenkit_cli),
        "uncook",
        str(archive),
        "-o", str(out_dir),
    ]
    files_processed = 0
    with Progress(
        TextColumn("  [bold]{task.description}"),
        SpinnerColumn(),
        TextColumn("{task.completed} file(s)"),
    ) as progress:
        task = progress.add_task(f"Extracting {archive.name}", total=None)
        with subprocess.Popen(
            cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True
        ) as proc:
            for line in iter(proc.stdout.readline, ""):
                if line.strip():
                    files_processed += 1
                    progress.advance(task)
            proc.wait()

        if proc.returncode != 0:
            rprint(f"  [yellow]Warning: uncook returned non-zero (exit {proc.returncode})[/yellow]")

    rprint(f"  Uncook complete: {files_processed} output line(s) from WolvenKit")


def discover_radio_tracks(
    config: Config,
    output_path: Path,
    min_duration_seconds: int,
    keep_extracted: bool = False,
) -> int:
    """Extract audio_2_soundbanks.archive, filter by duration, and write radio_tracks.json.

    Returns the number of tracks written to output_path.
    """
    work_dir = config.work_dir / "radio_discovery"
    ogg_dir = work_dir / "ogg"

    # Find the archive
    archive = find_radio_archive(config.game_dir)
    rprint(f"  Radio archive: {archive}")

    # Extract (resume-friendly: skip if .Ogg files already exist)
    existing_ogg = list(ogg_dir.rglob("*.ogg")) if ogg_dir.exists() else []
    if existing_ogg:
        rprint(f"  Reusing {len(existing_ogg)} existing .Ogg file(s) from previous extraction")
        ogg_files = existing_ogg
    else:
        _uncook_full_archive(config, archive, ogg_dir)
        ogg_files = list(ogg_dir.rglob("*.ogg"))

    if not ogg_files:
        rprint("  [yellow]Warning: no .Ogg files produced by uncook.[/yellow]")
        return 0

    rprint(f"  Checking duration of {len(ogg_files)} file(s) (>= {min_duration_seconds}s threshold)...")

    # Parallel duration check
    results: list[tuple[Path, float]] = []

    def _check(ogg: Path) -> tuple[Path, float]:
        return ogg, detect_duration(ogg)

    with Progress(
        TextColumn("  [bold]{task.description}"),
        BarColumn(),
        MofNCompleteColumn(),
        TimeRemainingColumn(),
    ) as progress:
        task = progress.add_task(
            f"Checking durations ({config.workers} workers)", total=len(ogg_files)
        )
        with ThreadPoolExecutor(max_workers=config.workers) as executor:
            futures = {executor.submit(_check, ogg): ogg for ogg in ogg_files}
            for future in as_completed(futures):
                results.append(future.result())
                progress.advance(task)

    # Filter by minimum duration and deduplicate by hash stem.
    # The same .wem can appear under multiple subdirectory paths after uncook,
    # so we keep only the first occurrence of each hash.
    seen: set[str] = set()
    long_files: list[tuple[Path, float]] = []
    for ogg, dur in sorted(results, key=lambda x: x[0].stem):
        if dur >= min_duration_seconds and ogg.stem not in seen:
            seen.add(ogg.stem)
            long_files.append((ogg, dur))

    rprint(f"  {len(long_files)} unique file(s) >= {min_duration_seconds}s (from {len(results)} total)")

    # Build track list — hash is the file stem (WolvenKit uses FNV1a64 hash as filename)
    tracks = [
        {"hash": ogg.stem, "station": "unknown", "title": "unknown"}
        for ogg, _ in long_files
    ]

    # Write output
    with atomic_write(output_path, encoding="utf-8") as f:
        json.dump(tracks, f, indent=2)

    # Clean up extracted files unless asked to keep them
    if not keep_extracted and ogg_dir.exists():
        try:
            shutil.rmtree(ogg_dir)
            rprint("  Cleaned up extracted .Ogg files")
        except OSError as e:
            rprint(f"  [yellow]Warning: could not fully clean up {ogg_dir}: {e}[/yellow]")

    return len(tracks)
