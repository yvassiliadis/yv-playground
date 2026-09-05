"""Tests for ffdraft.sources.espn -- fixture-based, no live HTTP.

`sample_response.json` is a trimmed set of *real* players captured from
ESPN's live endpoint (see `ffdraft.sources.espn`'s module docstring) --
Luther Burden III, Geno Smith, Omarion Hampton -- with additions on top:

- Burden's second `stats` entry (a weekly-actual block, fabricated to prove
  the season/week filter works).
- Geno Smith's second `stats` entry, a `seasonId=2025` season-total block
  alongside his real `seasonId=2026` one. The *collision itself* is real
  and live-confirmed: the actual live response for Geno Smith carried
  exactly two blocks with identical `scoringPeriodId=0`/`statSourceId=1`,
  differing only in `seasonId` (2026 and 2025) -- this is what the
  `seasonId == season` check in `ffdraft.sources.espn._rows_from_payload`
  guards against. The *specific numeric values* in the 2025 block here are
  fabricated (not the real captured numbers, which weren't fully recorded
  before being discarded) but deliberately distinct from the 2026 block's
  real values, so a test asserting on the 2026 numbers would fail if the
  season filter picked the wrong block.
- Geno Smith's third `stats` entry, a `statSplitTypeId=2` ("rest of
  season") block alongside his real `statSplitTypeId=0` (full season)
  2026 one, both otherwise identical (`scoringPeriodId=0`/`statSourceId=1`/
  `seasonId=2026`). This collision is also real and live-confirmed:
  ESPN's live API carries both split types once a season is underway, and
  without the `statSplitTypeId == 0` check a plain `next()` match could
  non-deterministically pick the near-zero "rest of season" block instead
  of the genuine full-season projection. The tiny stat values in the
  fabricated splitTypeId=2 block (e.g. 60.0 passing yards) are deliberately
  implausible as a *full-season* total, mirroring the real live case where
  a late-season "rest of season" projection shrinks toward zero.
- The entire "Unrostered Rookie" player (fabricated, to exercise the
  unmatched-native-ID path).
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

    def test_qb_passing_yards_are_plausible_for_a_starter(
        self, monkeypatch, sample_payload, crosswalk_reference
    ):
        # Complements the exact-match assertion above with a plausibility
        # check that isn't keyed to the same live capture used to derive
        # the stat-ID mapping in the first place -- an exact-match test
        # alone can't independently catch a future ID-shift regression
        # (e.g. if "3" stopped meaning passing yards, an exact-match test
        # against a hand-copied constant would still need updating by hand,
        # but a range check catches wildly-wrong values like the original
        # reception-ID bug even without knowing the exact right answer).
        _mock_client(monkeypatch, sample_payload)
        monkeypatch.setattr(espn, "build_crosswalk", lambda: crosswalk_reference)

        result = ESPNSource().fetch(season=2026)

        smith = result.filter(pl.col("source_player_id") == "15864")
        stats = dict(zip(smith["stat_name"], smith["stat_value"]))
        assert 3000 <= stats["passing yard"] <= 5000
        assert 0 <= stats["passing td"] <= 60
        assert 0 <= stats["pass intercepted"] <= 30

    def test_season_collision_uses_the_requested_seasons_block(
        self, monkeypatch, sample_payload, crosswalk_reference
    ):
        # Regression test for a real bug: Geno Smith's fixture carries two
        # stats entries with the identical scoringPeriodId=0/statSourceId=1
        # shape, differing only in seasonId (2026 and a fabricated-but-
        # distinct 2025). Removing the `seasonId == season` guard in
        # `_rows_from_payload` would make this test fail (it would either
        # pick whichever block happens to come first, or double-count both).
        _mock_client(monkeypatch, sample_payload)
        monkeypatch.setattr(espn, "build_crosswalk", lambda: crosswalk_reference)

        result_2026 = ESPNSource().fetch(season=2026)
        smith_2026 = result_2026.filter(pl.col("source_player_id") == "15864")
        stats_2026 = dict(zip(smith_2026["stat_name"], smith_2026["stat_value"]))
        assert stats_2026["passing yard"] == pytest.approx(3815.216003)

        result_2025 = ESPNSource().fetch(season=2025)
        smith_2025 = result_2025.filter(pl.col("source_player_id") == "15864")
        stats_2025 = dict(zip(smith_2025["stat_name"], smith_2025["stat_value"]))
        assert stats_2025["passing yard"] == pytest.approx(4224.622681)

    def test_split_type_collision_uses_the_full_season_block(
        self, monkeypatch, sample_payload, crosswalk_reference
    ):
        # Regression test for a real bug: ESPN's live API carries both a
        # full-season projection (statSplitTypeId=0) and a continuously
        # updated "rest of season" projection (statSplitTypeId=2) under the
        # identical scoringPeriodId=0/statSourceId=1/seasonId shape. Geno
        # Smith's fixture adds a fabricated splitTypeId=2 block with a tiny,
        # implausible-as-a-season-total value (60.0 passing yards).
        # Removing the `statSplitTypeId == 0` guard would let this test
        # fail by picking that block instead of the real 3815.216003 one.
        _mock_client(monkeypatch, sample_payload)
        monkeypatch.setattr(espn, "build_crosswalk", lambda: crosswalk_reference)

        result = ESPNSource().fetch(season=2026)

        smith = result.filter(pl.col("source_player_id") == "15864")
        stats = dict(zip(smith["stat_name"], smith["stat_value"]))
        assert stats["passing yard"] == pytest.approx(3815.216003)

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
