"""Tests for ffdraft.derive -- hand-computed expected values throughout."""

from pathlib import Path

import polars as pl

from ffdraft import derive, scoring

REPO_ROOT_DATA = Path(__file__).resolve().parents[1] / "data" / "scoring_rules.csv"


def _hist_row(player_id, position, stat_name, stat_value, season=2023, week=1):
    return {
        "season": season,
        "week": week,
        "source": "actuals",
        "snapshot_date": "2024-01-01",
        "source_player_id": player_id,
        "player_id": player_id,
        "player_name_raw": player_id,
        "team": "KC",
        "position": position,
        "stat_name": stat_name,
        "stat_value": stat_value,
    }


def _proj_row(player_id, position, stat_name, stat_value, season=2024, week=0):
    return {
        "season": season,
        "week": week,
        "source": "sleeper",
        "snapshot_date": "2024-08-01",
        "source_player_id": player_id,
        "player_id": player_id,
        "player_name_raw": player_id,
        "team": "KC",
        "position": position,
        "stat_name": stat_name,
        "stat_value": stat_value,
    }


# ---------------------------------------------------------------------------
# derive_first_downs
# ---------------------------------------------------------------------------


def test_derive_first_downs_shrinks_toward_positional_mean_and_scales_by_volume():
    # RB1 has a high true rate: 40 rushing 1st downs on 200 yards -> 0.20
    # rate. RB2 (positional-mean anchor) has a much lower rate: 10 first
    # downs on 200 yards -> 0.05. Positional totals: 50 first downs / 400
    # yards = 0.125 positional mean.
    #
    # RB1's own volume (200 yards) is well below the shrinkage prior
    # (300 yards), so RB1's shrunk rate must land strictly between RB1's own
    # rate (0.20) and the positional mean (0.125), pulled toward the mean:
    #   shrunk = (40 + 300*0.125) / (200 + 300) = (40 + 37.5) / 500 = 0.155
    historical = pl.DataFrame(
        [
            _hist_row("rb1", "RB", "rushing yard", 200.0),
            _hist_row("rb1", "RB", "rushing 1st down", 40.0),
            _hist_row("rb2", "RB", "rushing yard", 200.0),
            _hist_row("rb2", "RB", "rushing 1st down", 10.0),
        ]
    )
    projections = pl.DataFrame([_proj_row("rb1", "RB", "rushing yard", 250.0)])

    result = derive.derive_first_downs(projections, historical)

    rushing_result = result.filter(pl.col("stat_name") == "rushing 1st down")
    assert rushing_result.height == 1
    row = rushing_result.row(0, named=True)

    expected_shrunk_rate = (40.0 + 300.0 * 0.125) / (200.0 + 300.0)
    assert expected_shrunk_rate == 0.155
    assert 0.125 < expected_shrunk_rate < 0.20  # between mean and own rate

    expected_stat_value = expected_shrunk_rate * 250.0  # rate * projected volume
    assert row["stat_value"] == expected_stat_value
    assert row["player_id"] == "rb1"
    assert row["season"] == 2024
    assert row["source"] == "sleeper"


def test_derive_first_downs_new_player_falls_back_to_positional_mean():
    # rb3 has projected volume but zero rows in historical_actuals -- must
    # fall back to the pure positional mean rate (0.125, from the fixture
    # above), not crash or produce a null/zero rate.
    historical = pl.DataFrame(
        [
            _hist_row("rb1", "RB", "rushing yard", 200.0),
            _hist_row("rb1", "RB", "rushing 1st down", 40.0),
            _hist_row("rb2", "RB", "rushing yard", 200.0),
            _hist_row("rb2", "RB", "rushing 1st down", 10.0),
        ]
    )
    projections = pl.DataFrame([_proj_row("rb3", "RB", "rushing yard", 100.0)])

    result = derive.derive_first_downs(projections, historical)
    row = result.filter(pl.col("stat_name") == "rushing 1st down").row(0, named=True)

    assert row["stat_value"] == 0.125 * 100.0


def test_derive_first_downs_receiving_rate_uses_receptions_as_volume():
    # wr1: 20 receiving 1st downs on 50 receptions -> rate 0.40.
    # wr2: 5 receiving 1st downs on 50 receptions -> rate 0.10.
    # positional mean = 25 / 100 = 0.25.
    # wr1's volume (50) vs. prior (40): shrunk = (20 + 40*0.25)/(50+40) =
    # (20 + 10)/90 = 0.3333...
    historical = pl.DataFrame(
        [
            _hist_row("wr1", "WR", "reception", 50.0),
            _hist_row("wr1", "WR", "receiving 1st down", 20.0),
            _hist_row("wr2", "WR", "reception", 50.0),
            _hist_row("wr2", "WR", "receiving 1st down", 5.0),
        ]
    )
    projections = pl.DataFrame([_proj_row("wr1", "WR", "reception", 60.0)])

    result = derive.derive_first_downs(projections, historical)
    row = result.filter(pl.col("stat_name") == "receiving 1st down").row(0, named=True)

    expected_rate = (20.0 + 40.0 * 0.25) / (50.0 + 40.0)
    assert abs(row["stat_value"] - expected_rate * 60.0) < 1e-9


def test_derive_first_downs_zero_volume_position_falls_back_to_zero_not_nan(caplog):
    # QB has explicit zero rushing-yard volume in historical_actuals (so the
    # position's total volume is 0.0) -- the positional_rate division would
    # be 0/0 without the guard, silently becoming NaN and poisoning every
    # QB's shrunk_rate. With the guard, a QB projected for rushing yards
    # with no other historical signal falls back to a rate of 0.0, and the
    # fallback is logged rather than silent.
    historical = pl.DataFrame(
        [
            _hist_row("qb1", "QB", "rushing yard", 0.0),
            _hist_row("qb1", "QB", "rushing 1st down", 0.0),
        ]
    )
    projections = pl.DataFrame([_proj_row("qb1", "QB", "rushing yard", 20.0)])

    with caplog.at_level("WARNING"):
        result = derive.derive_first_downs(projections, historical)

    row = result.filter(pl.col("stat_name") == "rushing 1st down").row(0, named=True)
    assert row["stat_value"] == 0.0  # falls back to 0.0, not NaN/inf
    assert "zero total historical" in caplog.text


# ---------------------------------------------------------------------------
# derive_dst_expected_points
# ---------------------------------------------------------------------------


def _dst_row(team, season, week, stat_name, stat_value):
    return {
        "season": season,
        "week": week,
        "team": team,
        "stat_name": stat_name,
        "stat_value": stat_value,
    }


def test_derive_dst_expected_points_matches_hand_computed_decay_weighted_score():
    # Two seasons of team-level granular stats for one team, one stat
    # ("defense 3 and out", worth 0.25 points each per scoring_rules.csv).
    # 2024 (more recent): 4.0 per game average (weeks 1-2: 3, 5 -> avg 4.0).
    # 2023 (older): 2.0 per game average (weeks 1-2: 1, 3 -> avg 2.0).
    historical = pl.DataFrame(
        [
            _dst_row("KC", 2023, 1, "defense 3 and out", 1.0),
            _dst_row("KC", 2023, 2, "defense 3 and out", 3.0),
            _dst_row("KC", 2024, 1, "defense 3 and out", 3.0),
            _dst_row("KC", 2024, 2, "defense 3 and out", 5.0),
        ]
    )

    decay = 0.35
    most_recent_season = 2024
    # weight(2024) = 0.35**0 = 1.0, weight(2023) = 0.35**1 = 0.35
    weight_2024 = decay ** (most_recent_season - 2024)
    weight_2023 = decay ** (most_recent_season - 2023)
    assert weight_2024 == 1.0
    assert weight_2023 == 0.35

    avg_2024 = 4.0
    avg_2023 = 2.0
    expected_weighted_avg = (avg_2024 * weight_2024 + avg_2023 * weight_2023) / (
        weight_2024 + weight_2023
    )
    # (4.0*1.0 + 2.0*0.35) / (1.0 + 0.35) = 4.7 / 1.35
    assert abs(expected_weighted_avg - (4.7 / 1.35)) < 1e-9

    # Score that decay-weighted average directly via scoring.score() to get
    # the hand-computed expected fantasy_points, using the real scoring CSV.
    rules_path = REPO_ROOT_DATA
    rules = scoring.load_scoring_rules(rules_path)
    points_per_unit = rules["defense 3 and out"]
    assert points_per_unit == 0.25
    expected_fantasy_points = expected_weighted_avg * points_per_unit

    result = derive.derive_dst_expected_points(historical, decay=decay)

    row = result.filter(pl.col("player_id") == "DST_KC").row(0, named=True)
    assert abs(row["fantasy_points"] - expected_fantasy_points) < 1e-9
    assert row["season"] == 2025  # most_recent_season + 1
    assert row["week"] == 0
    assert row["source"] == "derived_dst"


def test_derive_dst_expected_points_divides_by_games_played_not_rows_present():
    """`ingest.actuals` writes DST rows SPARSELY: a bucket gets a
    `stat_value = 1.0` row only in the weeks it applied (no zero row
    otherwise), and zero-valued counting stats are dropped entirely. So the
    per-game average must be `sum(stat_value) / games played`, not a mean
    over the rows that happen to be present -- a mean over present rows makes
    every bucket flag average to exactly 1.0 (i.e. "applied in every game").

    One team, one season, four games (weeks 1-4), hand-computed below.
    """
    rules = scoring.load_scoring_rules(REPO_ROOT_DATA)
    historical = pl.DataFrame(
        [
            # Sacks: 2 in week 1, 3 in week 3, none in weeks 2/4 (no rows).
            _dst_row("KC", 2024, 1, "sacks", 2.0),
            _dst_row("KC", 2024, 3, "sacks", 3.0),
            # One interception, in week 2 only.
            _dst_row("KC", 2024, 2, "defense interception", 1.0),
            # Points-allowed buckets: one per game, mutually exclusive.
            _dst_row("KC", 2024, 1, "0-13 pts allowed", 1.0),
            _dst_row("KC", 2024, 2, "0-13 pts allowed", 1.0),
            _dst_row("KC", 2024, 3, "14-20 pts allowed", 1.0),
            _dst_row("KC", 2024, 4, "14-20 pts allowed", 1.0),
            # Yards-allowed buckets: one per game, mutually exclusive.
            _dst_row("KC", 2024, 1, "0-349 yds allowed", 1.0),
            _dst_row("KC", 2024, 2, "0-349 yds allowed", 1.0),
            _dst_row("KC", 2024, 3, "0-349 yds allowed", 1.0),
            _dst_row("KC", 2024, 4, "350-399 yds allowed", 1.0),
        ]
    )

    games = 4  # weeks 1-4 -- the true denominator
    # Per-game rates, each `sum(stat_value) / games`:
    #   sacks                 5/4 = 1.25   * 1.0 pts =  1.25
    #   defense interception  1/4 = 0.25   * 2.0 pts =  0.50
    #   0-13 pts allowed      2/4 = 0.50   * 0.0 pts =  0.00
    #   14-20 pts allowed     2/4 = 0.50   * -1.0 pts = -0.50
    #   0-349 yds allowed     3/4 = 0.75   * 0.0 pts =  0.00
    #   350-399 yds allowed   1/4 = 0.25   * -1.0 pts = -0.25
    #                                        total   =  1.00
    assert rules["sacks"] == 1.0
    assert rules["defense interception"] == 2.0
    assert rules["0-13 pts allowed"] == 0.0
    assert rules["14-20 pts allowed"] == -1.0
    assert rules["0-349 yds allowed"] == 0.0
    assert rules["350-399 yds allowed"] == -1.0
    expected = (
        (5.0 / games) * rules["sacks"]
        + (1.0 / games) * rules["defense interception"]
        + (2.0 / games) * rules["0-13 pts allowed"]
        + (2.0 / games) * rules["14-20 pts allowed"]
        + (3.0 / games) * rules["0-349 yds allowed"]
        + (1.0 / games) * rules["350-399 yds allowed"]
    )
    assert expected == 1.0

    result = derive.derive_dst_expected_points(historical, rules=rules)
    row = result.filter(pl.col("player_id") == "DST_KC").row(0, named=True)
    assert abs(row["fantasy_points"] - expected) < 1e-9

    # The old present-rows-only mean would have made every bucket flag 1.0
    # and sacks 2.5/game: 2.5 + 2.0 + 0.0 - 1.0 + 0.0 - 1.0 = 2.5. Pin that
    # down so a regression to `.mean()` cannot pass this test.
    mean_over_present_rows = (
        2.5 * rules["sacks"]
        + 1.0 * rules["defense interception"]
        + 1.0 * rules["0-13 pts allowed"]
        + 1.0 * rules["14-20 pts allowed"]
        + 1.0 * rules["0-349 yds allowed"]
        + 1.0 * rules["350-399 yds allowed"]
    )
    assert mean_over_present_rows == 2.5
    assert abs(row["fantasy_points"] - mean_over_present_rows) > 1e-9


def test_derive_dst_expected_points_counts_games_from_weeks_not_stat_rows():
    """A team with a quiet game still has that game in the denominator: the
    week is counted from any row the team has, not from rows of the stat
    being averaged."""
    rules = scoring.load_scoring_rules(REPO_ROOT_DATA)
    # Weeks 1-3 all played; only week 1 recorded a sack.
    historical = pl.DataFrame(
        [
            _dst_row("KC", 2024, 1, "sacks", 3.0),
            _dst_row("KC", 2024, 2, "0-13 pts allowed", 1.0),
            _dst_row("KC", 2024, 3, "0-13 pts allowed", 1.0),
        ]
    )

    result = derive.derive_dst_expected_points(historical, rules=rules)
    row = result.filter(pl.col("player_id") == "DST_KC").row(0, named=True)

    # 3 sacks / 3 games = 1.0/game * 1 pt; the pts-allowed bucket is worth 0.
    assert abs(row["fantasy_points"] - 1.0) < 1e-9


def test_derive_dst_expected_points_empty_input_returns_empty_not_crash(caplog):
    # historical_team_stats has rows, but none matching any DST-derived
    # stat_name -- per_season ends up empty, so `.max()` on its "season"
    # column would be None. Without the guard, `None - pl.col("season")`
    # (the decay exponent) and `None + 1` (the target season literal) both
    # raise/propagate nulls uninformatively. With the guard, this returns
    # an empty, correctly-typed result and logs why.
    historical = pl.DataFrame(
        [_dst_row("KC", 2023, 1, "passing yard", 300.0)]  # not a DST stat
    )

    with caplog.at_level("WARNING"):
        result = derive.derive_dst_expected_points(historical)

    assert result.is_empty()
    assert result.columns == ["season", "week", "source", "player_id", "fantasy_points"]
    assert "no rows matching" in caplog.text


# ---------------------------------------------------------------------------
# derive_kicker_fg_buckets
# ---------------------------------------------------------------------------


def test_derive_kicker_fg_buckets_splits_total_by_given_distribution():
    dist = {"0-39 FG made": 0.65, "40-49 FG made": 0.25, "5+ FG made": 0.10}
    projections = pl.DataFrame(
        [
            _proj_row("kicker1", "K", derive.TOTAL_FG_STAT_NAME, 20.0),
            _proj_row("kicker1", "K", "PAT made", 35.0),
        ]
    )

    result = derive.derive_kicker_fg_buckets(projections, dist)

    fg_rows = result.filter(pl.col("stat_name").is_in(dist.keys()))
    values = dict(zip(fg_rows["stat_name"], fg_rows["stat_value"]))
    assert values == {
        "0-39 FG made": 20.0 * 0.65,
        "40-49 FG made": 20.0 * 0.25,
        "5+ FG made": 20.0 * 0.10,
    }
    # sums back to the original total
    assert abs(sum(values.values()) - 20.0) < 1e-9

    # non-FG rows pass through untouched
    pat_row = result.filter(pl.col("stat_name") == "PAT made").row(0, named=True)
    assert pat_row["stat_value"] == 35.0

    # the original undifferentiated total row is gone
    assert result.filter(pl.col("stat_name") == derive.TOTAL_FG_STAT_NAME).is_empty()


def test_derive_kicker_fg_buckets_passes_through_already_bucketed_rows():
    dist = derive.DEFAULT_LEAGUE_FG_DISTANCE_DIST
    projections = pl.DataFrame(
        [
            _proj_row("kicker2", "K", "0-39 FG made", 10.0),
            _proj_row("kicker2", "K", "40-49 FG made", 4.0),
            _proj_row("kicker2", "K", "5+ FG made", 1.0),
        ]
    )

    result = derive.derive_kicker_fg_buckets(projections, dist)

    assert result.sort("stat_name").equals(projections.sort("stat_name"))
