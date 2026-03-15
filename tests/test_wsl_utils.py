"""Tests for wsl_utils.py: path conversion, basename collision handling, and batching."""

import pytest
from pathlib import Path
from unittest.mock import patch, MagicMock
from collections import defaultdict

from cp2077_profanity.wsl_utils import to_wsl_path


# -- to_wsl_path ----------------------------------------------------------

class TestToWslPath:
    def test_c_drive(self, tmp_path):
        """Basic C: drive conversion."""
        # Use a real resolvable path for testing
        result = to_wsl_path(tmp_path)
        assert result.startswith("/mnt/")
        assert "\\" not in result

    def test_forward_slashes(self, tmp_path):
        result = to_wsl_path(tmp_path)
        assert "\\" not in result

    def test_unc_path_raises(self):
        """UNC paths should be rejected with clear error."""
        with pytest.raises(ValueError, match="UNC"):
            # Mock resolve to return a UNC path
            mock_path = MagicMock(spec=Path)
            mock_path.resolve.return_value = Path("//server/share/file.txt")
            to_wsl_path(mock_path)


# -- Basename collision and rounds logic -----------------------------------
# These tests verify the batching logic from convert_ogg_to_wem without
# calling external tools. We extract and test the pure logic.

class TestBasenameGrouping:
    """Test the basename grouping and rounds logic used in convert_ogg_to_wem."""

    def _build_rounds(self, ogg_files: list[Path]) -> list[list[Path]]:
        """Reproduce the rounds logic from convert_ogg_to_wem."""
        basename_groups: dict[str, list[Path]] = defaultdict(list)
        for ogg in ogg_files:
            key = ogg.with_suffix(".wem").name.lower()
            basename_groups[key].append(ogg)

        max_copies = max((len(v) for v in basename_groups.values()), default=0)
        rounds: list[list[Path]] = []
        for i in range(max_copies):
            round_files = []
            for files in basename_groups.values():
                if i < len(files):
                    round_files.append(files[i])
            rounds.append(round_files)
        return rounds

    def test_no_collisions_single_round(self):
        """All unique basenames -> single round containing all files."""
        files = [
            Path("processed_ogg/vo/judy_f_AAAA.Ogg"),
            Path("processed_ogg/vo/reed_f_BBBB.Ogg"),
            Path("processed_ogg/vo/v_m_CCCC.Ogg"),
        ]
        rounds = self._build_rounds(files)
        assert len(rounds) == 1
        assert len(rounds[0]) == 3

    def test_basename_collision_separates_into_rounds(self):
        """Same basename in vo/ and vo_holocall/ must be in different rounds."""
        files = [
            Path("processed_ogg/vo/judy_f_AAAA.Ogg"),
            Path("processed_ogg/vo_holocall/judy_f_AAAA.Ogg"),
            Path("processed_ogg/vo/reed_f_BBBB.Ogg"),
        ]
        rounds = self._build_rounds(files)
        assert len(rounds) == 2

        # Round 0: one judy + reed
        round_0_basenames = [f.name.lower() for f in rounds[0]]
        assert len(round_0_basenames) == len(set(round_0_basenames)), "Round 0 has basename collision!"

        # Round 1: the other judy
        round_1_basenames = [f.name.lower() for f in rounds[1]]
        assert len(round_1_basenames) == len(set(round_1_basenames)), "Round 1 has basename collision!"

    def test_triple_collision(self):
        """Same basename in 3 directories -> 3 rounds."""
        files = [
            Path("processed_ogg/vo/judy_f_AAAA.Ogg"),
            Path("processed_ogg/vo_holocall/judy_f_AAAA.Ogg"),
            Path("processed_ogg/vo_helmet/judy_f_AAAA.Ogg"),
        ]
        rounds = self._build_rounds(files)
        assert len(rounds) == 3
        for i, r in enumerate(rounds):
            basenames = [f.name.lower() for f in r]
            assert len(basenames) == len(set(basenames)), f"Round {i} has collision!"

    def test_no_files_no_rounds(self):
        rounds = self._build_rounds([])
        assert rounds == []

    def test_all_rounds_cover_all_files(self):
        """Every input file must appear in exactly one round."""
        files = [
            Path("vo/judy_f_AAAA.Ogg"),
            Path("vo_holocall/judy_f_AAAA.Ogg"),
            Path("vo/reed_f_BBBB.Ogg"),
            Path("vo/v_m_CCCC.Ogg"),
            Path("vo_helmet/v_m_CCCC.Ogg"),
        ]
        rounds = self._build_rounds(files)
        all_files_in_rounds = [f for r in rounds for f in r]
        assert len(all_files_in_rounds) == len(files)
        assert set(str(f) for f in all_files_in_rounds) == set(str(f) for f in files)


class TestBatchSizing:
    """Test that batches respect command-line length limits."""

    def _build_batches(self, round_files: list[Path], max_cmd_len: int = 7500) -> list[list[Path]]:
        """Reproduce the batch-splitting logic from convert_ogg_to_wem."""
        base_cmd_len = 100  # approximate overhead
        batches: list[list[Path]] = []
        current_batch: list[Path] = []
        current_len = base_cmd_len
        for ogg in sorted(round_files, key=lambda p: p.name.lower()):
            path_len = len(str(ogg)) + 3
            if current_batch and current_len + path_len > max_cmd_len:
                batches.append(current_batch)
                current_batch = []
                current_len = base_cmd_len
            current_batch.append(ogg)
            current_len += path_len
        if current_batch:
            batches.append(current_batch)
        return batches

    def test_single_batch_when_small(self):
        files = [Path(f"vo/file_{i}.Ogg") for i in range(5)]
        batches = self._build_batches(files)
        assert len(batches) == 1
        assert len(batches[0]) == 5

    def test_splits_at_length_limit(self):
        # Create files with long paths that would exceed limit
        files = [Path(f"base/localization/en-us/vo/very_long_character_name_quest_{i:04d}_f_{i:016x}.Ogg") for i in range(100)]
        batches = self._build_batches(files, max_cmd_len=500)
        assert len(batches) > 1
        # Verify every file appears in exactly one batch
        all_in_batches = [f for b in batches for f in b]
        assert len(all_in_batches) == 100

    def test_empty_input(self):
        batches = self._build_batches([])
        assert batches == []
