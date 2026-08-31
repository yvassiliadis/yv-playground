"""Tests for ffdraft.metrics.vor."""

from __future__ import annotations

import polars as pl
import pytest

from ffdraft.config import RosterConfig
from ffdraft.metrics import vor

# ---------------------------------------------------------------------------
# compute_vor: N_pos replacement selection.
# ---------------------------------------------------------------------------


def test_compute_vor_selects_n_pos_from_adp_top_100():
    """7 WRs sit inside a hand-constructed ADP top 100; the 7th-ranked-by-EP
    WR is the replacement, so the 7th WR's VOR must be exactly 0."""
    blended = pl.DataFrame(
        {
            "player_id": [f"wr{i}" for i in range(1, 8)],
            "position": ["WR"] * 7,
            "EP": [200.0, 190.0, 180.0, 170.0, 160.0, 150.0, 140.0],
        }
    )
    adp = pl.DataFrame(
        {
            "player_id": [f"wr{i}" for i in range(1, 8)]
            + [f"rb{i}" for i in range(93)],
            "position": ["WR"] * 7 + ["RB"] * 93,
            "adp": list(range(1, 8)) + list(range(8, 101)),
        }
    )

    result = vor.compute_vor(blended, adp, teams=10)

    replacement_ep = 140.0  # the 7th (last) WR in the top-100 ADP pool
    for row in result.iter_rows(named=True):
        assert row["VOR"] == pytest.approx(row["EP"] - replacement_ep)
    assert result.filter(pl.col("player_id") == "wr7")["VOR"][0] == pytest.approx(0.0)


def test_compute_vor_fixes_k_dst_replacement_at_5_regardless_of_adp():
    """K/DST use N=5 even when the ADP pool has almost none of that position."""
    blended = pl.DataFrame(
        {
            "player_id": [f"k{i}" for i in range(1, 11)],
            "position": ["K"] * 10,
            "EP": [100.0, 95.0, 90.0, 85.0, 80.0, 75.0, 70.0, 65.0, 60.0, 55.0],
        }
    )
    # Only 2 kickers appear anywhere near the ADP top 100.
    adp = pl.DataFrame(
        {"player_id": ["k1", "k2"], "position": ["K", "K"], "adp": [150, 151]}
    )

    result = vor.compute_vor(blended, adp, teams=10)

    replacement_ep = 80.0  # 5th-ranked K by EP
    assert result.filter(pl.col("player_id") == "k5")["VOR"][0] == pytest.approx(0.0)
    assert result.filter(pl.col("player_id") == "k1")["VOR"][0] == pytest.approx(
        100.0 - replacement_ep
    )


def test_compute_vor_clamps_when_fewer_players_than_n_pos():
    """A position with fewer ranked players than N_pos uses its worst player
    as the replacement, rather than raising or returning a null VOR."""
    blended = pl.DataFrame(
        {"player_id": ["te1", "te2"], "position": ["TE", "TE"], "EP": [120.0, 90.0]}
    )
    adp = pl.DataFrame(
        {
            "player_id": [f"te{i}" for i in range(1, 13)],
            "position": ["TE"] * 12,
            "adp": list(range(1, 13)),
        }
    )

    result = vor.compute_vor(blended, adp, teams=10)
    assert result.filter(pl.col("player_id") == "te2")["VOR"][0] == pytest.approx(0.0)


# ---------------------------------------------------------------------------
# risk_adjusted_vor
# ---------------------------------------------------------------------------


def test_risk_adjusted_vor_from_quantile_spread():
    df = pl.DataFrame(
        {
            "player_id": ["a", "b"],
            "position": ["WR", "WR"],
            "EP": [100.0, 80.0],
            "VOR": [20.0, 0.0],
            "EP_p25": [80.0, 70.0],
            "EP_p75": [120.0, 90.0],
        }
    )
    result = vor.risk_adjusted_vor(df, lambda_risk=1.0)

    sigma_a = (120.0 - 80.0) / 1.34898
    sigma_b = (90.0 - 70.0) / 1.34898
    assert result.filter(pl.col("player_id") == "a")["risk_adjusted_vor"][
        0
    ] == pytest.approx(20.0 - sigma_a)
    assert result.filter(pl.col("player_id") == "b")["risk_adjusted_vor"][
        0
    ] == pytest.approx(0.0 - sigma_b)


def test_risk_adjusted_vor_falls_back_to_stddev_column():
    df = pl.DataFrame(
        {
            "player_id": ["a"],
            "position": ["WR"],
            "EP": [100.0],
            "VOR": [20.0],
            "stddev": [5.0],
        }
    )
    result = vor.risk_adjusted_vor(df, lambda_risk=2.0)
    assert result["risk_adjusted_vor"][0] == pytest.approx(20.0 - 2.0 * 5.0)


def test_risk_adjusted_vor_requires_a_variance_source():
    df = pl.DataFrame(
        {"player_id": ["a"], "position": ["WR"], "EP": [100.0], "VOR": [20.0]}
    )
    with pytest.raises(ValueError, match="sigma"):
        vor.risk_adjusted_vor(df)


# ---------------------------------------------------------------------------
# dropoff_value
# ---------------------------------------------------------------------------


def test_dropoff_value_picks_the_comparator_available_picks_ahead():
    df = pl.DataFrame(
        {
            "player_id": ["a", "b", "c", "d", "e"],
            "position": ["RB"] * 5,
            "EP": [100.0, 90.0, 80.0, 70.0, 60.0],
        }
    )
    adp = pl.DataFrame(
        {"player_id": ["a", "b", "c", "d", "e"], "adp": [1, 2, 3, 13, 14]}
    )

    result = vor.dropoff_value(df, adp, picks_ahead=10)

    # a's threshold is adp >= 11: the best RB left is d (EP=70).
    assert result.filter(pl.col("player_id") == "a")["dropoff_value"][
        0
    ] == pytest.approx(30.0)
    # c's threshold is adp >= 13: d (adp=13) qualifies, EP=70.
    assert result.filter(pl.col("player_id") == "c")["dropoff_value"][
        0
    ] == pytest.approx(10.0)
    # d and e have nobody left at or past their threshold.
    assert result.filter(pl.col("player_id") == "d")["dropoff_value"][0] is None
    assert result.filter(pl.col("player_id") == "e")["dropoff_value"][0] is None


def test_dropoff_value_is_position_scoped():
    """A high-EP player at a different position must never be the comparator."""
    df = pl.DataFrame(
        {
            "player_id": ["rb1", "wr1"],
            "position": ["RB", "WR"],
            "EP": [100.0, 999.0],
        }
    )
    adp = pl.DataFrame({"player_id": ["rb1", "wr1"], "adp": [1, 11]})
    result = vor.dropoff_value(df, adp, picks_ahead=10)
    assert result.filter(pl.col("player_id") == "rb1")["dropoff_value"][0] is None


# ---------------------------------------------------------------------------
# roster_math_replacement / positional_zscore
# ---------------------------------------------------------------------------


def test_roster_math_replacement_uses_starters_times_teams():
    roster_config = RosterConfig(starters={"WR": 2}, bench=6)
    blended = pl.DataFrame(
        {
            "player_id": [f"wr{i}" for i in range(1, 5)],
            "position": ["WR"] * 4,
            "EP": [100.0, 90.0, 80.0, 70.0],
        }
    )
    # teams=2 -> N_pos = 2 * 2 = 4 -> replacement is the 4th-ranked WR.
    result = vor.roster_math_replacement(blended, roster_config=roster_config, teams=2)
    assert result.filter(pl.col("player_id") == "wr4")["roster_math_vor"][
        0
    ] == pytest.approx(0.0)
    assert result.filter(pl.col("player_id") == "wr1")["roster_math_vor"][
        0
    ] == pytest.approx(30.0)


def test_positional_zscore_is_scoped_per_position():
    df = pl.DataFrame(
        {
            "player_id": ["wr1", "wr2", "rb1"],
            "position": ["WR", "WR", "RB"],
            "EP": [100.0, 80.0, 500.0],
        }
    )
    result = vor.positional_zscore(df)
    # A single-player position has zero within-group spread -> null z-score,
    # not skewed by the unrelated WR group.
    assert result.filter(pl.col("player_id") == "rb1")["positional_zscore"][0] is None
    wr = result.filter(pl.col("position") == "WR").sort("player_id")
    assert wr["positional_zscore"][0] == pytest.approx(-wr["positional_zscore"][1])


# ---------------------------------------------------------------------------
# calibrate_lambda_risk
# ---------------------------------------------------------------------------


def test_calibrate_lambda_risk_picks_a_candidate_from_the_grid():
    historical_df = pl.DataFrame(
        {
            "VOR": [20.0, 15.0, 10.0, 5.0, 0.0, -5.0],
            "stddev": [10.0, 2.0, 8.0, 1.0, 5.0, 1.0],
            "actual_points": [90.0, 110.0, 70.0, 100.0, 60.0, 80.0],
        }
    )
    lam = vor.calibrate_lambda_risk(historical_df, candidate_lambdas=(0.0, 1.0, 2.0))
    assert lam in (0.0, 1.0, 2.0)
