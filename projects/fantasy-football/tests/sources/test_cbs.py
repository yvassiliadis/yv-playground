"""Tests for ffdraft.sources.cbs -- fixture-based, no live HTTP.

**Live-verified.** The fixtures under tests/fixtures/sources/cbs/ are
trimmed excerpts (3 real players per position) captured from CBS's actual
`cbssports.com/fantasy/football/stats/<POS>/<season>/restofseason/
projections/nonppr/` page -- see `ffdraft.sources.cbs`'s module docstring
for how the real markup and native-ID join were confirmed.
"""

from pathlib import Path

import httpx
import polars as pl
import pytest

from ffdraft.sources import cbs
from ffdraft.sources.base import CANONICAL_COLUMNS
from ffdraft.sources.cbs import CBSSource

FIXTURE_DIR = Path(__file__).parent.parent / "fixtures" / "sources" / "cbs"

_HTML_BY_POSITION = {
    "QB": (FIXTURE_DIR / "sample_qb.html").read_text(),
    "RB": (FIXTURE_DIR / "sample_rb.html").read_text(),
    "WR": (FIXTURE_DIR / "sample_wr.html").read_text(),
    "TE": (FIXTURE_DIR / "sample_te.html").read_text(),
}

# Real CBS player IDs pulled from each fixture's player-link hrefs.
JOSH_ALLEN_CBS_ID = "2181054"
JAHMYR_GIBBS_CBS_ID = "3162723"
PUKA_NACUA_CBS_ID = "3121687"
TREY_MCBRIDE_CBS_ID = "2963385"


@pytest.fixture
def crosswalk_reference() -> pl.DataFrame:
    return pl.DataFrame(
        {
            "player_id": ["00-1111", "00-2222", "00-3333", "00-4444"],
            "cbs_id": [
                JOSH_ALLEN_CBS_ID,
                JAHMYR_GIBBS_CBS_ID,
                PUKA_NACUA_CBS_ID,
                TREY_MCBRIDE_CBS_ID,
            ],
        }
    )


def _mock_client(monkeypatch) -> None:
    def fake_get(self, url, **kwargs):
        position = next(p for p in _HTML_BY_POSITION if f"/{p}/" in url)
        request = httpx.Request("GET", url)
        return httpx.Response(200, text=_HTML_BY_POSITION[position], request=request)

    monkeypatch.setattr(httpx.Client, "get", fake_get)


class TestFetch:
    def test_fetch_parses_fixtures_into_canonical_schema(
        self, monkeypatch, crosswalk_reference
    ):
        _mock_client(monkeypatch)
        monkeypatch.setattr(cbs, "build_crosswalk", lambda: crosswalk_reference)

        result = CBSSource().fetch(season=2026)

        assert result.columns == CANONICAL_COLUMNS
        assert (result["source"] == "cbs").all()
        assert (result["week"] == 0).all()
        assert result.height > 0

    def test_extracts_team_from_player_name_cell(
        self, monkeypatch, crosswalk_reference
    ):
        _mock_client(monkeypatch)
        monkeypatch.setattr(cbs, "build_crosswalk", lambda: crosswalk_reference)

        result = CBSSource().fetch(season=2026)

        assert "Josh Allen" in result["player_name_raw"].to_list()
        allen_team = result.filter(pl.col("player_name_raw") == "Josh Allen")["team"]
        assert (allen_team == "BUF").all()

    def test_resolves_player_id_via_native_cbs_id(
        self, monkeypatch, crosswalk_reference
    ):
        _mock_client(monkeypatch)
        monkeypatch.setattr(cbs, "build_crosswalk", lambda: crosswalk_reference)

        result = CBSSource().fetch(season=2026)

        allen = result.filter(
            (pl.col("player_name_raw") == "Josh Allen")
            & (pl.col("stat_name") == "passing td")
        )
        assert allen.height == 1
        assert allen["player_id"].item() == "00-1111"
        assert allen["source_player_id"].item() == JOSH_ALLEN_CBS_ID

    def test_hand_computed_stat_values_for_josh_allen(
        self, monkeypatch, crosswalk_reference
    ):
        _mock_client(monkeypatch)
        monkeypatch.setattr(cbs, "build_crosswalk", lambda: crosswalk_reference)

        result = CBSSource().fetch(season=2026)
        allen = result.filter(pl.col("player_name_raw") == "Josh Allen")
        stats = dict(zip(allen["stat_name"], allen["stat_value"]))

        # Hand-verified against the live page these fixtures were captured from.
        assert stats["passing yard"] == 3704.0
        assert stats["passing td"] == 30.0
        assert stats["pass intercepted"] == 13.0
        assert stats["rushing yard"] == 610.0
        assert stats["rushing td"] == 10.0
        assert stats["fumble lost"] == 4.0

    def test_unmatched_native_id_resolves_to_null_player_id(self, monkeypatch):
        _mock_client(monkeypatch)
        empty_reference = pl.DataFrame(
            schema={"player_id": pl.String, "cbs_id": pl.String}
        )
        monkeypatch.setattr(cbs, "build_crosswalk", lambda: empty_reference)

        result = CBSSource().fetch(season=2026)

        assert result.height > 0
        assert result["player_id"].is_null().all()

    def test_never_makes_a_real_network_call(self, monkeypatch, crosswalk_reference):
        calls = []

        def fake_get(self, url, **kwargs):
            calls.append(url)
            position = next(p for p in _HTML_BY_POSITION if f"/{p}/" in url)
            request = httpx.Request("GET", url)
            return httpx.Response(
                200, text=_HTML_BY_POSITION[position], request=request
            )

        monkeypatch.setattr(httpx.Client, "get", fake_get)
        monkeypatch.setattr(cbs, "build_crosswalk", lambda: crosswalk_reference)

        CBSSource().fetch(season=2026)

        assert len(calls) == 4
        assert all(url.startswith("https://www.cbssports.com") for url in calls)
        assert all("/restofseason/" in url for url in calls)
