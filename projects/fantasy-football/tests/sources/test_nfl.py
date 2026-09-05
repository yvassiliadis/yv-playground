"""Tests for ffdraft.sources.nfl -- fixture-based, no live HTTP.

**Synthetic fixtures.** A live `curl` to fantasy.nfl.com's projections
research URL returned NFL.com's generic "Fantasy News" landing page, not a
projections table (see `ffdraft.sources.nfl`'s module docstring) -- there
is no live markup to check this parser against. The fixtures here are
hand-authored to exercise the parser's best-effort, unverified guess at
NFL.com's research-table shape.
"""

from pathlib import Path

import httpx
import polars as pl
import pytest

from ffdraft.sources import nfl
from ffdraft.sources.base import CANONICAL_COLUMNS
from ffdraft.sources.nfl import NFLSource

FIXTURE_DIR = Path(__file__).parent.parent / "fixtures" / "sources" / "nfl"

_HTML_BY_POSITION_ID = {
    1: (FIXTURE_DIR / "sample_qb.html").read_text(),
    2: (FIXTURE_DIR / "sample_rb.html").read_text(),
    3: (FIXTURE_DIR / "sample_wr.html").read_text(),
    4: (FIXTURE_DIR / "sample_te.html").read_text(),
}


@pytest.fixture
def crosswalk_reference() -> pl.DataFrame:
    return pl.DataFrame(
        {
            "player_id": ["00-1111", "00-2222", "00-3333", "00-4444"],
            "normalized_name": [
                "josh allen",
                "jahmyr gibbs",
                "puka nacua",
                "brock bowers",
            ],
            "position": ["QB", "RB", "WR", "TE"],
            "team": ["BUF", "DET", "LAR", "LV"],
        }
    )


def _mock_client(monkeypatch) -> None:
    def fake_get(self, url, **kwargs):
        position_id = kwargs["params"]["position"]
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
        monkeypatch.setattr(nfl, "build_crosswalk", lambda: crosswalk_reference)

        result = NFLSource().fetch(season=2026)

        assert result.columns == CANONICAL_COLUMNS
        assert (result["source"] == "nfl").all()
        assert (result["week"] == 0).all()
        assert result["source_player_id"].is_null().all()

    def test_fetches_one_position_at_a_time_and_concatenates(
        self, monkeypatch, crosswalk_reference
    ):
        seen_position_ids = []

        def fake_get(self, url, **kwargs):
            position_id = kwargs["params"]["position"]
            seen_position_ids.append(position_id)
            request = httpx.Request("GET", url)
            return httpx.Response(
                200, text=_HTML_BY_POSITION_ID[position_id], request=request
            )

        monkeypatch.setattr(httpx.Client, "get", fake_get)
        monkeypatch.setattr(nfl, "build_crosswalk", lambda: crosswalk_reference)

        NFLSource().fetch(season=2026)

        assert sorted(seen_position_ids) == [1, 2, 3, 4]

    def test_splits_trailing_team_and_position_suffix_off_name(
        self, monkeypatch, crosswalk_reference
    ):
        _mock_client(monkeypatch)
        monkeypatch.setattr(nfl, "build_crosswalk", lambda: crosswalk_reference)

        result = NFLSource().fetch(season=2026)

        assert "Josh Allen" in result["player_name_raw"].to_list()
        allen_team = result.filter(pl.col("player_name_raw") == "Josh Allen")["team"]
        assert (allen_team == "BUF").all()

    def test_resolves_player_id_via_name_match(self, monkeypatch, crosswalk_reference):
        _mock_client(monkeypatch)
        monkeypatch.setattr(nfl, "build_crosswalk", lambda: crosswalk_reference)

        result = NFLSource().fetch(season=2026)

        allen = result.filter(
            (pl.col("player_name_raw") == "Josh Allen")
            & (pl.col("stat_name") == "passing td")
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
        monkeypatch.setattr(nfl, "build_crosswalk", lambda: empty_reference)

        result = NFLSource().fetch(season=2026)

        assert result["player_id"].is_null().all()

    def test_never_makes_a_real_network_call(self, monkeypatch, crosswalk_reference):
        calls = []

        def fake_get(self, url, **kwargs):
            calls.append(url)
            position_id = kwargs["params"]["position"]
            request = httpx.Request("GET", url)
            return httpx.Response(
                200, text=_HTML_BY_POSITION_ID[position_id], request=request
            )

        monkeypatch.setattr(httpx.Client, "get", fake_get)
        monkeypatch.setattr(nfl, "build_crosswalk", lambda: crosswalk_reference)

        NFLSource().fetch(season=2026)

        assert len(calls) == 4
        assert all(url.startswith("https://fantasy.nfl.com") for url in calls)
