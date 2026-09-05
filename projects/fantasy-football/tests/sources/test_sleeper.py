"""Tests for ffdraft.sources.sleeper -- fixture-based, no live HTTP.

The httpx call is mocked to return a saved fixture response
(tests/fixtures/sources/sleeper/sample_response.json); `build_crosswalk` is
monkeypatched to a small in-memory reference table so these tests don't
depend on nflreadpy or the network either.
"""

import json
from pathlib import Path

import httpx
import polars as pl
import pytest

from ffdraft.sources import sleeper
from ffdraft.sources.base import CANONICAL_COLUMNS
from ffdraft.sources.sleeper import SleeperSource

FIXTURE_PATH = (
    Path(__file__).parent.parent
    / "fixtures"
    / "sources"
    / "sleeper"
    / "sample_response.json"
)


@pytest.fixture
def sample_payload() -> list[dict]:
    return json.loads(FIXTURE_PATH.read_text())


@pytest.fixture
def crosswalk_reference() -> pl.DataFrame:
    return pl.DataFrame(
        {
            "player_id": ["00-0038xxx", "00-0039xxx"],
            "sleeper_id": [10217, 9509],
            "name": ["Clayton Tune", "Chris Rodriguez"],
        }
    )


def _mock_client(monkeypatch, payload: list[dict]) -> None:
    def fake_get(self, url, **kwargs):
        request = httpx.Request("GET", url)
        return httpx.Response(200, json=payload, request=request)

    monkeypatch.setattr(httpx.Client, "get", fake_get)


class TestFetch:
    def test_fetch_parses_fixture_into_canonical_schema(
        self, monkeypatch, sample_payload, crosswalk_reference
    ):
        _mock_client(monkeypatch, sample_payload)
        monkeypatch.setattr(sleeper, "build_crosswalk", lambda: crosswalk_reference)

        result = SleeperSource().fetch(season=2026)

        assert result.columns == CANONICAL_COLUMNS
        assert (result["source"] == "sleeper").all()
        assert (result["week"] == 0).all()
        assert (result["season"] == 2026).all()

    def test_fetch_never_makes_a_real_network_call(
        self, monkeypatch, sample_payload, crosswalk_reference
    ):
        # If this test somehow hit the network it would raise/hang rather
        # than use the fixture; asserting the mocked call path is exercised
        # is the real point of the fixture-only guarantee.
        calls = []

        def fake_get(self, url, **kwargs):
            calls.append(url)
            request = httpx.Request("GET", url)
            return httpx.Response(200, json=sample_payload, request=request)

        monkeypatch.setattr(httpx.Client, "get", fake_get)
        monkeypatch.setattr(sleeper, "build_crosswalk", lambda: crosswalk_reference)

        SleeperSource().fetch(season=2026)

        assert len(calls) == 1
        assert calls[0].startswith("https://api.sleeper.app/projections/nfl/2026")

    def test_resolves_player_id_via_sleeper_id_join(
        self, monkeypatch, sample_payload, crosswalk_reference
    ):
        _mock_client(monkeypatch, sample_payload)
        monkeypatch.setattr(sleeper, "build_crosswalk", lambda: crosswalk_reference)

        result = SleeperSource().fetch(season=2026)

        tune = result.filter(pl.col("source_player_id") == "10217")
        assert (tune["player_id"] == "00-0038xxx").all()

    def test_unmatched_native_id_resolves_to_null_player_id(
        self, monkeypatch, sample_payload, crosswalk_reference
    ):
        _mock_client(monkeypatch, sample_payload)
        monkeypatch.setattr(sleeper, "build_crosswalk", lambda: crosswalk_reference)

        result = SleeperSource().fetch(season=2026)

        unrostered = result.filter(pl.col("source_player_id") == "9999999")
        assert unrostered.height > 0
        assert unrostered["player_id"].is_null().all()

    def test_expected_stat_values_present(
        self, monkeypatch, sample_payload, crosswalk_reference
    ):
        _mock_client(monkeypatch, sample_payload)
        monkeypatch.setattr(sleeper, "build_crosswalk", lambda: crosswalk_reference)

        result = SleeperSource().fetch(season=2026)

        tucker = result.filter(pl.col("source_player_id") == "10213")
        stats = dict(zip(tucker["stat_name"], tucker["stat_value"]))
        assert stats["reception"] == 46.0
        assert stats["receiving yard"] == 582.0
        assert stats["receiving td"] == 3.0

    def test_week_other_than_zero_not_implemented(self):
        with pytest.raises(NotImplementedError):
            SleeperSource().fetch(season=2026, week=1)
