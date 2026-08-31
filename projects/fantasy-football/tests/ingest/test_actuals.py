"""Tests for ffdraft.ingest.actuals -- reshaping only, no live network."""

import datetime as dt

import polars as pl
import pytest

from ffdraft.ingest import actuals


@pytest.fixture
def player_stats_fixture() -> pl.DataFrame:
    """Two players, two weeks, a handful of mapped stat columns."""
    base = {
        "season": [2023, 2023, 2023, 2023],
        "week": [1, 2, 1, 2],
        "player_id": ["00-0033873", "00-0033873", "00-0034796", "00-0034796"],
        "player_display_name": [
            "Patrick Mahomes",
            "Patrick Mahomes",
            "Travis Kelce",
            "Travis Kelce",
        ],
        "team": ["KC", "KC", "KC", "KC"],
        "position": ["QB", "QB", "TE", "TE"],
        "passing_yards": [300, 250, 0, 0],
        "passing_tds": [3, 1, 0, 0],
        "passing_2pt_conversions": [0, 0, 0, 0],
        "passing_interceptions": [1, 0, 0, 0],
        "rushing_yards": [10, 5, 0, 0],
        "rushing_tds": [0, 0, 0, 0],
        "rushing_2pt_conversions": [0, 0, 0, 0],
        "receptions": [0, 0, 6, 4],
        "receiving_yards": [0, 0, 80, 45],
        "receiving_tds": [0, 0, 1, 0],
        "receiving_2pt_conversions": [0, 0, 0, 0],
        "sack_fumbles_lost": [0, 1, 0, 0],
        "rushing_fumbles_lost": [0, 0, 0, 0],
        "receiving_fumbles_lost": [0, 0, 0, 1],
    }
    return pl.DataFrame(base)


def test_reshape_player_stats_has_canonical_columns(player_stats_fixture):
    long = actuals._reshape_player_stats(player_stats_fixture)
    assert long.columns == actuals.CANONICAL_COLUMNS


def test_reshape_player_stats_row_count_and_values(player_stats_fixture):
    long = actuals._reshape_player_stats(player_stats_fixture)

    # only nonzero stat cells survive the unpivot: mahomes wk1 (4), wk2 (4),
    # kelce wk1 (3), wk2 (3)
    assert long.height == 14

    mahomes_wk1 = long.filter(
        (pl.col("player_id") == "00-0033873") & (pl.col("week") == 1)
    ).sort("stat_name")
    stat_values = dict(zip(mahomes_wk1["stat_name"], mahomes_wk1["stat_value"]))
    assert stat_values == {
        "passing yard": 300.0,
        "passing td": 3.0,
        "pass intercepted": 1.0,
        "rushing yard": 10.0,
    }

    mahomes_wk2 = long.filter(
        (pl.col("player_id") == "00-0033873") & (pl.col("week") == 2)
    )
    fumble_row = mahomes_wk2.filter(pl.col("stat_name") == "fumble lost")
    assert fumble_row["stat_value"].item() == 1.0

    kelce_wk1 = long.filter(
        (pl.col("player_id") == "00-0034796") & (pl.col("week") == 1)
    )
    kelce_stats = dict(zip(kelce_wk1["stat_name"], kelce_wk1["stat_value"]))
    assert kelce_stats == {
        "reception": 6.0,
        "receiving yard": 80.0,
        "receiving td": 1.0,
    }


def test_reshape_player_stats_metadata_columns(player_stats_fixture):
    long = actuals._reshape_player_stats(player_stats_fixture)
    row = long.row(0, named=True)
    assert row["source"] == "actuals"
    assert row["snapshot_date"] == dt.datetime.now(tz=dt.UTC).date()
    assert row["source_player_id"] == row["player_id"]
    assert row["player_name_raw"] in {"Patrick Mahomes", "Travis Kelce"}


def test_load_weekly_actuals_calls_nflreadpy(monkeypatch, player_stats_fixture):
    captured = {}

    def fake_load_player_stats(seasons, summary_level):
        captured["seasons"] = seasons
        captured["summary_level"] = summary_level
        return player_stats_fixture

    monkeypatch.setattr(actuals.nfl, "load_player_stats", fake_load_player_stats)

    result = actuals.load_weekly_actuals([2023])

    assert captured == {"seasons": [2023], "summary_level": "week"}
    assert result.columns == actuals.CANONICAL_COLUMNS
    assert result.height > 0


@pytest.fixture
def team_stats_fixture() -> pl.DataFrame:
    """One game, two teams (KC home, DEN away).

    KC records a defensive TD and a special-teams return TD (two distinct
    TD types in the same week); DEN records a fumble-recovery TD. This
    exercises all three canonical TD stats without any of them overlapping.
    """
    return pl.DataFrame(
        {
            "season": [2023, 2023],
            "week": [1, 1],
            "team": ["KC", "DEN"],
            "opponent_team": ["DEN", "KC"],
            "game_id": ["2023_01_DEN_KC", "2023_01_DEN_KC"],
            "passing_yards": [250, 180],
            "rushing_yards": [100, 70],
            "def_sacks": [3, 1],
            "def_interceptions": [2, 0],
            "fumble_recovery_opp": [1, 0],
            "def_tds": [1, 0],
            "special_teams_tds": [1, 0],
            "fumble_recovery_tds": [0, 1],
        }
    )


@pytest.fixture
def schedules_fixture() -> pl.DataFrame:
    return pl.DataFrame(
        {
            "game_id": ["2023_01_DEN_KC"],
            "home_team": ["KC"],
            "home_score": [30],
            "away_team": ["DEN"],
            "away_score": [10],
        }
    )


def test_reshape_dst_stats_has_canonical_columns(team_stats_fixture, schedules_fixture):
    long = actuals._reshape_dst_stats(team_stats_fixture, schedules_fixture)
    assert long.columns == actuals.CANONICAL_COLUMNS


def test_reshape_dst_stats_counting_and_buckets(team_stats_fixture, schedules_fixture):
    long = actuals._reshape_dst_stats(team_stats_fixture, schedules_fixture)

    kc = long.filter(pl.col("team") == "KC")
    kc_stats = dict(zip(kc["stat_name"], kc["stat_value"]))

    # KC's defense: 3 sacks, 2 INTs, 1 fumble recovery, 1 defense TD, 1 special teams TD
    assert kc_stats["sacks"] == 3.0
    assert kc_stats["defense interception"] == 2.0
    assert kc_stats["fumble recovery"] == 1.0
    assert kc_stats["defense td"] == 1.0
    assert kc_stats["special teams td"] == 1.0
    assert "fumble recovery td" not in kc_stats

    # KC allowed DEN's 180 pass + 70 rush = 250 yards, and DEN's score (10 pts)
    assert kc_stats["0-349 yds allowed"] == 1.0
    assert kc_stats["0-13 pts allowed"] == 1.0

    den = long.filter(pl.col("team") == "DEN")
    den_stats = dict(zip(den["stat_name"], den["stat_value"]))
    # DEN's defense recorded 1 sack and 1 fumble-recovery TD, but no INTs,
    # fumble recoveries, defense TDs, or special-teams TDs -> those rows are dropped
    assert den_stats["sacks"] == 1.0
    assert den_stats["fumble recovery td"] == 1.0
    assert "defense interception" not in den_stats
    assert "fumble recovery" not in den_stats
    assert "defense td" not in den_stats
    assert "special teams td" not in den_stats
    # DEN allowed KC's 250 pass + 100 rush = 350 yards, and KC's score (30 pts)
    assert den_stats["350-399 yds allowed"] == 1.0
    assert den_stats["28-34 pts allowed"] == 1.0


def test_dst_player_id_is_synthetic(team_stats_fixture, schedules_fixture):
    long = actuals._reshape_dst_stats(team_stats_fixture, schedules_fixture)
    kc_ids = long.filter(pl.col("team") == "KC")["player_id"].unique().to_list()
    assert kc_ids == ["DST_KC"]


def test_load_dst_actuals_calls_nflreadpy(
    monkeypatch, team_stats_fixture, schedules_fixture
):
    captured = {}

    def fake_load_team_stats(seasons, summary_level):
        captured["team_stats_seasons"] = seasons
        captured["summary_level"] = summary_level
        return team_stats_fixture

    def fake_load_schedules(seasons):
        captured["schedules_seasons"] = seasons
        return schedules_fixture

    monkeypatch.setattr(actuals.nfl, "load_team_stats", fake_load_team_stats)
    monkeypatch.setattr(actuals.nfl, "load_schedules", fake_load_schedules)

    result = actuals.load_dst_actuals([2023])

    assert captured["team_stats_seasons"] == [2023]
    assert captured["schedules_seasons"] == [2023]
    assert result.columns == actuals.CANONICAL_COLUMNS
    assert result.height > 0


def test_reshape_dst_stats_skips_unresolved_game(schedules_fixture):
    """A game_id missing from schedules (e.g. a bye week) must not fall into a bucket."""
    team_stats = pl.DataFrame(
        {
            "season": [2023],
            "week": [1],
            "team": ["CHI"],
            "opponent_team": ["MIA"],
            "game_id": ["2023_01_CHI_MIA"],  # not present in schedules_fixture
            "passing_yards": [200],
            "rushing_yards": [80],
            "def_sacks": [0],
            "def_interceptions": [0],
            "fumble_recovery_opp": [0],
            "def_tds": [0],
            "special_teams_tds": [0],
            "fumble_recovery_tds": [0],
        }
    )

    long = actuals._reshape_dst_stats(team_stats, schedules_fixture)

    chi_stats = long.filter(pl.col("team") == "CHI")["stat_name"].to_list()
    assert not any("pts allowed" in name for name in chi_stats)
    assert not any("yds allowed" in name for name in chi_stats)
