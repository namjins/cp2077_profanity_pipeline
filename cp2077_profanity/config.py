"""Configuration loading from config.toml and CLI overrides."""

from dataclasses import dataclass, field
from pathlib import Path
import tomllib


@dataclass
class Config:
    """Runtime configuration for the profanity filter pipeline."""

    wolvenkit_cli: Path = Path("WolvenKit.CLI.exe")
    game_dir: Path = Path(".")
    work_dir: Path = Path("./work")
    output_dir: Path = Path("./output")
    mod_name: str = "CP2077ProfanityFilter"
    mod_version: str = "1.0.0"
    mod_description: str = "Replaces profane words in English localization with asterisks"
    wordlist_path: Path = Path("./profanity_list.txt")
    workers: int = 8
    # Audio pipeline settings
    sound2wem_script: Path = Path("C:/Tools/sound2wem/zSound2wem.cmd")
    wwise_dir: Path = Path("C:/Audiokinetic/Wwise2019.2.15.7667")
    whisper_model: str = "base"
    monkeyplug_workers: int = 6
    # Radio pipeline settings
    radio_tracks_file: Path | None = None  # None = use bundled data/radio_tracks.json
    radio_min_duration: int = 90  # seconds; used by discover-radio to separate songs from SFX


def load_config(config_path: Path | None = None, **overrides: str) -> Config:
    """Load configuration from a TOML file, with CLI overrides applied on top.

    Relative paths in the config file are resolved relative to the directory
    containing the config file, not the current working directory.  This means
    the pipeline behaves the same regardless of where you invoke it from.
    """
    cfg = Config()

    if config_path and not config_path.exists():
        raise FileNotFoundError(f"Config file not found: {config_path}")

    if config_path and config_path.exists():
        # All relative paths in the TOML are resolved relative to the config file's
        # directory so the tool works from any working directory.
        base = config_path.resolve().parent

        def _path(raw: str) -> Path:
            p = Path(raw)
            return (base / p).resolve() if not p.is_absolute() else p

        with open(config_path, "rb") as f:
            data = tomllib.load(f)

        wk = data.get("wolvenkit", {})
        if "cli_path" in wk:
            cfg.wolvenkit_cli = _path(wk["cli_path"])

        paths = data.get("paths", {})
        if "game_dir" in paths:
            cfg.game_dir = _path(paths["game_dir"])
        if "work_dir" in paths:
            cfg.work_dir = _path(paths["work_dir"])
        if "output_dir" in paths:
            cfg.output_dir = _path(paths["output_dir"])

        mod = data.get("mod", {})
        if "name" in mod:
            cfg.mod_name = mod["name"]
        if "version" in mod:
            cfg.mod_version = mod["version"]
        if "description" in mod:
            cfg.mod_description = mod["description"]

        prof = data.get("profanity", {})
        if "wordlist" in prof:
            cfg.wordlist_path = _path(prof["wordlist"])

        perf = data.get("performance", {})
        if "workers" in perf:
            cfg.workers = int(perf["workers"])

        audio = data.get("audio", {})
        if "sound2wem_script" in audio:
            cfg.sound2wem_script = _path(audio["sound2wem_script"])
        if "wwise_dir" in audio:
            cfg.wwise_dir = _path(audio["wwise_dir"])
        if "whisper_model" in audio:
            cfg.whisper_model = audio["whisper_model"]
        if "monkeyplug_workers" in audio:
            cfg.monkeyplug_workers = int(audio["monkeyplug_workers"])

        radio = data.get("radio", {})
        if "radio_tracks_file" in radio:
            cfg.radio_tracks_file = _path(radio["radio_tracks_file"])
        if "min_duration_seconds" in radio:
            cfg.radio_min_duration = int(radio["min_duration_seconds"])

    # Apply CLI overrides (resolved relative to CWD, since they come from the shell)
    if "wolvenkit_path" in overrides and overrides["wolvenkit_path"]:
        cfg.wolvenkit_cli = Path(overrides["wolvenkit_path"])
    if "game_dir" in overrides and overrides["game_dir"]:
        cfg.game_dir = Path(overrides["game_dir"])
    if "work_dir" in overrides and overrides["work_dir"]:
        cfg.work_dir = Path(overrides["work_dir"])
    if "output_dir" in overrides and overrides["output_dir"]:
        cfg.output_dir = Path(overrides["output_dir"])
    if "wordlist" in overrides and overrides["wordlist"]:
        cfg.wordlist_path = Path(overrides["wordlist"])

    # Validate numeric ranges
    if cfg.workers < 1 or cfg.workers > 64:
        raise ValueError(f"workers must be 1-64, got {cfg.workers}")
    if cfg.monkeyplug_workers < 1 or cfg.monkeyplug_workers > 32:
        raise ValueError(f"monkeyplug_workers must be 1-32, got {cfg.monkeyplug_workers}")
    if cfg.radio_min_duration < 1:
        raise ValueError(f"radio min_duration_seconds must be >= 1, got {cfg.radio_min_duration}")

    return cfg


def validate_tool_paths(cfg: Config) -> None:
    """Validate that WolvenKit CLI and game directory exist.

    Call this only when steps that need these tools will actually run.
    Checks PATH for the CLI executable if the path is not absolute.
    """
    import shutil

    wk = cfg.wolvenkit_cli
    if not wk.exists() and not shutil.which(str(wk)):
        raise FileNotFoundError(
            f"WolvenKit CLI not found: {wk}\n"
            "Set 'cli_path' in config.toml [wolvenkit] or pass --wolvenkit-path"
        )
    if not cfg.game_dir.exists():
        raise FileNotFoundError(
            f"Game directory not found: {cfg.game_dir}\n"
            "Set 'game_dir' in config.toml [paths] or pass --game-dir"
        )


def validate_audio_tool_paths(cfg: Config) -> None:
    """Validate that audio pipeline tools (sound2wem, Wwise) exist.

    Call this before starting the audio pipeline so failures surface early
    instead of after expensive monkeyplug processing.
    """
    if not cfg.sound2wem_script.exists():
        raise FileNotFoundError(
            f"sound2wem script not found: {cfg.sound2wem_script}\n"
            "Set 'sound2wem_script' in config.toml [audio]"
        )
    if not cfg.wwise_dir.exists():
        raise FileNotFoundError(
            f"Wwise directory not found: {cfg.wwise_dir}\n"
            "Set 'wwise_dir' in config.toml [audio]"
        )
