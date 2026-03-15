"""Tests for config.py: validation and path resolution."""

import pytest
from pathlib import Path

from cp2077_profanity.config import Config, load_config


class TestConfigValidation:
    def test_workers_range_validated_on_load(self, tmp_path):
        """Workers validation is tested via test_workers_zero_raises and test_workers_too_high_raises."""
        # Default config has valid workers
        cfg = Config()
        assert 1 <= cfg.workers <= 64

    def test_default_config(self):
        """Default Config() should have sane defaults."""
        cfg = Config()
        assert cfg.workers == 8
        assert cfg.monkeyplug_workers == 6
        assert cfg.radio_min_duration == 90
        assert cfg.whisper_model == "base"

    def test_config_from_toml(self, tmp_path):
        """Config loads correctly from a TOML file."""
        toml_content = """
[performance]
workers = 4

[profanity]
wordlist = "./words.txt"
"""
        config_file = tmp_path / "config.toml"
        config_file.write_text(toml_content)
        (tmp_path / "words.txt").write_text("test\n")

        cfg = load_config(config_file)
        assert cfg.workers == 4
        assert cfg.wordlist_path == (tmp_path / "words.txt").resolve()

    def test_workers_zero_raises(self, tmp_path):
        toml = "[performance]\nworkers = 0\n"
        config_file = tmp_path / "config.toml"
        config_file.write_text(toml)
        with pytest.raises(ValueError, match="workers"):
            load_config(config_file)

    def test_workers_too_high_raises(self, tmp_path):
        toml = "[performance]\nworkers = 100\n"
        config_file = tmp_path / "config.toml"
        config_file.write_text(toml)
        with pytest.raises(ValueError, match="workers"):
            load_config(config_file)

    def test_missing_config_file_raises(self):
        with pytest.raises(FileNotFoundError):
            load_config(Path("nonexistent.toml"))

    def test_relative_paths_resolved_from_config_dir(self, tmp_path):
        """Relative paths in TOML should resolve relative to config file location."""
        sub = tmp_path / "subdir"
        sub.mkdir()
        toml = '[profanity]\nwordlist = "../words.txt"\n'
        (sub / "config.toml").write_text(toml)
        (tmp_path / "words.txt").write_text("test\n")

        cfg = load_config(sub / "config.toml")
        assert cfg.wordlist_path == (tmp_path / "words.txt").resolve()
