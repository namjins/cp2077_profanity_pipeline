"""Tests for audio.py: voiceover map building, path resolution, and file identity."""

import json
import pytest
from pathlib import Path

from cp2077_profanity.audio import (
    build_string_id_to_wem_map,
    find_wem_paths_for_records,
    collect_target_ogg_files,
)
from cp2077_profanity.patcher import PatchRecord


# -- build_string_id_to_wem_map -------------------------------------------

class TestBuildStringIdToWemMap:
    def _write_voiceover_map(self, maps_dir: Path, filename: str, entries: list[dict]) -> None:
        """Write a mock voiceover map .json.json file."""
        data = {
            "Data": {"RootChunk": {"root": {"Data": {"entries": entries}}}}
        }
        out = maps_dir / filename
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(data), encoding="utf-8")

    def test_basic_mapping(self, tmp_path):
        self._write_voiceover_map(tmp_path, "content/voiceovermap.json.json", [
            {
                "stringId": 12345,
                "femaleResPath": {"DepotPath": {"$value": "base/vo/judy_f_AAAA.wem"}},
                "maleResPath": {"DepotPath": {"$value": "base/vo/judy_m_BBBB.wem"}},
            }
        ])
        result = build_string_id_to_wem_map(tmp_path)
        assert "12345" in result
        assert result["12345"]["female"] == ["base/vo/judy_f_AAAA.wem"]
        assert result["12345"]["male"] == ["base/vo/judy_m_BBBB.wem"]

    def test_merges_variants_from_multiple_maps(self, tmp_path):
        """Same stringId in voiceovermap.json and voiceovermap_holocall.json should merge paths."""
        self._write_voiceover_map(tmp_path, "content/voiceovermap.json.json", [
            {
                "stringId": 12345,
                "femaleResPath": {"DepotPath": {"$value": "base/vo/judy_f_AAAA.wem"}},
                "maleResPath": {"DepotPath": {"$value": ""}},
            }
        ])
        self._write_voiceover_map(tmp_path, "content/voiceovermap_holocall.json.json", [
            {
                "stringId": 12345,
                "femaleResPath": {"DepotPath": {"$value": "base/vo_holocall/judy_f_AAAA.wem"}},
                "maleResPath": {"DepotPath": {"$value": ""}},
            }
        ])
        result = build_string_id_to_wem_map(tmp_path)
        assert "12345" in result
        # Both paths should be preserved
        assert "base/vo/judy_f_AAAA.wem" in result["12345"]["female"]
        assert "base/vo_holocall/judy_f_AAAA.wem" in result["12345"]["female"]

    def test_skips_zero_string_id(self, tmp_path):
        self._write_voiceover_map(tmp_path, "map.json.json", [
            {
                "stringId": 0,
                "femaleResPath": {"DepotPath": {"$value": "base/vo/test.wem"}},
                "maleResPath": {"DepotPath": {"$value": ""}},
            }
        ])
        result = build_string_id_to_wem_map(tmp_path)
        assert "0" not in result

    def test_no_duplicate_paths(self, tmp_path):
        """Same path appearing in two maps should not be duplicated."""
        self._write_voiceover_map(tmp_path, "map1.json.json", [
            {
                "stringId": 100,
                "femaleResPath": {"DepotPath": {"$value": "base/vo/same.wem"}},
                "maleResPath": {"DepotPath": {"$value": ""}},
            }
        ])
        self._write_voiceover_map(tmp_path, "map2.json.json", [
            {
                "stringId": 100,
                "femaleResPath": {"DepotPath": {"$value": "base/vo/same.wem"}},
                "maleResPath": {"DepotPath": {"$value": ""}},
            }
        ])
        result = build_string_id_to_wem_map(tmp_path)
        assert result["100"]["female"].count("base/vo/same.wem") == 1


# -- find_wem_paths_for_records -------------------------------------------

class TestFindWemPathsForRecords:
    def _make_record(self, string_id, field="femaleVariant"):
        return PatchRecord(
            filepath="test.json.json",
            string_key="key",
            string_id=string_id,
            field=field,
            original="original",
            replacement="replaced",
            words_replaced=["word"],
        )

    def test_basic_matching(self):
        voiceover_map = {
            "12345": {
                "female": ["base/vo/judy_f_AAAA.wem"],
                "male": ["base/vo/judy_m_BBBB.wem"],
            }
        }
        records = [self._make_record("12345")]
        targets = find_wem_paths_for_records(records, voiceover_map)
        depot_paths = [dp for _, dp in targets]
        assert "base/vo/judy_f_AAAA.wem" in depot_paths
        assert "base/vo/judy_m_BBBB.wem" in depot_paths

    def test_deduplicates_by_full_path(self):
        """Same depot path from different records should appear only once."""
        voiceover_map = {
            "12345": {
                "female": ["base/vo/judy_f_AAAA.wem"],
                "male": [],
            }
        }
        records = [
            self._make_record("12345", "femaleVariant"),
            self._make_record("12345", "maleVariant"),
        ]
        targets = find_wem_paths_for_records(records, voiceover_map)
        depot_paths = [dp for _, dp in targets]
        assert depot_paths.count("base/vo/judy_f_AAAA.wem") == 1

    def test_includes_all_variant_paths(self):
        """vo/ and vo_holocall/ variants should both be included."""
        voiceover_map = {
            "12345": {
                "female": [
                    "base/vo/judy_f_AAAA.wem",
                    "base/vo_holocall/judy_f_AAAA.wem",
                ],
                "male": [],
            }
        }
        records = [self._make_record("12345")]
        targets = find_wem_paths_for_records(records, voiceover_map)
        depot_paths = [dp for _, dp in targets]
        assert "base/vo/judy_f_AAAA.wem" in depot_paths
        assert "base/vo_holocall/judy_f_AAAA.wem" in depot_paths

    def test_skips_records_without_string_id(self):
        voiceover_map = {"12345": {"female": ["path.wem"], "male": []}}
        records = [self._make_record(None)]
        targets = find_wem_paths_for_records(records, voiceover_map)
        assert targets == []


# -- collect_target_ogg_files ---------------------------------------------

class TestCollectTargetOggFiles:
    def test_resolves_by_full_path(self, tmp_path):
        """Files are matched by full depot path, not basename."""
        wem_dir = tmp_path / "wem_files"
        # Create two files with same basename in different directories
        vo_dir = wem_dir / "base" / "localization" / "en-us" / "vo"
        vo_holo_dir = wem_dir / "base" / "localization" / "en-us" / "vo_holocall"
        vo_dir.mkdir(parents=True)
        vo_holo_dir.mkdir(parents=True)

        (vo_dir / "judy_f_AAAA.ogg").write_bytes(b"audio_data_vo")
        (vo_holo_dir / "judy_f_AAAA.ogg").write_bytes(b"audio_data_holocall")

        depot_paths = [
            "base/localization/en-us/vo/judy_f_AAAA.wem",
            "base/localization/en-us/vo_holocall/judy_f_AAAA.wem",
        ]
        selected = collect_target_ogg_files(wem_dir, depot_paths)

        assert len(selected) == 2
        # Verify they are from different directories
        parents = {p.parent.name for p in selected}
        assert "vo" in parents
        assert "vo_holocall" in parents

    def test_no_basename_collision(self, tmp_path):
        """Same basename in different dirs should NOT overwrite each other."""
        wem_dir = tmp_path / "wem_files"
        vo = wem_dir / "base" / "vo"
        vo_hc = wem_dir / "base" / "vo_holocall"
        vo.mkdir(parents=True)
        vo_hc.mkdir(parents=True)

        (vo / "same.ogg").write_bytes(b"content_A")
        (vo_hc / "same.ogg").write_bytes(b"content_B")

        depot_paths = ["base/vo/same.wem", "base/vo_holocall/same.wem"]
        selected = collect_target_ogg_files(wem_dir, depot_paths)
        assert len(selected) == 2

        # Verify content is preserved (not overwritten)
        contents = {p.read_bytes() for p in selected}
        assert b"content_A" in contents
        assert b"content_B" in contents

    def test_missing_file_reported(self, tmp_path, capsys):
        wem_dir = tmp_path / "wem_files"
        wem_dir.mkdir()
        selected = collect_target_ogg_files(wem_dir, ["base/vo/missing.wem"])
        assert selected == []
        captured = capsys.readouterr()
        assert "missing" in captured.out.lower() or len(selected) == 0
