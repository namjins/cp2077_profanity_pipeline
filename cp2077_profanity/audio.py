"""Audio pipeline: extract, process, and repack voice lines containing profanity."""

import json
import os
import re
import shutil
import subprocess
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

from rich.progress import BarColumn, MofNCompleteColumn, Progress, TextColumn, TimeRemainingColumn

from .config import Config
from .scanner import _extract_entries


# Names of voiceover map files inside the voice archive
VOICEOVER_MAP_FILES = [
    "voiceovermap.json",
    "voiceovermap_1.json",
    "voiceovermap_helmet.json",
    "voiceovermap_holocall.json",
    "voiceovermap_rewinded.json",
]


def find_voice_archives(game_dir: Path) -> list[Path]:
    """Find lang_en_voice.archive files in base game and EP1 directories."""
    search_dirs = [
        game_dir / "archive" / "pc" / "content",
        game_dir / "archive" / "pc" / "ep1",
    ]
    archives = []
    for d in search_dirs:
        found = list(d.glob("lang_en_voice.archive")) if d.exists() else []
        if found:
            archives.extend(found)
    return sorted(archives)


def extract_voiceover_maps(config: Config, voice_extract_dir: Path) -> Path:
    """Extract and deserialize voiceover map JSON files from the voice archive.

    Returns the directory containing the extracted (and cr2w-converted) map files.
    """
    archives = find_voice_archives(config.game_dir)
    if not archives:
        raise FileNotFoundError(
            f"No lang_en_voice.archive found under {config.game_dir / 'archive' / 'pc'}"
        )

    maps_dir = voice_extract_dir / "voiceover_maps"
    maps_dir.mkdir(parents=True, exist_ok=True)

    for archive in archives:
        print(f"  Extracting voiceover maps from: {archive.name}")
        for map_name in VOICEOVER_MAP_FILES:
            cmd = [
                str(config.wolvenkit_cli),
                "unbundle",
                "-p", str(archive),
                "-o", str(maps_dir),
                "--pattern", f"*{map_name}",
            ]
            subprocess.run(cmd, capture_output=True, text=True)

    # Deserialize CR2W .json files to .json.json in parallel
    cr2w_files = [p for p in maps_dir.rglob("*.json") if not p.name.endswith(".json.json")]
    if cr2w_files:
        def _convert_map(f: Path) -> None:
            subprocess.run(
                [str(config.wolvenkit_cli), "cr2w", "-s", str(f)],
                capture_output=True, text=True,
            )

        with Progress(
            TextColumn("  [bold]{task.description}"),
            BarColumn(),
            MofNCompleteColumn(),
            TimeRemainingColumn(),
        ) as progress:
            task = progress.add_task(
                f"Converting voiceover maps ({config.workers} workers)", total=len(cr2w_files)
            )
            with ThreadPoolExecutor(max_workers=config.workers) as executor:
                futures = {executor.submit(_convert_map, f): f for f in cr2w_files}
                for future in as_completed(futures):
                    future.result()
                    progress.advance(task)

    return maps_dir


def build_string_id_to_wem_map(maps_dir: Path) -> dict[str, dict[str, str]]:
    """Parse voiceover map files and build a stringId → {female, male} wem path lookup.

    Returns a dict mapping stringId (str) to {"female": depot_path, "male": depot_path}.
    Depot paths use backslash notation, e.g. 'base\\localization\\en-us\\vo\\file.wem'.
    """
    lookup: dict[str, dict[str, str]] = {}

    json_json_files = list(maps_dir.rglob("*.json.json"))
    if not json_json_files:
        print("  Warning: no voiceover map .json.json files found.")
        return lookup

    for map_file in json_json_files:
        with open(map_file, encoding="utf-8") as f:
            data = json.load(f)

        entries = _extract_entries(data)
        for entry in entries:
            string_id = str(entry.get("stringId", "")).strip()
            if not string_id or string_id == "0":
                continue

            female_path = ""
            male_path = ""
            try:
                female_path = entry["femaleResPath"]["DepotPath"]["$value"]
            except (KeyError, TypeError):
                pass
            try:
                male_path = entry["maleResPath"]["DepotPath"]["$value"]
            except (KeyError, TypeError):
                pass

            if female_path or male_path:
                existing = lookup.get(string_id)
                if existing:
                    # Merge: keep non-empty paths from both sources
                    if female_path and not existing["female"]:
                        existing["female"] = female_path
                    if male_path and not existing["male"]:
                        existing["male"] = male_path
                else:
                    lookup[string_id] = {"female": female_path, "male": male_path}

    print(f"  Built voiceover map: {len(lookup)} entries")
    return lookup


def find_wem_paths_for_records(
    patch_records: list,
    voiceover_map: dict[str, dict[str, str]],
) -> list[tuple[str, str]]:
    """Return deduplicated list of (string_id, depot_path) tuples for patched records.

    Maps each patch record's string_id to its corresponding .wem depot path(s).
    Collects both female and male paths when the stringId appears in the voiceover
    map, since the same line often has both gender variants recorded.
    """
    targets: list[tuple[str, str]] = []
    seen: set[str] = set()

    for record in patch_records:
        string_id = str(record.string_id or "").strip()
        if not string_id or string_id not in voiceover_map:
            continue

        paths = voiceover_map[string_id]
        for depot_path in (paths.get("female", ""), paths.get("male", "")):
            if depot_path and depot_path not in seen:
                seen.add(depot_path)
                targets.append((string_id, depot_path))

    return targets


def extract_target_wem_files(
    config: Config,
    voice_extract_dir: Path,
    depot_paths: list[str],
) -> Path:
    """Extract specific .wem files (and their .Ogg conversions) from voice archives.

    Uses WolvenKit uncook with --regex to extract matching files in batches.
    Batches filenames to avoid spinning up WolvenKit once per file.
    Returns the directory containing the extracted files.
    """
    wem_dir = voice_extract_dir / "wem_files"
    wem_dir.mkdir(parents=True, exist_ok=True)

    archives = find_voice_archives(config.game_dir)
    if not archives:
        raise FileNotFoundError("No lang_en_voice.archive found.")

    # Collect all filename stems from depot paths
    # depot path format: base\localization\en-us\vo\filename.wem
    stems = sorted({Path(p.replace("\\", "/")).stem for p in depot_paths})
    print(f"  Extracting {len(stems)} target .wem file(s)...")

    # Batch stems into regex groups to reduce WolvenKit invocations.
    # WolvenKit --regex matches against the full depot path, so we
    # use a partial match on the filename stem.
    # Limit batch size to avoid command-line length issues.
    batch_size = 50
    batches = [stems[i : i + batch_size] for i in range(0, len(stems), batch_size)]

    # Flatten into (batch, archive) jobs and run in parallel
    jobs = [(batch, archive) for batch in batches for archive in archives]

    def _extract_job(batch: list[str], archive: Path) -> None:
        regex_pattern = "(" + "|".join(re.escape(s) for s in batch) + r")\.wem$"
        cmd = [
            str(config.wolvenkit_cli),
            "uncook",
            str(archive),
            "-o", str(wem_dir),
            "--regex", regex_pattern,
        ]
        subprocess.run(cmd, capture_output=True, text=True)

    with Progress(
        TextColumn("  [bold]{task.description}"),
        BarColumn(),
        MofNCompleteColumn(),
        TimeRemainingColumn(),
    ) as progress:
        task = progress.add_task(
            f"Extracting voice files ({config.workers} workers)", total=len(jobs)
        )
        with ThreadPoolExecutor(max_workers=config.workers) as executor:
            futures = {executor.submit(_extract_job, b, a): (b, a) for b, a in jobs}
            for future in as_completed(futures):
                future.result()
                progress.advance(task)

    return wem_dir


def process_audio_with_monkeyplug(
    config: Config,
    wem_dir: Path,
    ogg_files: list[Path],
) -> list[Path]:
    """Run monkeyplug on each .Ogg file to mute profane segments.

    Runs up to config.monkeyplug_workers instances in parallel (default 6).
    Skips files that were already processed in a previous run.
    monkeyplug is invoked via WSL so it can use CUDA on the Windows machine.
    Returns list of processed .Ogg output paths.
    """
    processed_dir = wem_dir.parent / "processed_ogg"
    processed_dir.mkdir(parents=True, exist_ok=True)

    wsl_wordlist = _to_wsl_path(config.wordlist_path)

    def _process_one(ogg: Path) -> Path | None:
        out_file = processed_dir / ogg.name

        # Skip if already processed (allows resuming interrupted runs)
        if out_file.exists():
            return out_file

        wsl_input = _to_wsl_path(ogg)
        wsl_output = _to_wsl_path(out_file)
        cmd = [
            "wsl",
            "monkeyplug",
            "-i", wsl_input,
            "-o", wsl_output,
            "-w", wsl_wordlist,
            "-m", "whisper",
            "--whisper-model-name", config.whisper_model,
            "-b", "false",  # silence mode (no beep)
            "--force", "true",
        ]
        result = subprocess.run(cmd, capture_output=True, text=True)
        if result.returncode != 0:
            print(f"  Warning: monkeyplug failed for {ogg.name}")
            if result.stderr:
                print(f"  stderr: {result.stderr.strip()[:200]}")
            return None
        return out_file if out_file.exists() else None

    processed: list[Path] = []
    with Progress(
        TextColumn("  [bold]{task.description}"),
        BarColumn(),
        MofNCompleteColumn(),
        TimeRemainingColumn(),
    ) as progress:
        task = progress.add_task(
            f"Processing audio ({config.monkeyplug_workers} workers)",
            total=len(ogg_files),
        )
        with ThreadPoolExecutor(max_workers=config.monkeyplug_workers) as executor:
            futures = {executor.submit(_process_one, ogg): ogg for ogg in ogg_files}
            for future in as_completed(futures):
                result = future.result()
                if result:
                    processed.append(result)
                progress.advance(task)

    return processed


def convert_ogg_to_wem(config: Config, ogg_files: list[Path], wem_out_dir: Path) -> list[Path]:
    """Convert processed .Ogg files back to .wem using sound2wem (Wwise CLI wrapper).

    Runs sound2wem from its own directory so it can find/create its Wwise project.
    Returns list of produced .wem file paths.
    """
    wem_out_dir.mkdir(parents=True, exist_ok=True)
    sound2wem = Path(config.sound2wem_script)

    if not sound2wem.exists():
        raise FileNotFoundError(f"sound2wem script not found: {sound2wem}")

    produced: list[Path] = []
    env = os.environ.copy()
    env["WWISEROOT"] = str(config.wwise_dir)

    with Progress(
        TextColumn("  [bold]{task.description}"),
        BarColumn(),
        MofNCompleteColumn(),
        TimeRemainingColumn(),
    ) as progress:
        task = progress.add_task("Converting OGG → WEM", total=len(ogg_files))
        for ogg in ogg_files:
            result = subprocess.run(
                ["cmd", "/c", str(sound2wem), str(ogg)],
                capture_output=True, text=True,
                cwd=str(sound2wem.parent),
                env=env,
            )
            # sound2wem outputs .wem in its own directory
            wem_candidate = sound2wem.parent / ogg.with_suffix(".wem").name
            if wem_candidate.exists():
                dest = wem_out_dir / wem_candidate.name
                shutil.move(str(wem_candidate), dest)
                produced.append(dest)
            else:
                print(f"  Warning: no .wem produced for {ogg.name}")
                if result.stderr:
                    print(f"  stderr: {result.stderr.strip()[:200]}")
            progress.advance(task)

    return produced


def pack_voice_archive(config: Config, wem_dir: Path, processed_wem_files: list[Path]) -> Path:
    """Replace original .wem files with processed ones and repack into a voice archive.

    Copies processed .wem files into the extracted voice directory tree (preserving
    depot path structure), then runs WolvenKit pack.
    Returns the directory containing the repacked .archive.
    """
    # Replace .wem files in the extraction tree with processed versions
    replaced = 0
    for wem_file in processed_wem_files:
        originals = list(wem_dir.rglob(wem_file.name))
        for orig in originals:
            if orig.suffix == ".wem":
                shutil.copy2(wem_file, orig)
                replaced += 1

    print(f"  Replaced {replaced} .wem file(s) in extraction tree")

    # Remove .Ogg files so they don't get packed into the archive
    for ogg in list(wem_dir.rglob("*.Ogg")) + list(wem_dir.rglob("*.ogg")):
        ogg.unlink()

    # Also remove non-wem files that uncook may have extracted (lipsync .anims etc)
    for f in wem_dir.rglob("*"):
        if f.is_file() and f.suffix not in (".wem",):
            f.unlink()

    # WolvenKit pack takes the top-level directory that contains the depot structure.
    # uncook creates: wem_dir/base/localization/... so we pack wem_dir directly.
    print(f"  Repacking voice archive from: {wem_dir}")
    cmd = [str(config.wolvenkit_cli), "pack", "-p", str(wem_dir)]
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        raise RuntimeError(f"WolvenKit pack failed: {result.stderr.strip()}")

    # WolvenKit places the .archive alongside the input folder
    packed_dir = wem_dir.parent
    archives = list(packed_dir.glob("*.archive"))
    if not archives:
        raise RuntimeError(f"No .archive produced in {packed_dir}")

    print(f"  Voice archive(s): {[a.name for a in archives]}")
    return packed_dir


def run_audio_pipeline(config: Config, patch_records: list) -> Path | None:
    """Full audio pipeline: extract → process → convert → repack.

    Returns the directory containing the voice .archive, or None if no
    voice lines were found for the given patch records.
    """
    voice_dir = config.work_dir / "audio"
    voice_dir.mkdir(parents=True, exist_ok=True)

    # Step A: extract voiceover maps and build stringId lookup
    print("  Extracting voiceover maps...")
    maps_dir = extract_voiceover_maps(config, voice_dir)
    voiceover_map = build_string_id_to_wem_map(maps_dir)

    if not voiceover_map:
        print("  No voiceover map entries found — skipping audio pipeline.")
        return None

    # Step B: find which .wem files correspond to patched text strings
    targets = find_wem_paths_for_records(patch_records, voiceover_map)
    if not targets:
        print("  No voice lines matched patched strings — skipping audio pipeline.")
        return None

    print(f"  Found {len(targets)} voice line(s) to process")
    depot_paths = [dp for _, dp in targets]

    # Step C: extract target .wem + .Ogg files from voice archive
    wem_dir = extract_target_wem_files(config, voice_dir, depot_paths)
    ogg_files = list(wem_dir.rglob("*.Ogg")) + list(wem_dir.rglob("*.ogg"))
    if not ogg_files:
        print("  Warning: no .Ogg files produced by uncook.")
        return None

    # Step D: process each .Ogg through monkeyplug
    processed_ogg = process_audio_with_monkeyplug(config, wem_dir, ogg_files)
    if not processed_ogg:
        print("  Warning: monkeyplug produced no output files.")
        return None

    # Step E: convert processed .Ogg → .wem
    wem_out_dir = voice_dir / "processed_wem"
    processed_wem = convert_ogg_to_wem(config, processed_ogg, wem_out_dir)
    if not processed_wem:
        print("  Warning: no .wem files produced from conversion.")
        return None

    # Step F: repack voice archive
    packed_dir = pack_voice_archive(config, wem_dir, processed_wem)
    return packed_dir


def _to_wsl_path(windows_path: Path) -> str:
    """Convert a Windows path to a WSL-compatible /mnt/... path."""
    p = str(windows_path).replace("\\", "/")
    if len(p) >= 2 and p[1] == ":":
        drive = p[0].lower()
        p = f"/mnt/{drive}/{p[3:]}"
    return p
