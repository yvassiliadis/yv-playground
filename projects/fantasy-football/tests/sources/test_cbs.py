"""Tests for ffdraft.sources.cbs -- fixture-based, no live HTTP.

**Synthetic fixtures.** Unlike fftoday/fantasysharks, CBS's live page
returned zero player data in its static HTML (a client-side-rendered React
app -- see `ffdraft.sources.cbs`'s module docstring for the live check that
established this). The fixtures under tests/fixtures/sources/cbs/ are
hand-authored to exercise the parser's best-effort, unverified guess at
CBS's classic server-rendered table shape -- they are illustrative, not
captured from a real response.
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
        assert result["source_player_id"].is_null().all()

    def test_splits_trailing_team_code_off_name(self, monkeypatch, crosswalk_reference):
        _mock_client(monkeypatch)
        monkeypatch.setattr(cbs, "build_crosswalk", lambda: crosswalk_reference)

        result = CBSSource().fetch(season=2026)

        assert "Josh Allen" in result["player_name_raw"].to_list()
        allen_team = result.filter(pl.col("player_name_raw") == "Josh Allen")["team"]
        assert (allen_team == "BUF").all()

    def test_resolves_player_id_via_name_match(self, monkeypatch, crosswalk_reference):
        _mock_client(monkeypatch)
        monkeypatch.setattr(cbs, "build_crosswalk", lambda: crosswalk_reference)

        result = CBSSource().fetch(season=2026)

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
        monkeypatch.setattr(cbs, "build_crosswalk", lambda: empty_reference)

        result = CBSSource().fetch(season=2026)

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
