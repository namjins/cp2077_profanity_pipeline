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


def load_config(config_path: Path | None = None, **overrides: str) -> Config:
    """Load configuration from a TOML file, with CLI overrides applied on top."""
    cfg = Config()

    if config_path and config_path.exists():
        with open(config_path, "rb") as f:
            data = tomllib.load(f)

        wk = data.get("wolvenkit", {})
        if "cli_path" in wk:
            cfg.wolvenkit_cli = Path(wk["cli_path"])

        paths = data.get("paths", {})
        if "game_dir" in paths:
            cfg.game_dir = Path(paths["game_dir"])
        if "work_dir" in paths:
            cfg.work_dir = Path(paths["work_dir"])
        if "output_dir" in paths:
            cfg.output_dir = Path(paths["output_dir"])

        mod = data.get("mod", {})
        if "name" in mod:
            cfg.mod_name = mod["name"]
        if "version" in mod:
            cfg.mod_version = mod["version"]
        if "description" in mod:
            cfg.mod_description = mod["description"]

        prof = data.get("profanity", {})
        if "wordlist" in prof:
            cfg.wordlist_path = Path(prof["wordlist"])

        perf = data.get("performance", {})
        if "workers" in perf:
            cfg.workers = int(perf["workers"])

    # Apply CLI overrides
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

    return cfg
