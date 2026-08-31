"""Tests for ffdraft.metrics.vor."""

from __future__ import annotations

import logging
from types import MappingProxyType

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


def test_risk_adjusted_vor_treats_a_coverage_gap_as_unknown_not_risk_free():
    """A player whose EP came from blend.py's coverage<1.0 fallback (see
    test_blend.py's test_apply_blend_leaves_ep_quantiles_null_when_only_ep_has_a_fallback)
    has null EP_p25/EP_p75 alongside a real EP/VOR. Silently zero-filling
    that null sigma would rank this player ABOVE an otherwise-identical
    player with real, nonzero variance data -- exactly the failure mode this
    function's docstring says it refuses to allow. It must come out null
    instead, a visible "unknown" rather than a fabricated "risk-free"."""
    df = pl.DataFrame(
        {
            "player_id": ["fallback_covered", "real_quantiles"],
            "position": ["WR", "WR"],
            "EP": [80.0, 80.0],
            "VOR": [10.0, 10.0],
            "EP_p25": [None, 70.0],
            "EP_p75": [None, 90.0],
        }
    )
    result = vor.risk_adjusted_vor(df, lambda_risk=1.0)

    fallback_row = result.filter(pl.col("player_id") == "fallback_covered").row(
        0, named=True
    )
    real_row = result.filter(pl.col("player_id") == "real_quantiles").row(0, named=True)
    # Unknown, not a fabricated risk-free 0.0 -- a null can't be sorted above
    # (or below) the real-quantile player's finite risk_adjusted_vor, which
    # is exactly the point: a caller ranking by this column can no longer
    # mistake "no data" for "no risk".
    assert fallback_row["risk_adjusted_vor"] is None
    assert real_row["risk_adjusted_vor"] is not None


def test_risk_adjusted_vor_falls_back_to_stddev_per_row_when_quantiles_are_null():
    """When a row's quantile columns are null but a stddev column is also
    present (e.g. after joining metrics.consistency's output), that row
    should use stddev rather than being left null -- the per-row fallback
    priority, not an all-or-nothing per-frame choice."""
    df = pl.DataFrame(
        {
            "player_id": ["a", "b"],
            "position": ["WR", "WR"],
            "EP": [80.0, 100.0],
            "VOR": [10.0, 20.0],
            "EP_p25": [None, 80.0],
            "EP_p75": [None, 120.0],
            "stddev": [4.0, 999.0],  # b has real quantiles, so its stddev is ignored
        }
    )
    result = vor.risk_adjusted_vor(df, lambda_risk=1.0)

    a = result.filter(pl.col("player_id") == "a").row(0, named=True)
    b = result.filter(pl.col("player_id") == "b").row(0, named=True)
    assert a["risk_adjusted_vor"] == pytest.approx(10.0 - 4.0)
    sigma_b = (120.0 - 80.0) / 1.34898
    assert b["risk_adjusted_vor"] == pytest.approx(20.0 - sigma_b)


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


def test_calibrate_lambda_risk_picks_the_uniquely_best_candidate():
    """VOR alone (lambda=0) ranks these players in the exact order they
    actually finished; the two players with the highest VOR also have
    outsized stddev, so penalising by any lambda > 0 scrambles that perfect
    ranking. lambda=0.0 must therefore win uniquely -- a stub that always
    returns e.g. `candidate_lambdas[0]` would only pass by coincidence here
    since 0.0 also happens to be first, so this also checks lambda=2.0 (a
    non-first, non-default candidate) loses too."""
    historical_df = pl.DataFrame(
        {
            "VOR": [50.0, 40.0, 30.0, 20.0, 10.0, 0.0],
            "stddev": [100.0, 80.0, 60.0, 5.0, 3.0, 1.0],
            "actual_points": [500.0, 400.0, 300.0, 200.0, 100.0, 0.0],
        }
    )
    lam = vor.calibrate_lambda_risk(historical_df, candidate_lambdas=(2.0, 1.0, 0.0))
    assert lam == pytest.approx(0.0)


# ---------------------------------------------------------------------------
# Degraded quantile output must read as "unknown sigma", not "zero sigma".
# ---------------------------------------------------------------------------


def test_risk_adjusted_vor_treats_degraded_null_quantiles_as_unknown_sigma():
    """A player whose winning calibrator degraded to EqualWeightMean gets null
    EP_p25/EP_p75 (see calibrate.QuantileBlend.predict). That must fall through
    to `stddev`, and must NOT be read as a zero-width, risk-free band."""
    df = pl.DataFrame(
        {
            "player_id": ["real", "degraded", "nothing"],
            "position": ["WR", "WR", "WR"],
            "EP": [200.0, 200.0, 200.0],
            "VOR": [50.0, 50.0, 50.0],
            # `degraded`/`nothing` are what the fallback branch now emits.
            "EP_p25": [180.0, None, None],
            "EP_p75": [220.0, None, None],
            "stddev": [30.0, 30.0, None],
        }
    )

    result = vor.risk_adjusted_vor(df, lambda_risk=1.0)
    by_player = dict(zip(result["player_id"], result["risk_adjusted_vor"]))

    # Real quantiles: sigma = (220 - 180) / 1.34898.
    assert by_player["real"] == pytest.approx(50.0 - 40.0 / vor._IQR_TO_SIGMA)
    # Degraded: falls through to stddev, NOT sigma = 0 (which would give 50.0).
    assert by_player["degraded"] == pytest.approx(20.0)
    assert by_player["degraded"] != pytest.approx(50.0)
    # No variance information at all stays null rather than becoming risk-free.
    assert by_player["nothing"] is None


# ---------------------------------------------------------------------------
# Positions absent from the ADP pool (I2).
# ---------------------------------------------------------------------------


def test_compute_vor_warns_and_falls_back_when_a_position_is_absent_from_adp(caplog):
    """A hand-maintained ADP file missing TEs is a data gap, not a result: warn
    and use the roster-math replacement rank instead of nulling every TE."""
    blended = pl.DataFrame(
        {
            "player_id": ["te1", "te2", "te3", "wr1"],
            "position": ["TE", "TE", "TE", "WR"],
            "EP": [150.0, 120.0, 100.0, 200.0],
        }
    )
    adp = pl.DataFrame({"player_id": ["wr1"], "position": ["WR"], "adp": [1]})
    roster = RosterConfig(starters=MappingProxyType({"TE": 1, "WR": 1}))

    with caplog.at_level(logging.WARNING, logger=vor.__name__):
        result = vor.compute_vor(blended, adp, teams=2, roster_config=roster)

    assert "TE" in caplog.text and "ADP" in caplog.text

    # teams=2 * 1 TE starter -> replacement is the 2nd-ranked TE by EP (120.0).
    tes = result.filter(pl.col("position") == "TE")
    assert tes["VOR"].null_count() == 0
    assert dict(zip(tes["player_id"], tes["VOR"])) == pytest.approx(
        {"te1": 30.0, "te2": 0.0, "te3": -20.0}
    )


def test_compute_vor_leaves_null_vor_and_warns_for_a_position_with_no_starters(caplog):
    blended = pl.DataFrame({"player_id": ["p1"], "position": ["P"], "EP": [10.0]})
    adp = pl.DataFrame({"player_id": ["p9"], "position": ["WR"], "adp": [1]})

    with caplog.at_level(logging.WARNING, logger=vor.__name__):
        result = vor.compute_vor(
            blended,
            adp,
            teams=10,
            roster_config=RosterConfig(starters=MappingProxyType({})),
        )

    assert "VOR stays null" in caplog.text
    assert result["VOR"][0] is None
