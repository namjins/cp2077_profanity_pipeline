"""Shared WSL utilities for audio pipelines (voice and radio)."""

import shlex
import subprocess
from pathlib import Path

from .config import Config


def to_wsl_path(windows_path: Path) -> str:
    """Convert a Windows path to a WSL-compatible /mnt/... path."""
    p = str(windows_path).replace("\\", "/")
    if len(p) >= 2 and p[1] == ":":
        drive = p[0].lower()
        p = f"/mnt/{drive}/{p[3:]}"
    return p


def check_monkeyplug() -> None:
    """Verify monkeyplug is installed and importable in WSL; raise with fix instructions if not."""
    which_result = subprocess.run(
        ["wsl", "bash", "-lc", "command -v monkeyplug"],
        capture_output=True, text=True,
    )
    if which_result.returncode != 0:
        raise RuntimeError(
            "monkeyplug is not installed in WSL.\n"
            "Install with: wsl bash -lc \"pipx install monkeyplug\""
        )

    # Trigger its import chain by invoking with no args.
    # If it complains about missing arguments, the import succeeded.
    # If there's a Traceback/ModuleNotFoundError, a dependency is missing.
    result = subprocess.run(
        ["wsl", "bash", "-lc", "monkeyplug 2>&1"],
        capture_output=True, text=True,
    )
    combined = (result.stdout + result.stderr).strip()
    if "Traceback" in combined or "ModuleNotFoundError" in combined:
        missing = None
        for line in combined.splitlines():
            if "ModuleNotFoundError: No module named" in line:
                missing = line.split("'")[1]
                break
        if missing:
            raise RuntimeError(
                f"monkeyplug is missing a dependency in WSL: '{missing}'\n"
                f"Fix with: wsl bash -lc \"pipx inject monkeyplug {missing}\""
            )
        raise RuntimeError(
            f"monkeyplug import failed in WSL:\n{combined}\n\n"
            "Try reinstalling: wsl bash -lc \"pipx reinstall monkeyplug\""
        )


def detect_channels(ogg_path: Path) -> int:
    """Detect the number of audio channels in an .Ogg file via ffprobe in WSL.

    Returns 1 (mono) or 2 (stereo). Falls back to 2 if ffprobe is unavailable
    or the file cannot be probed.
    """
    wsl_path = shlex.quote(to_wsl_path(ogg_path))
    cmd = [
        "wsl", "bash", "-lc",
        f"ffprobe -v error -select_streams a:0 -show_entries stream=channels "
        f"-of csv=p=0 {wsl_path} 2>/dev/null",
    ]
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=15)
        if result.returncode == 0:
            channels = int(result.stdout.strip())
            if channels in (1, 2):
                return channels
    except (subprocess.TimeoutExpired, ValueError, OSError):
        pass
    return 2  # safe default


def run_monkeyplug_on_file(
    ogg: Path,
    out_file: Path,
    wsl_wordlist: str,
    whisper_model: str,
    channels: int,
) -> Path | None:
    """Run monkeyplug on a single .Ogg file.

    wsl_wordlist must already be a WSL path string (e.g. /mnt/c/...).
    channels: 1 for mono, 2 for stereo (passed as -c flag).
    Returns out_file on success, None on failure.
    """
    # Skip if already processed (allows resuming interrupted runs)
    if out_file.exists() and out_file.stat().st_size > 0:
        return out_file

    wsl_input = shlex.quote(to_wsl_path(ogg))
    wsl_output = shlex.quote(to_wsl_path(out_file))
    quoted_wordlist = shlex.quote(wsl_wordlist)
    monkeyplug_args = (
        f"monkeyplug"
        f" -i {wsl_input}"
        f" -o {wsl_output}"
        f" -w {quoted_wordlist}"
        f" -c {channels}"
        f" -m whisper"
        f" --whisper-model-name {whisper_model}"
        f" -b false"
        f" --force true"
    )
    cmd = ["wsl", "bash", "-lc", monkeyplug_args]
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        print(f"  Warning: monkeyplug failed for {ogg.name}")
        if result.stderr:
            print(f"  stderr: {result.stderr.strip()[:200]}")
        return None
    return out_file if out_file.exists() else None


def convert_ogg_to_wem(config: Config, ogg_files: list[Path], wem_out_dir: Path) -> list[Path]:
    """Convert processed .Ogg files back to .wem using sound2wem (Wwise CLI wrapper).

    Runs sound2wem from its own directory so it can find/create its Wwise project.
    Returns list of produced .wem file paths.
    """
    import os
    import shutil

    from rich.progress import BarColumn, MofNCompleteColumn, Progress, TextColumn, TimeRemainingColumn

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
