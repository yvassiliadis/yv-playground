"""Tests for ffdraft.metrics.consistency."""

from __future__ import annotations

import numpy as np
import polars as pl
import pytest

from ffdraft.config import RosterConfig
from ffdraft.metrics import consistency

RULES = {"reception": 1.0}


def _weekly_rows(rows: list[tuple]) -> pl.DataFrame:
    return pl.DataFrame(
        [
            {
                "season": season,
                "week": week,
                "source": "nflverse",
                "snapshot_date": "2025-01-01",
                "source_player_id": player_id,
                "player_id": player_id,
                "player_name_raw": player_id,
                "team": "SF",
                "position": position,
                "stat_name": "reception",
                "stat_value": float(value),
            }
            for season, week, player_id, position, value in rows
        ]
    )


def test_stddev_floor_ceiling_match_hand_computed_values():
    # 6 distinct, non-symmetric values: p25/p75 fall strictly between two
    # observations here, so linear and nearest interpolation genuinely
    # disagree (numpy linear gives 8.75/20.25; Polars' "nearest" default
    # would give 8.0/22.0) -- this fixture actually exercises
    # `interpolation="linear"` rather than coincidentally matching both.
    scores = [8.0, 15.0, 22.0, 6.0, 30.0, 11.0]
    weekly = _weekly_rows(
        [(2024, week, "p1", "WR", value) for week, value in enumerate(scores, start=1)]
    )

    result = consistency.compute_consistency(weekly, [2024], RULES)
    row = result.row(0, named=True)

    assert row["stddev"] == pytest.approx(np.std(scores, ddof=1))
    assert row["floor"] == pytest.approx(np.percentile(scores, 25))
    assert row["ceiling"] == pytest.approx(np.percentile(scores, 75))


def test_pct_weeks_above_baseline_uses_the_positional_starter_median():
    """Two WRs, one starter slot per team, one team: the baseline each week is
    just the single starter's own score, since roster_config makes the top-1
    WR the whole "starter pool". A player above the OTHER player's score every
    week should have pct_weeks_above_baseline computed against that pool, not
    against their own scores."""
    roster_config = RosterConfig(starters={"WR": 1}, bench=0)
    weekly = _weekly_rows(
        [
            (2024, 1, "high", "WR", 30.0),
            (2024, 2, "high", "WR", 30.0),
            (2024, 3, "high", "WR", 30.0),
            (2024, 1, "low", "WR", 10.0),
            (2024, 2, "low", "WR", 10.0),
            (2024, 3, "low", "WR", 10.0),
        ]
    )

    result = consistency.compute_consistency(
        weekly, [2024], RULES, roster_config=roster_config, teams=1
    )

    high = result.filter(pl.col("player_id") == "high").row(0, named=True)
    low = result.filter(pl.col("player_id") == "low").row(0, named=True)
    # Baseline each week is the top-1 (teams=1 * starters=1) scorer's score,
    # i.e. "high"'s own 30.0 -- "high" is never strictly above its own score,
    # and "low" (10.0) is never above 30.0 either.
    assert high["pct_weeks_above_baseline"] == pytest.approx(0.0)
    assert low["pct_weeks_above_baseline"] == pytest.approx(0.0)


def test_rookie_with_no_qualifying_history_gets_none_not_a_crash():
    weekly = _weekly_rows(
        [
            (2024, week, "veteran", "WR", value)
            for week, value in enumerate([10.0, 20.0, 15.0], start=1)
        ]
    )
    players = pl.DataFrame(
        {"player_id": ["veteran", "rookie"], "position": ["WR", "WR"]}
    )

    result = consistency.compute_consistency(weekly, [2024], RULES, players=players)

    assert result.height == 2  # rookie is not dropped
    rookie = result.filter(pl.col("player_id") == "rookie").row(0, named=True)
    for column in ("stddev", "floor", "ceiling", "pct_weeks_above_baseline"):
        assert rookie[column] is None
    veteran = result.filter(pl.col("player_id") == "veteran").row(0, named=True)
    assert veteran["stddev"] is not None


def test_below_min_qualifying_weeks_gets_none_even_with_some_history():
    """A player with real but too-sparse weekly history (e.g. injured after 1
    game) should not get a stddev computed from noise."""
    weekly = _weekly_rows([(2024, 1, "hurt", "WR", 25.0)])

    result = consistency.compute_consistency(weekly, [2024], RULES)
    row = result.row(0, named=True)
    assert row["stddev"] is None
    assert row["floor"] is None


def test_rejects_multiple_actual_sources():
    weekly = pl.concat(
        [
            _weekly_rows([(2024, 1, "p1", "WR", 10.0)]),
            _weekly_rows([(2024, 1, "p1", "WR", 12.0)]).with_columns(
                pl.lit("some_other_feed").alias("source")
            ),
        ]
    )
    with pytest.raises(ValueError, match="exactly one source"):
        consistency.compute_consistency(weekly, [2024], RULES)
