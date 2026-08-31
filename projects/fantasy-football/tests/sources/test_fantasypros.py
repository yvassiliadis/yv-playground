"""Tests for ffdraft.sources.fantasypros -- fixture-based, no live HTTP.

The fixture CSVs' column *layout* (a single combined Player+Team cell like
"Jahmyr Gibbs DET", then position-specific stat columns in a confirmed
order) matches what a live, anonymous fetch of FantasyPros' HTML projections
page actually renders -- see `ffdraft.sources.fantasypros`'s module
docstring for how that was confirmed despite the CSV export itself requiring
an authenticated session. The specific numbers in these fixtures are still
illustrative, not captured live.
"""

from pathlib import Path

import httpx
import polars as pl
import pytest

from ffdraft.sources import fantasypros
from ffdraft.sources.base import CANONICAL_COLUMNS
from ffdraft.sources.fantasypros import FantasyProsSource

FIXTURE_DIR = Path(__file__).parent.parent / "fixtures" / "sources" / "fantasypros"

_CSV_BY_POSITION = {
    position: (FIXTURE_DIR / f"sample_response_{position}.csv").read_text()
    for position in ("qb", "rb", "wr", "te", "dst")
}


@pytest.fixture
def crosswalk_reference() -> pl.DataFrame:
    return pl.DataFrame(
        {
            "player_id": ["00-1111", "00-2222", "00-3333", "00-4444"],
            "normalized_name": [
                "patrick mahomes",
                "christian mccaffrey",
                "justin jefferson",
                "travis kelce",
            ],
            "position": ["QB", "RB", "WR", "TE"],
            "team": ["KC", "SF", "MIN", "KC"],
        }
    )


def _mock_client(monkeypatch) -> None:
    def fake_get(self, url, **kwargs):
        position = url.rsplit("/", 1)[-1].removesuffix(".php")
        request = httpx.Request("GET", url)
        return httpx.Response(200, text=_CSV_BY_POSITION[position], request=request)

    monkeypatch.setattr(httpx.Client, "get", fake_get)


class TestFetch:
    def test_fetch_parses_fixtures_into_canonical_schema(
        self, monkeypatch, crosswalk_reference
    ):
        _mock_client(monkeypatch)
        monkeypatch.setattr(fantasypros, "build_crosswalk", lambda: crosswalk_reference)

        result = FantasyProsSource().fetch(season=2026)

        assert result.columns == CANONICAL_COLUMNS
        assert (result["source"] == "fantasypros").all()
        assert (result["week"] == 0).all()
        assert result["source_player_id"].is_null().all()

    def test_fetches_one_position_at_a_time_and_concatenates(
        self, monkeypatch, crosswalk_reference
    ):
        seen_positions = []

        def fake_get(self, url, **kwargs):
            position = url.rsplit("/", 1)[-1].removesuffix(".php")
            seen_positions.append(position)
            request = httpx.Request("GET", url)
            return httpx.Response(200, text=_CSV_BY_POSITION[position], request=request)

        monkeypatch.setattr(httpx.Client, "get", fake_get)
        monkeypatch.setattr(fantasypros, "build_crosswalk", lambda: crosswalk_reference)

        FantasyProsSource().fetch(season=2026)

        assert sorted(seen_positions) == ["dst", "qb", "rb", "te", "wr"]

    def test_duplicate_header_columns_parsed_positionally(
        self, monkeypatch, crosswalk_reference
    ):
        # The QB fixture's CSV header has "ATT", "YDS", "TDS" twice
        # (passing, then rushing) -- this proves the positional rename
        # doesn't confuse the two blocks.
        _mock_client(monkeypatch)
        monkeypatch.setattr(fantasypros, "build_crosswalk", lambda: crosswalk_reference)

        result = FantasyProsSource().fetch(season=2026)

        mahomes = result.filter(pl.col("player_name_raw") == "Patrick Mahomes")
        stats = dict(zip(mahomes["stat_name"], mahomes["stat_value"]))
        assert stats["passing yard"] == 4300.0
        assert stats["rushing yard"] == 275.0
        assert stats["passing td"] == 32.0
        assert stats["rushing td"] == 3.0

    def test_resolves_player_id_via_name_matching(
        self, monkeypatch, crosswalk_reference
    ):
        _mock_client(monkeypatch)
        monkeypatch.setattr(fantasypros, "build_crosswalk", lambda: crosswalk_reference)

        result = FantasyProsSource().fetch(season=2026)

        jefferson = result.filter(pl.col("player_name_raw") == "Justin Jefferson")
        assert (jefferson["player_id"] == "00-3333").all()

    def test_unmatched_name_resolves_to_null_player_id(
        self, monkeypatch, crosswalk_reference
    ):
        _mock_client(monkeypatch)
        monkeypatch.setattr(fantasypros, "build_crosswalk", lambda: crosswalk_reference)

        result = FantasyProsSource().fetch(season=2026)

        # "Nobody Special" isn't in the crosswalk fixture.
        unmatched = result.filter(pl.col("player_name_raw") == "Nobody Special")
        assert unmatched.height > 0
        assert unmatched["player_id"].is_null().all()

    def test_splits_combined_player_and_team_cell(
        self, monkeypatch, crosswalk_reference
    ):
        # Confirmed live: FantasyPros' report has no separate Team column --
        # each row's first cell is "<Name> <TEAM>" combined.
        _mock_client(monkeypatch)
        monkeypatch.setattr(fantasypros, "build_crosswalk", lambda: crosswalk_reference)

        result = FantasyProsSource().fetch(season=2026)

        jefferson = result.filter(pl.col("player_name_raw") == "Justin Jefferson")
        assert (jefferson["team"] == "MIN").all()

    def test_dst_player_and_team_cell_has_no_trailing_code(
        self, monkeypatch, crosswalk_reference
    ):
        # DST rows' first cell is just the franchise's full name with no
        # trailing team code at all ("San Francisco 49ers", not "... SF").
        _mock_client(monkeypatch)
        monkeypatch.setattr(fantasypros, "build_crosswalk", lambda: crosswalk_reference)

        result = FantasyProsSource().fetch(season=2026)

        dst = result.filter(pl.col("position") == "DST")
        assert (dst["player_name_raw"] == "San Francisco 49ers").all()

    def test_dst_row_resolves_via_full_franchise_name(
        self, monkeypatch, crosswalk_reference
    ):
        _mock_client(monkeypatch)
        monkeypatch.setattr(fantasypros, "build_crosswalk", lambda: crosswalk_reference)

        result = FantasyProsSource().fetch(season=2026)

        dst = result.filter(pl.col("position") == "DST")
        assert dst.height > 0
        assert (dst["player_id"] == "DST_SF").all()

    def test_week_other_than_zero_not_implemented(self):
        with pytest.raises(NotImplementedError):
            FantasyProsSource().fetch(season=2026, week=1)
