"""Tests for ffdraft.sources.numberfire -- fixture-based, no live HTTP.

**Synthetic fixtures.** A live `curl` to numberFire's projections page
returned HTTP 500 with FanDuel's generic sportsbook error page body (see
`ffdraft.sources.numberfire`'s module docstring) -- there is no live markup
at all to check this parser against, making numberFire the lowest-confidence
source in this task. The fixtures here are hand-authored to exercise the
parser's best-effort, unverified guess at numberFire's table shape.
"""

from pathlib import Path

import httpx
import polars as pl
import pytest

from ffdraft.sources import numberfire
from ffdraft.sources.base import CANONICAL_COLUMNS
from ffdraft.sources.numberfire import NumberFireSource

FIXTURE_DIR = Path(__file__).parent.parent / "fixtures" / "sources" / "numberfire"

_HTML_BY_QUERY_POSITION = {
    "qb": (FIXTURE_DIR / "sample_qb.html").read_text(),
    "rb": (FIXTURE_DIR / "sample_rb.html").read_text(),
    "wr": (FIXTURE_DIR / "sample_wr.html").read_text(),
    "te": (FIXTURE_DIR / "sample_te.html").read_text(),
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
        query_position = kwargs["params"]["position"]
        request = httpx.Request("GET", url)
        return httpx.Response(
            200, text=_HTML_BY_QUERY_POSITION[query_position], request=request
        )

    monkeypatch.setattr(httpx.Client, "get", fake_get)


class TestFetch:
    def test_fetch_parses_fixtures_into_canonical_schema(
        self, monkeypatch, crosswalk_reference
    ):
        _mock_client(monkeypatch)
        monkeypatch.setattr(numberfire, "build_crosswalk", lambda: crosswalk_reference)

        result = NumberFireSource().fetch(season=2026)

        assert result.columns == CANONICAL_COLUMNS
        assert (result["source"] == "numberfire").all()
        assert (result["week"] == 0).all()
        assert result["source_player_id"].is_null().all()

    def test_fetches_one_position_at_a_time_and_concatenates(
        self, monkeypatch, crosswalk_reference
    ):
        seen_positions = []

        def fake_get(self, url, **kwargs):
            query_position = kwargs["params"]["position"]
            seen_positions.append(query_position)
            request = httpx.Request("GET", url)
            return httpx.Response(
                200, text=_HTML_BY_QUERY_POSITION[query_position], request=request
            )

        monkeypatch.setattr(httpx.Client, "get", fake_get)
        monkeypatch.setattr(numberfire, "build_crosswalk", lambda: crosswalk_reference)

        NumberFireSource().fetch(season=2026)

        assert sorted(seen_positions) == ["qb", "rb", "te", "wr"]

    def test_resolves_player_id_via_name_match(self, monkeypatch, crosswalk_reference):
        _mock_client(monkeypatch)
        monkeypatch.setattr(numberfire, "build_crosswalk", lambda: crosswalk_reference)

        result = NumberFireSource().fetch(season=2026)

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
        monkeypatch.setattr(numberfire, "build_crosswalk", lambda: empty_reference)

        result = NumberFireSource().fetch(season=2026)

        assert result["player_id"].is_null().all()

    def test_never_makes_a_real_network_call(self, monkeypatch, crosswalk_reference):
        calls = []

        def fake_get(self, url, **kwargs):
            calls.append(url)
            query_position = kwargs["params"]["position"]
            request = httpx.Request("GET", url)
            return httpx.Response(
                200, text=_HTML_BY_QUERY_POSITION[query_position], request=request
            )

        monkeypatch.setattr(httpx.Client, "get", fake_get)
        monkeypatch.setattr(numberfire, "build_crosswalk", lambda: crosswalk_reference)

        NumberFireSource().fetch(season=2026)

        assert len(calls) == 4
        assert all(url.startswith("https://www.numberfire.com") for url in calls)
