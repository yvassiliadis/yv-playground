"""Tests for ffdraft.ingest.archives -- fixture-based, no network.

The fixture CSVs under tests/fixtures/archives/{ffa,ffdp}/ are hand-built,
NOT captured from real downloaded FFA/ffdp archives -- see
ffdraft.ingest.archives's module docstring for the documented (and
explicitly unverified) format assumptions these fixtures encode.
"""

from pathlib import Path

import polars as pl
import pytest

from ffdraft.ids.crosswalk import REFERENCE_COLUMNS
from ffdraft.ingest import archives
from ffdraft.ingest.actuals import CANONICAL_COLUMNS
from ffdraft.ingest.archives import load_ffa_archives, load_ffdp_archives

FIXTURE_DIR = Path(__file__).parent.parent / "fixtures" / "archives"


@pytest.fixture
def crosswalk_reference() -> pl.DataFrame:
    return pl.DataFrame(
        {
            "player_id": ["00-1111", "00-2222"],
            "normalized_name": ["patrick mahomes", "justin jefferson"],
            "position": ["QB", "WR"],
            "team": ["KC", "MIN"],
        }
    )


def test_reference_fixture_matches_crosswalk_shape(crosswalk_reference):
    # sanity check: the columns resolve_by_name actually needs are a subset
    # of what build_crosswalk() produces.
    assert set(crosswalk_reference.columns) <= set(REFERENCE_COLUMNS)


class TestLoadFfaArchives:
    def test_parses_fixture_into_canonical_schema(
        self, monkeypatch, crosswalk_reference
    ):
        monkeypatch.setattr(archives, "build_crosswalk", lambda: crosswalk_reference)

        result = load_ffa_archives(FIXTURE_DIR / "ffa")

        assert result.columns == CANONICAL_COLUMNS
        assert (result["week"] == 0).all()
        assert (result["season"] == 2020).all()

    def test_source_is_prefixed_with_ffa_and_original_source_name(
        self, monkeypatch, crosswalk_reference
    ):
        monkeypatch.setattr(archives, "build_crosswalk", lambda: crosswalk_reference)

        result = load_ffa_archives(FIXTURE_DIR / "ffa")

        sources = set(result["source"].to_list())
        assert "ffa_espn" in sources
        assert "ffa_nfl" in sources

    def test_resolves_player_id_via_name_matching(
        self, monkeypatch, crosswalk_reference
    ):
        monkeypatch.setattr(archives, "build_crosswalk", lambda: crosswalk_reference)

        result = load_ffa_archives(FIXTURE_DIR / "ffa")

        mahomes = result.filter(pl.col("player_name_raw") == "Patrick Mahomes")
        assert mahomes.height > 0
        assert (mahomes["player_id"] == "00-1111").all()

        jefferson = result.filter(pl.col("player_name_raw") == "Justin Jefferson")
        assert jefferson.height > 0
        assert (jefferson["player_id"] == "00-2222").all()

    def test_unmatched_name_gets_null_player_id_and_is_collectible(
        self, monkeypatch, crosswalk_reference
    ):
        monkeypatch.setattr(archives, "build_crosswalk", lambda: crosswalk_reference)

        result = load_ffa_archives(FIXTURE_DIR / "ffa")

        # "Totally Fictional Player" isn't in the crosswalk fixture -- must
        # not raise, and must surface via the same
        # `player_id.is_null()` filter Task 4 established as the unmatched-
        # report mechanism.
        unmatched = result.filter(pl.col("player_id").is_null())
        assert unmatched.height > 0
        assert (unmatched["player_name_raw"] == "Totally Fictional Player").all()

    def test_dst_row_resolves_via_synthetic_dst_id(
        self, monkeypatch, crosswalk_reference
    ):
        monkeypatch.setattr(archives, "build_crosswalk", lambda: crosswalk_reference)

        result = load_ffa_archives(FIXTURE_DIR / "ffa")

        dst = result.filter(pl.col("position") == "DST")
        assert dst.height > 0
        assert (dst["player_id"] == "DST_SF").all()

    def test_stat_values_mapped_to_canonical_stat_names(
        self, monkeypatch, crosswalk_reference
    ):
        monkeypatch.setattr(archives, "build_crosswalk", lambda: crosswalk_reference)

        result = load_ffa_archives(FIXTURE_DIR / "ffa")

        jefferson = result.filter(pl.col("player_name_raw") == "Justin Jefferson")
        stats = dict(zip(jefferson["stat_name"], jefferson["stat_value"]))
        assert stats["receiving yard"] == 1500.0
        assert stats["receiving td"] == 10.0
        assert stats["fumble lost"] == 1.0

    def test_non_matching_filenames_are_skipped(
        self, tmp_path, monkeypatch, crosswalk_reference
    ):
        monkeypatch.setattr(archives, "build_crosswalk", lambda: crosswalk_reference)
        (tmp_path / "README.txt").write_text("not a csv archive")
        (tmp_path / "notes.csv").write_text("this,does,not,match,the,pattern\n")

        result = load_ffa_archives(tmp_path)

        assert result.height == 0
        assert result.columns == CANONICAL_COLUMNS

    def test_empty_directory_returns_empty_canonical_frame(
        self, tmp_path, monkeypatch, crosswalk_reference
    ):
        monkeypatch.setattr(archives, "build_crosswalk", lambda: crosswalk_reference)

        result = load_ffa_archives(tmp_path)

        assert result.height == 0
        assert result.columns == CANONICAL_COLUMNS


class TestLoadFfdpArchives:
    def test_parses_fixture_into_canonical_schema(
        self, monkeypatch, crosswalk_reference
    ):
        monkeypatch.setattr(archives, "build_crosswalk", lambda: crosswalk_reference)

        result = load_ffdp_archives(FIXTURE_DIR / "ffdp")

        assert result.columns == CANONICAL_COLUMNS
        assert (result["week"] == 0).all()
        assert (result["season"] == 2020).all()
        assert (result["source"] == "ffdp").all()

    def test_resolves_player_id_via_name_matching(
        self, monkeypatch, crosswalk_reference
    ):
        monkeypatch.setattr(archives, "build_crosswalk", lambda: crosswalk_reference)

        result = load_ffdp_archives(FIXTURE_DIR / "ffdp")

        mahomes = result.filter(pl.col("player_name_raw") == "Patrick Mahomes")
        assert mahomes.height > 0
        assert (mahomes["player_id"] == "00-1111").all()

    def test_unmatched_name_gets_null_player_id_and_is_collectible(
        self, monkeypatch, crosswalk_reference
    ):
        monkeypatch.setattr(archives, "build_crosswalk", lambda: crosswalk_reference)

        result = load_ffdp_archives(FIXTURE_DIR / "ffdp")

        unmatched = result.filter(pl.col("player_id").is_null())
        assert unmatched.height > 0
        assert (unmatched["player_name_raw"] == "Nobody Special").all()

    def test_stat_values_mapped_to_canonical_stat_names(
        self, monkeypatch, crosswalk_reference
    ):
        monkeypatch.setattr(archives, "build_crosswalk", lambda: crosswalk_reference)

        result = load_ffdp_archives(FIXTURE_DIR / "ffdp")

        mahomes = result.filter(pl.col("player_name_raw") == "Patrick Mahomes")
        stats = dict(zip(mahomes["stat_name"], mahomes["stat_value"]))
        assert stats["passing yard"] == 4500.0
        assert stats["passing td"] == 38.0
        assert stats["pass intercepted"] == 10.0
        assert stats["fumble lost"] == 1.0

    def test_empty_directory_returns_empty_canonical_frame(
        self, tmp_path, monkeypatch, crosswalk_reference
    ):
        monkeypatch.setattr(archives, "build_crosswalk", lambda: crosswalk_reference)

        result = load_ffdp_archives(tmp_path)

        assert result.height == 0
        assert result.columns == CANONICAL_COLUMNS
