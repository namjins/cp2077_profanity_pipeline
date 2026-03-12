"""Audio pipeline: extract, process, and repack voice lines containing profanity."""

import csv
import json
import re
import shutil
import subprocess
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

from rich.progress import BarColumn, MofNCompleteColumn, Progress, SpinnerColumn, TextColumn, TimeRemainingColumn

from .config import Config
from .scanner import _extract_entries
from .wsl_utils import check_monkeyplug, convert_ogg_to_wem, run_monkeyplug_on_file, to_wsl_path


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
            result = subprocess.run(cmd, capture_output=True, text=True)
            if result.returncode != 0:
                print(f"  Warning: unbundle failed for {map_name} in {archive.name}")
                if result.stderr:
                    print(f"  stderr: {result.stderr.strip()[:500]}")

    # Deserialize CR2W .json files to .json.json in parallel
    cr2w_files = [p for p in maps_dir.rglob("*.json") if not p.name.endswith(".json.json")]
    if cr2w_files:
        def _convert_map(f: Path) -> None:
            result = subprocess.run(
                [str(config.wolvenkit_cli), "cr2w", "-s", str(f)],
                capture_output=True, text=True,
            )
            if result.returncode != 0:
                print(f"  Warning: cr2w conversion failed for {f.name}")

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
    """Parse voiceover map files and build a stringId -> {female, male} wem path lookup.

    Returns a dict mapping stringId (str) to {"female": depot_path, "male": depot_path}.
    Depot paths use backslash notation, e.g. 'base\\localization\\en-us\\vo\\file.wem'.
    """
    lookup: dict[str, dict[str, str]] = {}

    json_json_files = list(maps_dir.rglob("*.json.json"))
    if not json_json_files:
        print("  Warning: no voiceover map .json.json files found.")
        return lookup

    parse_errors = 0
    for map_file in json_json_files:
        try:
            with open(map_file, encoding="utf-8") as f:
                data = json.load(f)
        except (json.JSONDecodeError, UnicodeDecodeError) as e:
            print(f"  Warning: failed to parse {map_file.name}: {e}")
            parse_errors += 1
            continue

        entries = _extract_entries(data)
        if not entries:
            print(f"  Warning: no entries found in voiceover map {map_file.name}")

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

    print(f"  Built voiceover map: {len(lookup)} entries from {len(json_json_files)} file(s)"
          + (f" ({parse_errors} parse error(s))" if parse_errors else ""))
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

    no_string_id = 0
    not_in_map = 0
    matched = 0

    for record in patch_records:
        string_id = str(record.string_id or "").strip()
        if not string_id:
            no_string_id += 1
            continue
        if string_id not in voiceover_map:
            not_in_map += 1
            continue

        matched += 1
        paths = voiceover_map[string_id]
        for depot_path in (paths.get("female", ""), paths.get("male", "")):
            if depot_path and depot_path not in seen:
                seen.add(depot_path)
                targets.append((string_id, depot_path))

    total = len(patch_records)
    print(f"  Voice line matching: {matched} matched, {no_string_id} without string_id, "
          f"{not_in_map} string_id not in voiceover map (of {total} patch records)")

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
    print(f"  Extracting {len(stems)} target .wem file(s) from {len(archives)} archive(s)...")

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
        result = subprocess.run(cmd, capture_output=True, text=True)
        if result.returncode != 0 and result.stderr:
            print(f"  Warning: uncook failed for batch in {archive.name}: {result.stderr.strip()[:500]}")

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

    # Log extraction results
    extracted_wem = list(wem_dir.rglob("*.wem"))
    extracted_ogg = list(wem_dir.rglob("*.Ogg")) + list(wem_dir.rglob("*.ogg"))
    print(f"  Extracted {len(extracted_wem)} .wem file(s) and {len(extracted_ogg)} .Ogg file(s)")

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
    check_monkeyplug()

    processed_dir = wem_dir.parent / "processed_ogg"
    processed_dir.mkdir(parents=True, exist_ok=True)

    wsl_wordlist = to_wsl_path(config.wordlist_path)

    def _process_one(ogg: Path) -> Path | None:
        out_file = processed_dir / ogg.name
        # Voice lines are mono; pass channels=1 so monkeyplug outputs mono .Ogg
        return run_monkeyplug_on_file(ogg, out_file, wsl_wordlist, config.whisper_model, channels=1)

    # Collisions are possible when base/ep1 contain files with the same basename.
    # Deduplicate by filename because downstream replacement is filename-based.
    unique_ogg_by_name: dict[str, Path] = {}
    duplicate_count = 0
    for ogg in sorted(ogg_files, key=lambda p: str(p)):
        if ogg.name in unique_ogg_by_name:
            duplicate_count += 1
            continue
        unique_ogg_by_name[ogg.name] = ogg

    selected_ogg_files = list(unique_ogg_by_name.values())
    if duplicate_count:
        print(f"  Deduplicated {duplicate_count} duplicate .Ogg input(s) by basename")

    failed_count = 0
    processed: list[Path] = []
    start_time = time.time()
    with Progress(
        TextColumn("  [bold]{task.description}"),
        BarColumn(),
        MofNCompleteColumn(),
        TimeRemainingColumn(),
    ) as progress:
        task = progress.add_task(
            f"Processing audio ({config.monkeyplug_workers} workers)",
            total=len(selected_ogg_files),
        )
        with ThreadPoolExecutor(max_workers=config.monkeyplug_workers) as executor:
            futures = {executor.submit(_process_one, ogg): ogg for ogg in selected_ogg_files}
            for future in as_completed(futures):
                result = future.result()
                if result:
                    processed.append(result)
                else:
                    failed_count += 1
                progress.advance(task)

    elapsed = time.time() - start_time
    print(f"  monkeyplug: {len(processed)} succeeded, {failed_count} failed "
          f"(of {len(selected_ogg_files)} files, {elapsed:.1f}s)")

    # Write audio processing audit log
    audit_path = wem_dir.parent / "audio_processing_log.csv"
    processed_names = {p.name for p in processed}
    with open(audit_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(["filename", "status"])
        for ogg in selected_ogg_files:
            status = "processed" if ogg.name in processed_names else "failed"
            writer.writerow([ogg.name, status])
    print(f"  Audio processing log written to {audit_path}")

    return processed


def pack_voice_archive(config: Config, wem_dir: Path, processed_wem_files: list[Path]) -> Path:
    """Replace original .wem files with processed ones and repack into a voice archive.

    Copies processed .wem files into the extracted voice directory tree (preserving
    depot path structure), then runs WolvenKit pack.
    Returns the directory containing the repacked .archive.
    """
    # Build a filename -> path lookup once to avoid an rglob call per processed file
    wem_lookup: dict[str, list[Path]] = {}
    for f in wem_dir.rglob("*.wem"):
        wem_lookup.setdefault(f.name, []).append(f)

    # Replace .wem files in the extraction tree with processed versions
    replaced = 0
    not_found = 0
    with Progress(
        TextColumn("  [bold]{task.description}"),
        BarColumn(),
        MofNCompleteColumn(),
        TimeRemainingColumn(),
    ) as progress:
        task = progress.add_task("Replacing voice .wem files", total=len(processed_wem_files))
        for wem_file in processed_wem_files:
            matches = wem_lookup.get(wem_file.name, [])
            if not matches:
                print(f"  Warning: no original .wem found for processed file {wem_file.name}")
                not_found += 1
            for orig in matches:
                shutil.copy2(wem_file, orig)
                replaced += 1
            progress.advance(task)

    print(f"  Replaced {replaced} .wem file(s) in extraction tree"
          + (f" ({not_found} processed file(s) had no match)" if not_found else ""))

    # Remove non-.wem files (Ogg conversions, lipsync .anims, etc.) before packing.
    # Count removals for logging.
    removed = 0
    for f in wem_dir.rglob("*"):
        if f.is_file() and f.suffix != ".wem":
            f.unlink(missing_ok=True)
            removed += 1
    if removed:
        print(f"  Cleaned {removed} non-.wem file(s) from extraction tree")

    # WolvenKit pack takes the top-level directory that contains the depot structure.
    # uncook creates: wem_dir/base/localization/... so we pack wem_dir directly.
    print(f"  Repacking voice archive from: {wem_dir}")
    cmd = [str(config.wolvenkit_cli), "pack", "-p", str(wem_dir)]
    with Progress(
        TextColumn("  [bold]{task.description}"),
        SpinnerColumn(),
        TextColumn("{task.completed} file(s) packed"),
    ) as progress:
        task = progress.add_task("Packing voice archive", total=None)
        with subprocess.Popen(
            cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True
        ) as proc:
            for line in iter(proc.stdout.readline, ""):
                if line.strip():
                    progress.advance(task)
            proc.wait()
        if proc.returncode != 0:
            raise RuntimeError(f"WolvenKit pack failed (exit {proc.returncode})")

    # WolvenKit places the .archive alongside the input folder
    packed_dir = wem_dir.parent
    archives = list(packed_dir.glob("*.archive"))
    if not archives:
        raise RuntimeError(f"No .archive produced in {packed_dir}")

    for a in archives:
        size_mb = a.stat().st_size / (1024 * 1024)
        print(f"  Voice archive: {a.name} ({size_mb:.1f} MB)")
    return packed_dir


def run_audio_pipeline(config: Config, patch_records: list) -> Path | None:
    """Full audio pipeline: extract -> process -> convert -> repack.

    Returns the directory containing the voice .archive, or None if no
    voice lines were found for the given patch records.
    """
    pipeline_start = time.time()
    voice_dir = config.work_dir / "audio"
    voice_dir.mkdir(parents=True, exist_ok=True)

    # Step A: extract voiceover maps and build stringId lookup
    maps_dir = voice_dir / "voiceover_maps"
    existing_maps = list(maps_dir.rglob("*.json.json")) if maps_dir.exists() else []
    if existing_maps:
        print(f"  Reusing {len(existing_maps)} existing voiceover map(s)")
    else:
        print("  Extracting voiceover maps...")
        maps_dir = extract_voiceover_maps(config, voice_dir)
    voiceover_map = build_string_id_to_wem_map(maps_dir)

    if not voiceover_map:
        print("  No voiceover map entries found -- skipping audio pipeline.")
        return None

    # Step B: find which .wem files correspond to patched text strings
    targets = find_wem_paths_for_records(patch_records, voiceover_map)
    if not targets:
        print("  No voice lines matched patched strings -- skipping audio pipeline.")
        return None

    print(f"  Found {len(targets)} voice line(s) to process ({len(set(dp for _, dp in targets))} unique depot paths)")
    depot_paths = [dp for _, dp in targets]

    # Step C: extract target .wem + .Ogg files from voice archive
    wem_dir = extract_target_wem_files(config, voice_dir, depot_paths)
    ogg_files = list(wem_dir.rglob("*.Ogg")) + list(wem_dir.rglob("*.ogg"))
    if not ogg_files:
        raise RuntimeError("WolvenKit uncook produced no .Ogg files for matched voice lines.")

    # Step D: process each .Ogg through monkeyplug
    processed_ogg = process_audio_with_monkeyplug(config, wem_dir, ogg_files)
    if not processed_ogg:
        raise RuntimeError("monkeyplug produced no output files for matched voice lines.")

    # Step E: convert processed .Ogg -> .wem
    wem_out_dir = voice_dir / "processed_wem"
    processed_wem = convert_ogg_to_wem(config, processed_ogg, wem_out_dir)
    if not processed_wem:
        raise RuntimeError("sound2wem produced no .wem files from processed voice audio.")

    # Step F: repack voice archive
    packed_dir = pack_voice_archive(config, wem_dir, processed_wem)
    elapsed = time.time() - pipeline_start
    print(f"  Audio pipeline completed in {elapsed:.1f}s")
    return packed_dir
