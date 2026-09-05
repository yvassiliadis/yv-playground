"""Tests for ffdraft.sources.fantasysharks -- fixture-based, no live HTTP.

The fixture HTML files under tests/fixtures/sources/fantasysharks/ are
trimmed excerpts of a *real* live response (header row, "Points Awarded"
scoring-key row, plus 3 player rows) from
`fantasysharks.com/apps/bert/forecasts/projections.php?Position=<id>` for
each of QB/RB/WR/TE -- see `ffdraft.sources.fantasysharks`'s module
docstring for the (non-sequential) Position-code mapping this was confirmed
against.
"""

from pathlib import Path

import httpx
import polars as pl
import pytest

from ffdraft.sources import fantasysharks
from ffdraft.sources.base import CANONICAL_COLUMNS
from ffdraft.sources.fantasysharks import FantasySharksSource

FIXTURE_DIR = Path(__file__).parent.parent / "fixtures" / "sources" / "fantasysharks"

_HTML_BY_POSITION_ID = {
    1: (FIXTURE_DIR / "sample_qb.html").read_text(),
    2: (FIXTURE_DIR / "sample_rb.html").read_text(),
    4: (FIXTURE_DIR / "sample_wr.html").read_text(),
    5: (FIXTURE_DIR / "sample_te.html").read_text(),
}


@pytest.fixture
def crosswalk_reference() -> pl.DataFrame:
    return pl.DataFrame(
        {
            "player_id": ["00-1111", "00-2222", "00-3333", "00-4444"],
            "normalized_name": [
                "josh allen",
                "christian mccaffrey",
                "ja'marr chase",
                "trey mcbride",
            ],
            "position": ["QB", "RB", "WR", "TE"],
            "team": ["BUF", "SFO", "CIN", "ARI"],
        }
    )


def _mock_client(monkeypatch) -> None:
    def fake_get(self, url, **kwargs):
        position_id = kwargs["params"]["Position"]
        request = httpx.Request("GET", url)
        return httpx.Response(
            200, text=_HTML_BY_POSITION_ID[position_id], request=request
        )

    monkeypatch.setattr(httpx.Client, "get", fake_get)


class TestFetch:
    def test_fetch_parses_fixtures_into_canonical_schema(
        self, monkeypatch, crosswalk_reference
    ):
        _mock_client(monkeypatch)
        monkeypatch.setattr(
            fantasysharks, "build_crosswalk", lambda: crosswalk_reference
        )

        result = FantasySharksSource().fetch(season=2026)

        assert result.columns == CANONICAL_COLUMNS
        assert (result["source"] == "fantasysharks").all()
        assert (result["week"] == 0).all()
        assert (result["season"] == 2026).all()
        assert result["source_player_id"].is_null().all()

    def test_fetches_one_position_at_a_time_and_concatenates(
        self, monkeypatch, crosswalk_reference
    ):
        seen_position_ids = []

        def fake_get(self, url, **kwargs):
            position_id = kwargs["params"]["Position"]
            seen_position_ids.append(position_id)
            request = httpx.Request("GET", url)
            return httpx.Response(
                200, text=_HTML_BY_POSITION_ID[position_id], request=request
            )

        monkeypatch.setattr(httpx.Client, "get", fake_get)
        monkeypatch.setattr(
            fantasysharks, "build_crosswalk", lambda: crosswalk_reference
        )

        FantasySharksSource().fetch(season=2026)

        assert sorted(seen_position_ids) == [1, 2, 4, 5]

    def test_reverses_last_first_name_order(self, monkeypatch, crosswalk_reference):
        _mock_client(monkeypatch)
        monkeypatch.setattr(
            fantasysharks, "build_crosswalk", lambda: crosswalk_reference
        )

        result = FantasySharksSource().fetch(season=2026)

        assert "Josh Allen" in result["player_name_raw"].to_list()
        assert "Allen, Josh" not in result["player_name_raw"].to_list()

    def test_resolves_player_id_via_name_match(self, monkeypatch, crosswalk_reference):
        _mock_client(monkeypatch)
        monkeypatch.setattr(
            fantasysharks, "build_crosswalk", lambda: crosswalk_reference
        )

        result = FantasySharksSource().fetch(season=2026)

        allen = result.filter(
            (pl.col("player_name_raw") == "Josh Allen")
            & (pl.col("stat_name") == "passing yard")
        )
        assert allen.height == 1
        assert allen["player_id"].item() == "00-1111"

    def test_unmatched_name_resolves_to_null_player_id(self, monkeypatch):
        _mock_client(monkeypatch)
        empty_reference = pl.DataFrame(
            schema={
                "player_id": pl.String,
                "normalized_name": pl.String,
                "position": pl.String,
                "team": pl.String,
            }
        )
        monkeypatch.setattr(fantasysharks, "build_crosswalk", lambda: empty_reference)

        result = FantasySharksSource().fetch(season=2026)

        assert result["player_id"].is_null().all()

    def test_distance_bucket_and_points_allowed_columns_not_mapped(
        self, monkeypatch, crosswalk_reference
    ):
        _mock_client(monkeypatch)
        monkeypatch.setattr(
            fantasysharks, "build_crosswalk", lambda: crosswalk_reference
        )

        result = FantasySharksSource().fetch(season=2026)

        assert not any(
            "0-9" in name or "50+" in name
            for name in result["stat_name"].unique().to_list()
        )

    def test_never_makes_a_real_network_call(self, monkeypatch, crosswalk_reference):
        calls = []

        def fake_get(self, url, **kwargs):
            calls.append(url)
            position_id = kwargs["params"]["Position"]
            request = httpx.Request("GET", url)
            return httpx.Response(
                200, text=_HTML_BY_POSITION_ID[position_id], request=request
            )

        monkeypatch.setattr(httpx.Client, "get", fake_get)
        monkeypatch.setattr(
            fantasysharks, "build_crosswalk", lambda: crosswalk_reference
        )

        FantasySharksSource().fetch(season=2026)

        assert len(calls) == 4
        assert all(
            url.startswith(
                "https://www.fantasysharks.com/apps/bert/forecasts/projections.php"
            )
            for url in calls
        )
