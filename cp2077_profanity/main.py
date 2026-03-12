"""CLI entry point for the CP2077 Profanity Filter pipeline."""

import logging
import shutil
import subprocess
import time
import traceback
from datetime import datetime
from pathlib import Path
from typing import Optional

import typer
from rich import print as rprint

from .audio import run_audio_pipeline
from .config import load_config, validate_tool_paths
from .discovery import discover_radio_tracks
from .extractor import collect_locale_jsons, extract_archives
from .patcher import load_patch_log, patch_all
from .packager import package_mod, write_summary
from .radio import run_radio_pipeline
from .repacker import repack_archives

logger = logging.getLogger(__name__)


def _setup_logging(output_dir: Path, level: int = logging.INFO) -> str:
    """Configure file logging for pipeline runs. Returns the run ID."""
    run_id = datetime.now().strftime("%Y%m%d_%H%M%S")
    output_dir.mkdir(parents=True, exist_ok=True)
    log_path = output_dir / f"pipeline_{run_id}.log"

    root = logging.getLogger("cp2077_profanity")
    root.setLevel(level)

    # File handler: detailed log for post-run forensics
    fh = logging.FileHandler(log_path, encoding="utf-8")
    fh.setLevel(level)
    fh.setFormatter(logging.Formatter(
        "%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    ))
    root.addHandler(fh)

    logger.info("Pipeline run %s started", run_id)
    logger.info("Log file: %s", log_path)
    return run_id


app = typer.Typer(
    name="cp2077-profanity",
    help="CP2077 Profanity Filter Mod - CLI toolchain for filtering profanity from Cyberpunk 2077 localization files.",
)


@app.command()
def run(
    config_file: Optional[Path] = typer.Option(
        None, "--config", "-c", help="Path to config.toml"
    ),
    wolvenkit_path: Optional[str] = typer.Option(
        None, "--wolvenkit-path", help="Path to WolvenKit CLI executable"
    ),
    game_dir: Optional[str] = typer.Option(
        None, "--game-dir", help="Path to Cyberpunk 2077 installation"
    ),
    work_dir: Optional[str] = typer.Option(
        None, "--work-dir", help="Working directory for intermediate files"
    ),
    output_dir: Optional[str] = typer.Option(
        None, "--output-dir", help="Output directory for the final mod package"
    ),
    wordlist: Optional[str] = typer.Option(
        None, "--wordlist", help="Path to profanity wordlist file"
    ),
    scan_only: bool = typer.Option(
        False, "--scan-only", help="Only scan for profanity, do not patch or package"
    ),
    skip_extract: bool = typer.Option(
        False, "--skip-extract", help="Skip extraction (use previously extracted files)"
    ),
    skip_repack: bool = typer.Option(
        False, "--skip-repack", help="Skip repacking and packaging (patch files only)"
    ),
    skip_audio: bool = typer.Option(
        False, "--skip-audio", help="Skip the voice audio pipeline"
    ),
    skip_radio: bool = typer.Option(
        True, "--skip-radio/--no-skip-radio", help="Skip the radio music pipeline (default: skip)"
    ),
    skip_text_repack: bool = typer.Option(
        False, "--skip-text-repack", help="Skip text archive repacking (use previously built text archive)"
    ),
    clean: bool = typer.Option(
        False, "--clean", help="Delete work directory before starting (forces full fresh run)"
    ),
) -> None:
    """Run the full profanity filter pipeline: extract -> scan -> patch -> repack -> package."""
    pipeline_start = time.time()
    overrides = {
        k: v
        for k, v in {
            "wolvenkit_path": wolvenkit_path,
            "game_dir": game_dir,
            "work_dir": work_dir,
            "output_dir": output_dir,
            "wordlist": wordlist,
        }.items()
        if v is not None
    }
    try:
        config = load_config(config_file, **overrides)
    except FileNotFoundError as e:
        rprint(f"[red]Error:[/red] {e}")
        raise typer.Exit(1)

    run_id = _setup_logging(config.output_dir)
    logger.info("Config: game_dir=%s, work_dir=%s, output_dir=%s, workers=%d",
                config.game_dir, config.work_dir, config.output_dir, config.workers)
    logger.info("Flags: skip_extract=%s, skip_audio=%s, skip_radio=%s, skip_text_repack=%s, "
                "scan_only=%s, clean=%s",
                skip_extract, skip_audio, skip_radio, skip_text_repack, scan_only, clean)

    log_path = config.output_dir / "patch_log.csv"

    # --clean: wipe work directory to force a fully fresh run
    if clean:
        if config.work_dir.exists():
            rprint(f"[yellow]--clean: deleting work directory {config.work_dir}[/yellow]")
            try:
                shutil.rmtree(config.work_dir)
            except OSError as e:
                rprint(f"[red]Error: could not delete work directory: {e}[/red]")
                rprint("  Close any programs that may have files open in the work directory and retry.")
                raise typer.Exit(1)

    # P0-1 fix: --skip-extract without --skip-text-repack will re-scan already-patched
    # files and produce an incomplete patch_log (missing entries that were patched in
    # prior runs). Force --skip-text-repack so the existing log is used instead.
    if skip_extract and not skip_text_repack:
        if log_path.exists():
            rprint("[yellow]--skip-extract implies --skip-text-repack (extracted files may "
                   "already be patched). Using existing patch log.[/yellow]")
            skip_text_repack = True
        else:
            rprint("[red]Error: --skip-extract requires an existing patch log at "
                   f"{log_path}, but none was found. Run a full pipeline first, or "
                   "use --clean for a fresh run.[/red]")
            raise typer.Exit(1)

    # Validate tool paths only when steps that need them will run
    needs_wolvenkit = not (skip_text_repack and skip_audio and skip_radio)
    if needs_wolvenkit:
        try:
            validate_tool_paths(config)
        except FileNotFoundError as e:
            rprint(f"[red]Error:[/red] {e}")
            raise typer.Exit(1)

    # Validate audio tool paths early when audio pipeline will run
    if not skip_audio:
        try:
            from .config import validate_audio_tool_paths
            validate_audio_tool_paths(config)
        except FileNotFoundError as e:
            rprint(f"[red]Error:[/red] {e}")
            raise typer.Exit(1)

    # Wordlist is only needed when scanning/patching or running audio/radio
    needs_wordlist = not skip_text_repack or not skip_audio or not skip_radio
    if needs_wordlist and not config.wordlist_path.exists():
        rprint(f"[red]Error: Wordlist not found: {config.wordlist_path}[/red]")
        raise typer.Exit(1)

    if skip_text_repack:
        # Files are already patched from a previous run -- load records from the existing log
        rprint("[yellow]Loading patch records from existing patch log (skip-text-repack mode).[/yellow]")
        try:
            records = load_patch_log(log_path)
        except FileNotFoundError:
            rprint(f"[red]Error: patch log not found at {log_path}. Run without --skip-text-repack first.[/red]")
            raise typer.Exit(1)
        except (ValueError, KeyError) as e:
            rprint(f"[red]Error: patch log is malformed: {e}[/red]")
            raise typer.Exit(1)
        rprint(f"  Loaded {len(records)} patch record(s) from log")
        records_with_voice = sum(1 for r in records if r.string_id)
        rprint(f"  {records_with_voice} record(s) have voice string IDs")
    else:
        # Step 1: Extract
        step_start = time.time()
        if skip_extract:
            rprint("[yellow]Skipping extraction (using existing files)[/yellow]")
            extract_dir = config.work_dir / "extracted"
            if not extract_dir.exists():
                rprint("[red]Error: extracted directory not found. Run without --skip-extract first.[/red]")
                raise typer.Exit(1)
        else:
            rprint("[bold]Step 1/7: Extracting archives...[/bold]")
            logger.info("Step 1: Extracting archives")
            extract_dir = extract_archives(config)
            elapsed = time.time() - step_start
            logger.info("Step 1 complete: extraction in %.1fs", elapsed)
            rprint(f"  Extraction completed in {elapsed:.1f}s")

        # Collect locale JSONs and validate extraction completeness
        json_files = collect_locale_jsons(extract_dir)
        rprint(f"  Found {len(json_files)} English locale file(s)")

        if not json_files:
            rprint("[red]No English locale files found. Check your game directory path.[/red]")
            raise typer.Exit(1)

        # Sanity check: CP2077 base game has ~200+ locale files; warn if suspiciously low
        if len(json_files) < 50:
            rprint(f"[yellow]  Warning: only {len(json_files)} locale file(s) found -- "
                   "this is unusually low. Some files may have failed to extract or convert. "
                   "Check WolvenKit output above for errors.[/yellow]")

        if scan_only:
            from .scanner import scan_all
            rprint("[bold]Step 2/7: Scanning for profanity...[/bold]")
            hits = scan_all(json_files, config.wordlist_path)
            rprint(f"  Found {len(hits)} profanity match(es)")
            if not hits:
                rprint("[green]No profanity found! Nothing to do.[/green]")
                raise typer.Exit(0)
            rprint("[yellow]Scan-only mode: skipping patch, repack, and package steps.[/yellow]")
            unique_words = {h.matched_word.lower() for h in hits}
            files_with_hits = len({str(h.filepath) for h in hits})
            write_summary(config, files_with_hits, len(hits), unique_words)
            rprint(f"  Summary written to {config.output_dir / 'summary.txt'}")
            raise typer.Exit(0)

        # Step 2+3: Scan & Patch (single authoritative pass)
        step_start = time.time()
        rprint("[bold]Step 2-3/7: Scanning and patching files...[/bold]")
        logger.info("Steps 2-3: Scanning and patching %d file(s)", len(json_files))
        records = patch_all(json_files, config.wordlist_path, log_path)
        if not records:
            logger.info("No profanity found, exiting")
            rprint("[green]No profanity found! Nothing to do.[/green]")
            raise typer.Exit(0)
        records_with_voice = sum(1 for r in records if r.string_id)
        elapsed = time.time() - step_start
        unique_words = {w.lower() for r in records for w in r.words_replaced}
        logger.info("Steps 2-3 complete: %d strings patched, %d unique words, "
                     "%d with voice IDs (%.1fs)",
                     len(records), len(unique_words), records_with_voice, elapsed)
        rprint(f"  Patched {len(records)} string(s) in {elapsed:.1f}s")
        rprint(f"  {records_with_voice} record(s) have voice string IDs")
        rprint(f"  Patch log written to {log_path}")

    if skip_repack:
        rprint("[yellow]Skipping repack and package steps.[/yellow]")
        raise typer.Exit(0)

    # Step 4: Repack text archive
    if skip_text_repack:
        rprint("[yellow]Skipping text repack (using existing archive).[/yellow]")
        packed_dir = config.work_dir
        if not list(packed_dir.glob("*.archive")):
            rprint("[red]Error: no .archive found in work dir. Run without --skip-text-repack first.[/red]")
            raise typer.Exit(1)
    else:
        step_start = time.time()
        rprint("[bold]Step 4/7: Repacking text archive...[/bold]")
        logger.info("Step 4: Repacking text archive")
        packed_dir = repack_archives(config, records)
        elapsed = time.time() - step_start
        logger.info("Step 4 complete: text repack in %.1fs", elapsed)
        rprint(f"  Text repack completed in {elapsed:.1f}s")

    # Step 5: Voice audio pipeline
    voice_packed_dir = None
    if skip_audio:
        rprint("[yellow]Skipping voice audio pipeline.[/yellow]")
    else:
        step_start = time.time()
        rprint("[bold]Step 5/7: Processing voice audio...[/bold]")
        logger.info("Step 5: Processing voice audio")
        try:
            voice_packed_dir = run_audio_pipeline(config, records)
            if voice_packed_dir:
                logger.info("Step 5 complete: voice archive at %s", voice_packed_dir)
                rprint(f"  Voice archive(s) repacked to: {voice_packed_dir}")
            else:
                logger.info("Step 5 complete: no voice archive produced (no matches)")
                rprint("  Voice pipeline produced no archive (no map entries or no matched voice lines).")
        except (RuntimeError, FileNotFoundError, subprocess.CalledProcessError) as e:
            logger.error("Step 5 failed (continuing without audio): %s", e, exc_info=True)
            rprint(f"[red]  Voice audio pipeline error (continuing without audio): {e}[/red]")
            rprint(f"[dim]{traceback.format_exc()}[/dim]")
        except Exception as e:
            logger.error("Step 5 unexpected failure (continuing without audio): %s", e, exc_info=True)
            rprint(f"[red]  Voice audio pipeline unexpected error: {e}[/red]")
            rprint(f"[dim]{traceback.format_exc()}[/dim]")
            rprint("[red]  This is an unexpected failure. The mod will be packaged without voice audio.[/red]")
        rprint(f"  Voice audio step completed in {time.time() - step_start:.1f}s")

    # Step 6: Radio music pipeline
    radio_packed_dir = None
    if skip_radio:
        rprint("[yellow]Skipping radio music pipeline.[/yellow]")
    else:
        step_start = time.time()
        rprint("[bold]Step 6/7: Processing radio music...[/bold]")
        logger.info("Step 6: Processing radio music")
        try:
            radio_packed_dir = run_radio_pipeline(config)
            if radio_packed_dir:
                logger.info("Step 6 complete: radio archive at %s", radio_packed_dir)
                rprint(f"  Radio archive(s) repacked to: {radio_packed_dir}")
            else:
                logger.info("Step 6 complete: no radio output (no tracks or matches)")
                rprint("  Radio pipeline produced no output (no tracks configured or no matches).")
        except (RuntimeError, FileNotFoundError, subprocess.CalledProcessError) as e:
            logger.error("Step 6 failed (continuing without radio): %s", e, exc_info=True)
            rprint(f"[red]  Radio pipeline error (continuing without radio): {e}[/red]")
            rprint(f"[dim]{traceback.format_exc()}[/dim]")
        except Exception as e:
            logger.error("Step 6 unexpected failure (continuing without radio): %s", e, exc_info=True)
            rprint(f"[red]  Radio pipeline unexpected error: {e}[/red]")
            rprint(f"[dim]{traceback.format_exc()}[/dim]")
            rprint("[red]  This is an unexpected failure. The mod will be packaged without radio audio.[/red]")
        rprint(f"  Radio step completed in {time.time() - step_start:.1f}s")

    # Step 7: Package
    rprint("[bold]Step 7/7: Packaging mod...[/bold]")
    logger.info("Step 7: Packaging mod")
    zip_path = package_mod(config, packed_dir, voice_packed_dir, radio_packed_dir, records)
    elapsed = time.time() - pipeline_start
    logger.info("Pipeline complete: %s (%.1fs total, run_id=%s)", zip_path, elapsed, run_id)
    rprint(f"[green bold]Done! Mod package: {zip_path} (total: {elapsed:.1f}s)[/green bold]")


@app.command("discover-radio")
def discover_radio(
    config_file: Optional[Path] = typer.Option(
        None, "--config", "-c", help="Path to config.toml"
    ),
    output: Optional[Path] = typer.Option(
        None, "--output", "-o",
        help="Output path for radio_tracks.json (default: ./radio_tracks.json)",
    ),
    min_duration: Optional[int] = typer.Option(
        None, "--min-duration",
        help="Minimum track duration in seconds to classify as a song (default: from config, 60)",
    ),
    keep_extracted: bool = typer.Option(
        False, "--keep-extracted",
        help="Keep extracted .Ogg files after discovery (default: delete them)",
    ),
) -> None:
    """Discover radio track hashes from audio_2_soundbanks.archive by duration filtering.

    Extracts all audio from the radio soundbank, measures each file's duration via ffprobe,
    and writes a radio_tracks.json containing only files long enough to be songs.

    After running, set radio_tracks_file in config.toml [radio] to the output path,
    then re-run the main pipeline to process those tracks.
    """
    try:
        config = load_config(config_file)
    except FileNotFoundError as e:
        rprint(f"[red]Error:[/red] {e}")
        raise typer.Exit(1)

    try:
        validate_tool_paths(config)
    except FileNotFoundError as e:
        rprint(f"[red]Error:[/red] {e}")
        raise typer.Exit(1)

    out_path = output or Path("radio_tracks.json")
    duration_threshold = min_duration if min_duration is not None else config.radio_min_duration

    rprint(f"[bold]Discovering radio tracks (duration >= {duration_threshold}s)...[/bold]")
    try:
        count = discover_radio_tracks(config, out_path, duration_threshold, keep_extracted)
    except FileNotFoundError as e:
        rprint(f"[red]Error:[/red] {e}")
        raise typer.Exit(1)

    if count == 0:
        rprint("[yellow]No tracks found. Try lowering --min-duration.[/yellow]")
        raise typer.Exit(1)

    rprint(f"[green bold]Done! {count} track(s) written to {out_path}[/green bold]")
    rprint(f"  Next: set [bold]radio_tracks_file = \"{out_path}\"[/bold] in config.toml [radio]")


def main() -> None:
    """Entry point for the CLI."""
    app()


if __name__ == "__main__":
    main()


