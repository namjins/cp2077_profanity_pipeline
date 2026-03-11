"""CLI entry point for the CP2077 Profanity Filter pipeline."""

from pathlib import Path
from typing import Optional

import typer
from rich import print as rprint

from .audio import run_audio_pipeline
from .config import load_config
from .extractor import collect_locale_jsons, extract_archives
from .patcher import patch_all
from .packager import package_mod, write_summary
from .repacker import repack_archives
from .scanner import scan_all

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
        False, "--skip-audio", help="Skip the audio pipeline (text-only mod)"
    ),
    skip_text_repack: bool = typer.Option(
        False, "--skip-text-repack", help="Skip text archive repacking (use previously built text archive)"
    ),
) -> None:
    """Run the full profanity filter pipeline: extract → scan → patch → repack → package."""
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
    config = load_config(config_file, **overrides)

    # Step 1: Extract
    if skip_extract:
        rprint("[yellow]Skipping extraction (using existing files)[/yellow]")
        extract_dir = config.work_dir / "extracted"
        if not extract_dir.exists():
            rprint("[red]Error: extracted directory not found. Run without --skip-extract first.[/red]")
            raise typer.Exit(1)
    else:
        rprint("[bold]Step 1/6: Extracting archives...[/bold]")
        extract_dir = extract_archives(config)

    # Collect locale JSONs
    json_files = collect_locale_jsons(extract_dir)
    rprint(f"  Found {len(json_files)} English locale file(s)")

    if not json_files:
        rprint("[red]No English locale files found. Check your game directory path.[/red]")
        raise typer.Exit(1)

    # Step 2: Scan
    rprint("[bold]Step 2/6: Scanning for profanity...[/bold]")
    hits = scan_all(json_files, config.wordlist_path)
    rprint(f"  Found {len(hits)} profanity match(es)")

    if not hits:
        rprint("[green]No profanity found! Nothing to do.[/green]")
        raise typer.Exit(0)

    if scan_only:
        rprint("[yellow]Scan-only mode: skipping patch, repack, and package steps.[/yellow]")
        # Still write a summary
        unique_words = {h.matched_word.lower() for h in hits}
        files_with_hits = len({str(h.filepath) for h in hits})
        write_summary(config, files_with_hits, len(hits), unique_words)
        rprint(f"  Summary written to {config.output_dir / 'summary.txt'}")
        raise typer.Exit(0)

    # Step 3: Patch
    rprint("[bold]Step 3/6: Patching files...[/bold]")
    log_path = config.output_dir / "patch_log.csv"
    records = patch_all(json_files, config.wordlist_path, log_path)
    rprint(f"  Patched {len(records)} string(s), log written to {log_path}")

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
        rprint("[bold]Step 4/6: Repacking text archive...[/bold]")
        packed_dir = repack_archives(config, records)

    # Step 5: Audio pipeline
    voice_packed_dir = None
    if skip_audio:
        rprint("[yellow]Skipping audio pipeline.[/yellow]")
    else:
        rprint("[bold]Step 5/6: Processing audio...[/bold]")
        try:
            voice_packed_dir = run_audio_pipeline(config, records)
            if voice_packed_dir:
                rprint(f"  Voice archive(s) repacked to: {voice_packed_dir}")
            else:
                rprint("  No matching voice lines found — audio step skipped.")
        except Exception as e:
            rprint(f"[yellow]  Audio pipeline error (continuing without audio): {e}[/yellow]")

    # Step 6: Package
    rprint("[bold]Step 6/6: Packaging mod...[/bold]")
    zip_path = package_mod(config, packed_dir, voice_packed_dir, records)
    rprint(f"[green bold]Done! Mod package: {zip_path}[/green bold]")


def main() -> None:
    """Entry point for the CLI."""
    app()


if __name__ == "__main__":
    main()
