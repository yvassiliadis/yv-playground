"""Tests for ffdraft.sources.fftoday -- fixture-based, no live HTTP.

The fixture HTML files under tests/fixtures/sources/fftoday/ are trimmed
excerpts of a *real* live response (3 data rows plus the header row) from
`fftoday.com/rankings/playerproj.php?Season=2026&PosID=<id>` for each of the
four skill positions -- see `ffdraft.sources.fftoday`'s module docstring for
how the live structure was confirmed.
"""

from pathlib import Path

import httpx
import polars as pl
import pytest

from ffdraft.sources import fftoday
from ffdraft.sources.base import CANONICAL_COLUMNS
from ffdraft.sources.fftoday import FFTodaySource

FIXTURE_DIR = Path(__file__).parent.parent / "fixtures" / "sources" / "fftoday"

_HTML_BY_POSID = {
    10: (FIXTURE_DIR / "sample_qb.html").read_text(),
    20: (FIXTURE_DIR / "sample_rb.html").read_text(),
    30: (FIXTURE_DIR / "sample_wr.html").read_text(),
    40: (FIXTURE_DIR / "sample_te.html").read_text(),
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
        pos_id = kwargs["params"]["PosID"]
        request = httpx.Request("GET", url)
        return httpx.Response(200, text=_HTML_BY_POSID[pos_id], request=request)

    monkeypatch.setattr(httpx.Client, "get", fake_get)


class TestFetch:
    def test_fetch_parses_fixtures_into_canonical_schema(
        self, monkeypatch, crosswalk_reference
    ):
        _mock_client(monkeypatch)
        monkeypatch.setattr(fftoday, "build_crosswalk", lambda: crosswalk_reference)

        result = FFTodaySource().fetch(season=2026)

        assert result.columns == CANONICAL_COLUMNS
        assert (result["source"] == "fftoday").all()
        assert (result["week"] == 0).all()
        assert (result["season"] == 2026).all()
        assert result["source_player_id"].is_null().all()

    def test_fetches_one_position_at_a_time_and_concatenates(
        self, monkeypatch, crosswalk_reference
    ):
        seen_pos_ids = []

        def fake_get(self, url, **kwargs):
            pos_id = kwargs["params"]["PosID"]
            seen_pos_ids.append(pos_id)
            request = httpx.Request("GET", url)
            return httpx.Response(200, text=_HTML_BY_POSID[pos_id], request=request)

        monkeypatch.setattr(httpx.Client, "get", fake_get)
        monkeypatch.setattr(fftoday, "build_crosswalk", lambda: crosswalk_reference)

        FFTodaySource().fetch(season=2026)

        assert sorted(seen_pos_ids) == [10, 20, 30, 40]

    def test_resolves_player_id_via_name_match(self, monkeypatch, crosswalk_reference):
        _mock_client(monkeypatch)
        monkeypatch.setattr(fftoday, "build_crosswalk", lambda: crosswalk_reference)

        result = FFTodaySource().fetch(season=2026)

        allen = result.filter(
            (pl.col("player_name_raw") == "Josh Allen")
            & (pl.col("stat_name") == "passing td")
        )
        assert allen.height == 1
        assert allen["player_id"].item() == "00-1111"
        assert allen["stat_value"].item() == 26.0

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
        monkeypatch.setattr(fftoday, "build_crosswalk", lambda: empty_reference)

        result = FFTodaySource().fetch(season=2026)

        assert result["player_id"].is_null().all()

    def test_pass_yards_parsed_without_thousands_comma(
        self, monkeypatch, crosswalk_reference
    ):
        _mock_client(monkeypatch)
        monkeypatch.setattr(fftoday, "build_crosswalk", lambda: crosswalk_reference)

        result = FFTodaySource().fetch(season=2026)

        allen_yards = result.filter(
            (pl.col("player_name_raw") == "Josh Allen")
            & (pl.col("stat_name") == "passing yard")
        )
        assert allen_yards["stat_value"].item() == 3787.0

    def test_never_makes_a_real_network_call(self, monkeypatch, crosswalk_reference):
        calls = []

        def fake_get(self, url, **kwargs):
            calls.append(url)
            pos_id = kwargs["params"]["PosID"]
            request = httpx.Request("GET", url)
            return httpx.Response(200, text=_HTML_BY_POSID[pos_id], request=request)

        monkeypatch.setattr(httpx.Client, "get", fake_get)
        monkeypatch.setattr(fftoday, "build_crosswalk", lambda: crosswalk_reference)

        FFTodaySource().fetch(season=2026)

        assert len(calls) == 4
        assert all(
            url.startswith("https://fftoday.com/rankings/playerproj.php")
            for url in calls
        )
