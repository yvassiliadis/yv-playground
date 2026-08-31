"""Tests for ffdraft.sources.espn -- fixture-based, no live HTTP.

`sample_response.json` is a trimmed set of *real* players captured from
ESPN's live endpoint (see `ffdraft.sources.espn`'s module docstring) --
Luther Burden III, Geno Smith, Omarion Hampton -- with two additions on top
that don't come from the live response: Burden's second `stats` entry (a
weekly-actual block, added to prove the season/week filter works) and the
entire "Unrostered Rookie" player (added to exercise the unmatched-native-ID
path).
"""

import json
from pathlib import Path

import httpx
import polars as pl
import pytest

from ffdraft.sources import espn
from ffdraft.sources.base import CANONICAL_COLUMNS
from ffdraft.sources.espn import ESPNSource

FIXTURE_PATH = (
    Path(__file__).parent.parent
    / "fixtures"
    / "sources"
    / "espn"
    / "sample_response.json"
)


@pytest.fixture
def sample_payload() -> list[dict]:
    return json.loads(FIXTURE_PATH.read_text())


@pytest.fixture
def crosswalk_reference() -> pl.DataFrame:
    return pl.DataFrame(
        {
            "player_id": ["00-3333", "00-4444", "00-5555"],
            "espn_id": [4685278, 15864, 4685382],
            "name": ["Luther Burden III", "Geno Smith", "Omarion Hampton"],
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
        monkeypatch.setattr(espn, "build_crosswalk", lambda: crosswalk_reference)

        result = ESPNSource().fetch(season=2026)

        assert result.columns == CANONICAL_COLUMNS
        assert (result["source"] == "espn").all()
        assert (result["week"] == 0).all()

    def test_only_season_projection_row_is_used_not_weekly_actuals(
        self, monkeypatch, sample_payload, crosswalk_reference
    ):
        # Burden's fixture has a second stats entry for scoringPeriodId=1,
        # statSourceId=0 (a fabricated single-week actual) -- it must be
        # excluded from the season projection.
        _mock_client(monkeypatch, sample_payload)
        monkeypatch.setattr(espn, "build_crosswalk", lambda: crosswalk_reference)

        result = ESPNSource().fetch(season=2026)

        burden = result.filter(pl.col("source_player_id") == "4685278")
        stats = dict(zip(burden["stat_name"], burden["stat_value"]))
        assert stats["reception"] == pytest.approx(75.04624562)
        assert stats["receiving yard"] == pytest.approx(937.1764825)

    def test_resolves_player_id_via_espn_id_join(
        self, monkeypatch, sample_payload, crosswalk_reference
    ):
        _mock_client(monkeypatch, sample_payload)
        monkeypatch.setattr(espn, "build_crosswalk", lambda: crosswalk_reference)

        result = ESPNSource().fetch(season=2026)

        smith = result.filter(pl.col("source_player_id") == "15864")
        assert (smith["player_id"] == "00-4444").all()

    def test_expected_qb_stat_values_present(
        self, monkeypatch, sample_payload, crosswalk_reference
    ):
        _mock_client(monkeypatch, sample_payload)
        monkeypatch.setattr(espn, "build_crosswalk", lambda: crosswalk_reference)

        result = ESPNSource().fetch(season=2026)

        smith = result.filter(pl.col("source_player_id") == "15864")
        stats = dict(zip(smith["stat_name"], smith["stat_value"]))
        assert stats["passing yard"] == pytest.approx(3815.216003)
        assert stats["passing td"] == pytest.approx(20.4058202)
        assert stats["pass intercepted"] == pytest.approx(14.35380989)

    def test_resolves_team_via_pro_team_map(
        self, monkeypatch, sample_payload, crosswalk_reference
    ):
        _mock_client(monkeypatch, sample_payload)
        monkeypatch.setattr(espn, "build_crosswalk", lambda: crosswalk_reference)

        result = ESPNSource().fetch(season=2026)

        hampton = result.filter(pl.col("source_player_id") == "4685382")
        assert (hampton["team"] == "LAC").all()

    def test_unmatched_native_id_resolves_to_null_player_id(
        self, monkeypatch, sample_payload, crosswalk_reference
    ):
        _mock_client(monkeypatch, sample_payload)
        monkeypatch.setattr(espn, "build_crosswalk", lambda: crosswalk_reference)

        result = ESPNSource().fetch(season=2026)

        unrostered = result.filter(pl.col("source_player_id") == "9999999")
        assert unrostered.height > 0
        assert unrostered["player_id"].is_null().all()

    def test_unrecognized_pro_team_id_resolves_to_null_team(
        self, monkeypatch, sample_payload, crosswalk_reference
    ):
        _mock_client(monkeypatch, sample_payload)
        monkeypatch.setattr(espn, "build_crosswalk", lambda: crosswalk_reference)

        result = ESPNSource().fetch(season=2026)

        unrostered = result.filter(pl.col("source_player_id") == "9999999")
        assert unrostered["team"].is_null().all()

    def test_week_other_than_zero_not_implemented(self):
        with pytest.raises(NotImplementedError):
            ESPNSource().fetch(season=2026, week=1)
