"""Tests for ffdraft.model.blend."""

from __future__ import annotations

import polars as pl
import pytest

from ffdraft.model import blend, calibrate

RULES = {"reception": 0.5, "rush yard": 0.1}


def _long_rows(rows: list[tuple]) -> pl.DataFrame:
    return pl.DataFrame(
        [
            {
                "season": season,
                "week": week,
                "source": source,
                "snapshot_date": "2025-08-01",
                "source_player_id": player_id,
                "player_id": player_id,
                "player_name_raw": player_id,
                "team": "SF",
                "position": position,
                "stat_name": stat_name,
                "stat_value": float(stat_value),
            }
            for season, week, source, player_id, position, stat_name, stat_value in rows
        ]
    )


def _write_equal_weight_weights(
    tmp_path, position: str = "WR", sources: tuple[str, ...] = ("sleeper", "espn")
):
    """A minimal source_weights.parquet whose winner is EqualWeightMean."""
    train_df = pl.DataFrame(
        {
            "season": [2023, 2024],
            "player_id": ["x1", "x1"],
            "position": [position, position],
            **{
                calibrate.projection_column(s): [80.0 + i, 85.0 + i]
                for i, s in enumerate(sources)
            },
            "actual_points": [85.0, 88.0],
        }
    )
    calibrators = {"EqualWeightMean": calibrate.EqualWeightMean()}
    loso = calibrate.run_loso_evaluation(train_df, calibrators)
    weights_df = calibrate.fit_winning_calibrators(train_df, loso, calibrators)
    path = tmp_path / "source_weights.parquet"
    calibrate.write_source_weights(weights_df, path)
    return path


def test_apply_blend_renormalises_over_available_sources(tmp_path):
    path = _write_equal_weight_weights(tmp_path)
    projections = _long_rows(
        [
            (2025, 0, "sleeper", "p1", "WR", "reception", 80),
            (2025, 0, "espn", "p1", "WR", "reception", 90),
            # p2 only has one of the two sources the winner was fitted on.
            (2025, 0, "sleeper", "p2", "WR", "reception", 40),
        ]
    )

    result = blend.apply_blend(projections, path, RULES)

    p1 = result.filter(pl.col("player_id") == "p1")["EP"][0]
    p2 = result.filter(pl.col("player_id") == "p2")["EP"][0]
    assert p1 == pytest.approx((80 * 0.5 + 90 * 0.5) / 2)
    # Renormalised over the single present source, not zero-filled.
    assert p2 == pytest.approx(40 * 0.5)


def test_apply_blend_falls_back_when_the_winning_calibrator_abstains(tmp_path):
    """A FantasyProsOnly winner returns null for a player missing that source;
    apply_blend must fall back to an equal-weight mean for that player, not
    ship a null EP for a player who has other usable sources."""
    train_df = pl.DataFrame(
        {
            "season": [2023, 2024],
            "player_id": ["x1", "x1"],
            "position": ["WR", "WR"],
            "proj_fantasypros": [80.0, 82.0],
            "proj_sleeper": [70.0, 72.0],
            "actual_points": [80.0, 83.0],
        }
    )
    calibrators = {"FantasyProsOnly": calibrate.FantasyProsOnly()}
    loso = calibrate.run_loso_evaluation(train_df, calibrators)
    weights_df = calibrate.fit_winning_calibrators(train_df, loso, calibrators)
    path = tmp_path / "source_weights.parquet"
    calibrate.write_source_weights(weights_df, path)

    projections = _long_rows(
        [
            (2025, 0, "fantasypros", "p1", "WR", "reception", 80),
            (2025, 0, "sleeper", "p1", "WR", "reception", 70),
            # p2 has no fantasypros row at all -- FantasyProsOnly abstains.
            (2025, 0, "sleeper", "p2", "WR", "reception", 40),
        ]
    )

    result = blend.apply_blend(projections, path, RULES)

    p1 = result.filter(pl.col("player_id") == "p1")["EP"][0]
    p2 = result.filter(pl.col("player_id") == "p2")["EP"][0]
    assert p1 == pytest.approx(80 * 0.5)
    assert p2 == pytest.approx(40 * 0.5)  # equal-weight fallback, not null


def test_apply_blend_rejects_schema_version_mismatch(tmp_path):
    path = _write_equal_weight_weights(tmp_path)
    weights_df = calibrate.load_source_weights(path).with_columns(
        pl.lit(999).cast(pl.Int32).alias("schema_version")
    )
    bad_path = tmp_path / "bad_weights.parquet"
    calibrate.write_source_weights(weights_df, bad_path)

    projections = _long_rows([(2025, 0, "sleeper", "p1", "WR", "reception", 80)])
    with pytest.raises(blend.BlendArtifactError, match="schema_version"):
        blend.apply_blend(projections, bad_path, RULES)


def test_apply_blend_raises_a_clear_error_on_a_corrupt_pickle(tmp_path):
    path = _write_equal_weight_weights(tmp_path)
    weights_df = calibrate.load_source_weights(path).with_columns(
        pl.lit(b"not a valid pickle").alias("model_pickle")
    )
    corrupt_path = tmp_path / "corrupt_weights.parquet"
    calibrate.write_source_weights(weights_df, corrupt_path)

    projections = _long_rows([(2025, 0, "sleeper", "p1", "WR", "reception", 80)])
    with pytest.raises(blend.BlendArtifactError, match="unpickle"):
        blend.apply_blend(projections, corrupt_path, RULES)


def test_apply_blend_uses_equal_weight_mean_for_a_position_with_no_winner(tmp_path):
    """A position absent from source_weights.parquet entirely (e.g. too little
    LOSO history) should still get a blended EP via equal-weight mean, not be
    dropped from the output."""
    path = _write_equal_weight_weights(tmp_path, position="WR")
    projections = _long_rows(
        [
            (
                2025,
                0,
                "sleeper",
                "k1",
                "K",
                "reception",
                10,
            ),  # not a real K stat, just plumbing
            (2025, 0, "espn", "k1", "K", "reception", 12),
        ]
    )
    result = blend.apply_blend(projections, path, RULES)
    ep = result.filter(pl.col("player_id") == "k1")["EP"][0]
    assert ep == pytest.approx((10 * 0.5 + 12 * 0.5) / 2)
