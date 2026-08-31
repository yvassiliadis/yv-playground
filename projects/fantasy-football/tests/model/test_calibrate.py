"""Tests for ffdraft.model.calibrate."""

from __future__ import annotations

import numpy as np
import polars as pl
import pytest

from ffdraft.model import calibrate

TRUE_WEIGHTS = {"sleeper": 0.5, "fantasypros": 0.3, "espn": 0.2}
SOURCES = list(TRUE_WEIGHTS)

#: Tolerance for the synthetic weight-recovery acceptance bar. With 3000 rows,
#: noise sd = 8 points against a signal sd of ~55, and Ridge(alpha=1.0) fitted
#: intercept-free, the estimator's standard error on each weight is ~0.003, so
#: 0.02 absolute is roughly 6 sigma -- tight enough that a genuinely wrong
#: blend (say, an unclipped or unnormalised one) cannot slip through, loose
#: enough not to be flaky across BLAS/platform differences.
WEIGHT_TOLERANCE = 0.02


def _synthetic_sources(n: int, rng: np.random.Generator) -> dict[str, np.ndarray]:
    """Three correlated-but-distinct source projections, in fantasy points."""
    latent = rng.normal(160.0, 55.0, size=n)
    return {
        "sleeper": latent + rng.normal(0.0, 12.0, size=n),
        "fantasypros": latent + rng.normal(0.0, 15.0, size=n),
        "espn": latent + rng.normal(0.0, 20.0, size=n),
    }


def _synthetic_training_frame(
    n: int = 3000,
    seed: int = 7,
    season: int = 2024,
    position: str = "RB",
    noise_sd: float = 8.0,
) -> pl.DataFrame:
    """Rows whose target is a KNOWN linear combination of the sources."""
    rng = np.random.default_rng(seed)
    projections = _synthetic_sources(n, rng)
    target = sum(TRUE_WEIGHTS[s] * projections[s] for s in SOURCES)
    target = target + rng.normal(0.0, noise_sd, size=n)

    return pl.DataFrame(
        {
            "season": [season] * n,
            "player_id": [f"P{i:05d}" for i in range(n)],
            "position": [position] * n,
            **{calibrate.projection_column(s): projections[s] for s in SOURCES},
            calibrate.TARGET_COLUMN: target,
        }
    )


def _tiny_frame() -> pl.DataFrame:
    return pl.DataFrame(
        {
            "season": [2024, 2024, 2024],
            "player_id": ["a", "b", "c"],
            "position": ["WR", "WR", "WR"],
            "proj_sleeper": [100.0, 200.0, None],
            "proj_fantasypros": [110.0, None, 300.0],
            "proj_espn": [120.0, 220.0, None],
            calibrate.TARGET_COLUMN: [105.0, 210.0, 290.0],
        }
    )


# ---------------------------------------------------------------------------
# The acceptance bar: recover known weights from synthetic data.
# ---------------------------------------------------------------------------


def test_ridge_blend_recovers_known_weights_on_synthetic_data():
    train_df = _synthetic_training_frame()

    model = calibrate.RidgeBlend()
    model.fit(train_df)

    assert not model.is_fallback
    recovered = model.weights
    assert set(recovered) == set(TRUE_WEIGHTS)

    for source, true_weight in TRUE_WEIGHTS.items():
        assert recovered[source] == pytest.approx(true_weight, abs=WEIGHT_TOLERANCE), (
            f"{source}: recovered {recovered[source]:.4f}, true {true_weight:.4f}"
        )

    assert sum(recovered.values()) == pytest.approx(1.0)


def test_ridge_blend_clips_negative_weights_and_renormalises():
    """A source that is pure noise should be pushed to (or near) zero, and the
    surviving weights must still sum to exactly 1."""
    rng = np.random.default_rng(11)
    n = 2000
    projections = _synthetic_sources(n, rng)
    projections["noise"] = rng.normal(160.0, 55.0, size=n)
    target = sum(TRUE_WEIGHTS[s] * projections[s] for s in SOURCES)

    train_df = pl.DataFrame(
        {
            "season": [2024] * n,
            "player_id": [f"P{i}" for i in range(n)],
            "position": ["RB"] * n,
            **{calibrate.projection_column(s): v for s, v in projections.items()},
            calibrate.TARGET_COLUMN: target,
        }
    )

    model = calibrate.RidgeBlend()
    model.fit(train_df)

    assert all(w >= 0.0 for w in model.weights.values())
    assert sum(model.weights.values()) == pytest.approx(1.0)
    assert model.weights["noise"] < 0.02


# ---------------------------------------------------------------------------
# Baselines on a tiny fixture.
# ---------------------------------------------------------------------------


def test_equal_weight_mean_is_the_mean_of_present_sources():
    model = calibrate.EqualWeightMean()
    model.fit(_tiny_frame())
    predicted = model.predict(_tiny_frame())

    assert predicted[calibrate.PREDICTION_COLUMN].to_list() == pytest.approx(
        [110.0, 210.0, 300.0]
    )


def test_equal_weight_mean_is_deterministic():
    model = calibrate.EqualWeightMean()
    model.fit(_tiny_frame())
    first = model.predict(_tiny_frame())
    second = model.predict(_tiny_frame())
    assert first.equals(second)


def test_fantasypros_only_passes_the_fantasypros_column_through():
    model = calibrate.FantasyProsOnly()
    model.fit(_tiny_frame())
    predicted = model.predict(_tiny_frame())

    assert predicted[calibrate.PREDICTION_COLUMN].to_list() == [110.0, None, 300.0]


def test_prediction_is_null_when_no_source_is_present():
    frame = pl.DataFrame(
        {
            "season": [2024],
            "player_id": ["ghost"],
            "position": ["TE"],
            "proj_sleeper": [None],
            "proj_espn": [None],
            calibrate.TARGET_COLUMN: [50.0],
        },
        schema_overrides={"proj_sleeper": pl.Float64, "proj_espn": pl.Float64},
    )
    model = calibrate.EqualWeightMean()
    model.fit(frame)
    assert model.predict(frame)[calibrate.PREDICTION_COLUMN].to_list() == [None]


# ---------------------------------------------------------------------------
# Missing-source renormalisation.
# ---------------------------------------------------------------------------


def test_equal_weight_mean_renormalises_over_present_sources():
    """Player b has 2 of 3 sources: the answer must be their mean (210), not
    the 3-source mean that treats the missing source as zero (140)."""
    model = calibrate.EqualWeightMean()
    model.fit(_tiny_frame())
    predicted = model.predict(_tiny_frame())
    assert predicted[calibrate.PREDICTION_COLUMN][1] == pytest.approx(210.0)


def test_ridge_blend_renormalises_over_present_sources():
    train_df = _synthetic_training_frame(n=600)
    model = calibrate.RidgeBlend()
    model.fit(train_df)
    weights = model.weights

    partial = pl.DataFrame(
        {
            "season": [2024],
            "player_id": ["partial"],
            "position": ["RB"],
            "proj_sleeper": [200.0],
            "proj_fantasypros": [None],
            "proj_espn": [180.0],
        },
        schema_overrides={"proj_fantasypros": pl.Float64},
    )
    predicted = model.predict(partial)[calibrate.PREDICTION_COLUMN][0]

    surviving = weights["sleeper"] + weights["espn"]
    expected = (weights["sleeper"] * 200.0 + weights["espn"] * 180.0) / surviving
    assert predicted == pytest.approx(expected)

    # The renormalised prediction lies between the two present projections;
    # zero-filling the missing source would drag it far below 180.
    assert 180.0 <= predicted <= 200.0


# ---------------------------------------------------------------------------
# Sparse-data fallback.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "factory",
    [
        calibrate.RidgeBlend,
        calibrate.LightGBMBlend,
        calibrate.SmallMLP,
        calibrate.QuantileBlend,
    ],
)
def test_sparse_training_data_falls_back_to_equal_weight_mean(factory):
    model = factory()
    model.fit(_tiny_frame())

    assert model.is_fallback
    predicted = model.predict(_tiny_frame())
    assert predicted[calibrate.PREDICTION_COLUMN].to_list() == pytest.approx(
        [110.0, 210.0, 300.0]
    )


def test_games_played_is_not_a_model_feature():
    """`games_played` counts games in the season being predicted, so it is
    near-deterministic of that season's points and is null for an upcoming
    season. It stays a diagnostic column, never a model input."""
    assert "games_played" not in calibrate.EXTRA_FEATURE_COLUMNS

    frame = _synthetic_training_frame(n=200).with_columns(
        pl.lit(17.0).alias("games_played"),
        pl.lit(150.0).alias("prior_actual_points"),
    )
    model = calibrate.LightGBMBlend(n_estimators=10)
    model.fit(frame)
    assert "games_played" not in model.features_
    assert "prior_actual_points" in model.features_


@pytest.mark.parametrize(
    "factory",
    [calibrate.LightGBMBlend, calibrate.SmallMLP, calibrate.QuantileBlend],
)
def test_predict_before_fit_raises_a_named_error(factory):
    model = factory()
    with pytest.raises(RuntimeError, match=r"\.predict called before fit"):
        model.predict(_tiny_frame())


def test_fallback_without_any_source_columns_names_the_real_calibrator():
    frame = pl.DataFrame(
        {
            "season": [2024, 2024],
            "player_id": ["a", "b"],
            "position": ["WR", "WR"],
            calibrate.TARGET_COLUMN: [100.0, 120.0],
        }
    )
    with pytest.raises(ValueError, match="RidgeBlend.fit: no proj_. columns"):
        calibrate.RidgeBlend().fit(frame)


def test_quantile_blend_returns_three_ordered_columns():
    frame = _synthetic_training_frame(n=200)
    model = calibrate.QuantileBlend(n_estimators=30)
    model.fit(frame)
    predicted = model.predict(frame)

    for column in ("prediction_p25", calibrate.PREDICTION_COLUMN, "prediction_p75"):
        assert column in predicted.columns

    low = predicted["prediction_p25"].to_numpy()
    mid = predicted[calibrate.PREDICTION_COLUMN].to_numpy()
    high = predicted["prediction_p75"].to_numpy()
    assert np.all(low <= mid) and np.all(mid <= high)


def test_degraded_quantile_blend_emits_null_quantiles_not_a_zero_width_band():
    """A QuantileBlend that fell back to EqualWeightMean has no floor/ceiling
    estimate. Aliasing the point prediction into both would make
    `p75 - p25 == 0`, which `metrics.vor.risk_adjusted_vor` reads as
    "confidently risk-free" -- exactly the ranking bug it exists to prevent."""
    sparse = _synthetic_training_frame(n=5)
    model = calibrate.QuantileBlend(n_estimators=10)
    model.fit(sparse)

    assert model.is_fallback
    predicted = model.predict(sparse)

    assert predicted["prediction"].null_count() == 0
    assert predicted["prediction_p25"].null_count() == predicted.height
    assert predicted["prediction_p75"].null_count() == predicted.height
    assert predicted.schema["prediction_p25"] == pl.Float64


# ---------------------------------------------------------------------------
# Recency weighting.
# ---------------------------------------------------------------------------


def test_recency_weights_decay_from_the_newest_season():
    frame = pl.DataFrame({"season": [2024, 2023, 2022]})
    weights = calibrate.recency_weights(frame, lambda_=0.35)
    assert weights == pytest.approx([1.0, np.exp(-0.35), np.exp(-0.70)])


def test_loso_anchors_recency_on_the_whole_dataset_not_the_fold():
    """Every fold must weight a given row identically, otherwise fold MAEs are
    not comparable. The harness therefore pins `reference_season` to the newest
    season across all data rather than letting each fold re-derive it."""
    seen: list[int | None] = []

    class Recorder(calibrate.EqualWeightMean):
        name = "Recorder"

        def fit(self, train_df):
            seen.append(self.reference_season)
            super().fit(train_df)

    train_df = _multi_season_frame()
    calibrate.run_loso_evaluation(train_df, {"Recorder": Recorder()})

    assert seen  # folds actually ran
    assert set(seen) == {2024}


def test_recency_weights_never_exceed_one():
    frame = pl.DataFrame({"season": [2024, 2025]})
    weights = calibrate.recency_weights(frame, lambda_=0.35, reference_season=2024)
    assert weights.max() <= 1.0


# ---------------------------------------------------------------------------
# LOSO harness.
# ---------------------------------------------------------------------------


def _light_calibrators() -> dict[str, calibrate.Calibrator]:
    """All six calibrators, with the boosted/neural ones shrunk.

    Identical code paths to `default_calibrators()`, just far fewer trees and
    epochs -- these tests check plumbing and table shape, not accuracy, and
    the production-sized defaults make the suite minutes-long for no signal.
    """
    return {
        "EqualWeightMean": calibrate.EqualWeightMean(),
        "FantasyProsOnly": calibrate.FantasyProsOnly(),
        "RidgeBlend": calibrate.RidgeBlend(),
        "LightGBMBlend": calibrate.LightGBMBlend(n_estimators=20),
        "SmallMLP": calibrate.SmallMLP(max_iter=40),
        "QuantileBlend": calibrate.QuantileBlend(n_estimators=20),
    }


def test_default_calibrators_are_the_six_spec_calibrators():
    assert set(calibrate.default_calibrators()) == {
        "EqualWeightMean",
        "FantasyProsOnly",
        "RidgeBlend",
        "LightGBMBlend",
        "SmallMLP",
        "QuantileBlend",
    }
    for name, model in calibrate.default_calibrators().items():
        assert isinstance(model, calibrate.Calibrator)
        assert model.name == name


def _multi_season_frame(seasons=(2022, 2023, 2024), positions=("RB", "WR"), n=60):
    """Multi-season fixture for the LOSO/artifact tests.

    `prior_actual_points` is generated independently of the row's own target
    -- a noisy echo of a *different* draw, standing in for the previous
    season. Setting it to the current target (an easy mistake) would hand the
    three learned calibrators a perfect-information feature that the
    production path can never produce, and would silently rig every LOSO
    comparison run through this fixture.
    """
    frames = []
    for season_index, season in enumerate(seasons):
        for position_index, position in enumerate(positions):
            seed = 100 + 10 * season_index + position_index
            rng = np.random.default_rng(seed + 5000)
            frame = _synthetic_training_frame(
                n=n,
                seed=seed,
                season=season,
                position=position,
            ).with_columns(
                (pl.col("player_id") + f"-{season}-{position}").alias("player_id"),
                pl.Series("prior_games_played", rng.integers(8, 18, size=n)).cast(
                    pl.Float64
                ),
                pl.Series("prior_actual_points", rng.normal(160.0, 55.0, size=n)).cast(
                    pl.Float64
                ),
            )
            frames.append(frame)
    return pl.concat(frames, how="vertical")


def test_loso_produces_one_row_per_position_and_calibrator():
    train_df = _multi_season_frame()
    calibrators = _light_calibrators()

    loso = calibrate.run_loso_evaluation(train_df, calibrators)

    assert loso.height == 2 * len(calibrators)
    assert set(loso.columns) == set(calibrate.LOSO_COLUMNS)
    assert loso.select(["position", "calibrator"]).unique().height == 2 * len(
        calibrators
    )
    assert loso["n_folds"].to_list() == [3] * loso.height
    assert loso["mae"].null_count() == 0

    # Exactly one winner per position.
    winners = loso.filter(pl.col("is_winner"))
    assert sorted(winners["position"].to_list()) == ["RB", "WR"]

    # The winner really is the minimum-MAE calibrator for its position.
    for row in winners.iter_rows(named=True):
        best = loso.filter(pl.col("position") == row["position"])["mae"].min()
        assert row["mae"] == pytest.approx(best)


def test_loso_scores_every_calibrator_on_the_same_rows():
    """FantasyProsOnly abstains where its column is null. Every calibrator must
    still be scored on the same (intersected) rows, or the abstainer gets an
    easier subset and an unearned MAE advantage."""
    train_df = _multi_season_frame().with_columns(
        pl.when(pl.int_range(pl.len()).over("season", "position") < 10)
        .then(None)
        .otherwise(pl.col("proj_fantasypros"))
        .alias("proj_fantasypros")
    )
    calibrators = {
        "EqualWeightMean": calibrate.EqualWeightMean(),
        "FantasyProsOnly": calibrate.FantasyProsOnly(),
        "RidgeBlend": calibrate.RidgeBlend(),
    }

    loso = calibrate.run_loso_evaluation(train_df, calibrators)

    for position in ("RB", "WR"):
        rows = loso.filter(pl.col("position") == position)
        # Common support: identical evaluated row count for every calibrator.
        assert rows["n_rows"].n_unique() == 1
        # ...and it really is the intersection, not the full held-out set.
        assert rows["n_rows"][0] == 3 * (60 - 10)

    coverage = dict(
        loso.filter(pl.col("position") == "RB")
        .select("calibrator", "coverage")
        .iter_rows()
    )
    assert coverage["FantasyProsOnly"] == pytest.approx(50 / 60)
    assert coverage["EqualWeightMean"] == pytest.approx(1.0)
    assert coverage["RidgeBlend"] == pytest.approx(1.0)


def test_loso_skips_positions_with_a_single_season():
    train_df = _synthetic_training_frame(n=200, position="K")
    loso = calibrate.run_loso_evaluation(
        train_df, {"EqualWeightMean": calibrate.EqualWeightMean()}
    )
    assert loso.height == 0


def test_loso_does_not_mutate_the_caller_s_calibrators():
    train_df = _multi_season_frame()
    ridge = calibrate.RidgeBlend()
    calibrate.run_loso_evaluation(train_df, {"RidgeBlend": ridge})
    assert ridge.weights_ == {}


def test_print_loso_table_renders(capsys):
    loso = calibrate.run_loso_evaluation(
        _multi_season_frame(),
        {
            "EqualWeightMean": calibrate.EqualWeightMean(),
            "RidgeBlend": calibrate.RidgeBlend(),
        },
    )
    from rich.console import Console

    calibrate.print_loso_table(loso, console=Console(width=200))
    out = capsys.readouterr().out
    assert "EqualWeightMean" in out
    assert "RidgeBlend" in out
    assert "Coverage" in out


# ---------------------------------------------------------------------------
# source_weights.parquet artefact.
# ---------------------------------------------------------------------------


def test_source_weights_roundtrip(tmp_path):
    train_df = _multi_season_frame()
    loso = calibrate.run_loso_evaluation(train_df, _light_calibrators())
    weights_df = calibrate.fit_winning_calibrators(train_df, loso, _light_calibrators())

    assert weights_df.columns == calibrate.SOURCE_WEIGHTS_COLUMNS
    assert sorted(weights_df["position"].to_list()) == ["RB", "WR"]
    assert (
        weights_df["schema_version"].to_list()
        == [calibrate.SOURCE_WEIGHTS_SCHEMA_VERSION] * 2
    )

    path = calibrate.write_source_weights(
        weights_df, tmp_path / "source_weights.parquet"
    )
    reloaded = calibrate.load_source_weights(path)
    assert reloaded.equals(weights_df)

    models = calibrate.load_fitted_calibrators(path)
    assert set(models) == {"RB", "WR"}
    for position, model in models.items():
        subset = train_df.filter(pl.col("position") == position)
        predicted = model.predict(subset)
        assert predicted.height == subset.height
        assert predicted[calibrate.PREDICTION_COLUMN].null_count() == 0


def test_source_weights_records_normalised_weights_for_weight_based_winners():
    train_df = _multi_season_frame()
    loso = calibrate.run_loso_evaluation(
        train_df,
        {
            "EqualWeightMean": calibrate.EqualWeightMean(),
            "RidgeBlend": calibrate.RidgeBlend(),
        },
    )
    weights_df = calibrate.fit_winning_calibrators(
        train_df,
        loso,
        {
            "EqualWeightMean": calibrate.EqualWeightMean(),
            "RidgeBlend": calibrate.RidgeBlend(),
        },
    )

    for row in weights_df.iter_rows(named=True):
        assert sorted(row["sources"]) == sorted(SOURCES)
        assert sum(row["weights"]) == pytest.approx(1.0)
        assert row["intercept"] == 0.0


# ---------------------------------------------------------------------------
# Training-frame construction from canonical long-schema input.
# ---------------------------------------------------------------------------

RULES = {"passing yard": 0.04, "passing td": 6.0, "reception": 0.5}


def _long_rows(rows):
    return pl.DataFrame(
        [
            {
                "season": season,
                "week": week,
                "source": source,
                "snapshot_date": "2024-08-01",
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


def test_build_training_frame_scores_both_sides_with_the_shared_function():
    projections = _long_rows(
        [
            (2024, 0, "sleeper", "p1", "QB", "passing yard", 4000),
            (2024, 0, "sleeper", "p1", "QB", "passing td", 30),
            (2024, 0, "espn", "p1", "QB", "passing yard", 3800),
        ]
    )
    actuals = _long_rows(
        [
            (2024, 1, "nflverse", "p1", "QB", "passing yard", 300),
            (2024, 2, "nflverse", "p1", "QB", "passing td", 2),
        ]
    )

    frame = calibrate.build_training_frame(projections, actuals, RULES)

    assert frame.height == 1
    row = frame.row(0, named=True)
    assert row["position"] == "QB"
    assert row["proj_sleeper"] == pytest.approx(4000 * 0.04 + 30 * 6.0)
    assert row["proj_espn"] == pytest.approx(3800 * 0.04)
    assert row[calibrate.TARGET_COLUMN] == pytest.approx(300 * 0.04 + 2 * 6.0)
    assert row["games_played"] == pytest.approx(2.0)


def test_build_training_frame_rejects_multi_source_actuals():
    """Two sources for the same player-season would silently double the target
    (weeks are summed), so it must fail loudly instead."""
    projections = _long_rows(
        [(2024, 0, "sleeper", "p1", "WR", "reception", 80)],
    )
    actuals = _long_rows(
        [
            (2024, 1, "nflverse", "p1", "WR", "reception", 70),
            (2024, 1, "some_other_feed", "p1", "WR", "reception", 70),
        ]
    )

    with pytest.raises(ValueError, match="must carry exactly one source"):
        calibrate.build_training_frame(projections, actuals, RULES)

    frame = calibrate.build_training_frame(
        projections, actuals, RULES, actual_source="nflverse"
    )
    assert frame[calibrate.TARGET_COLUMN].to_list() == [pytest.approx(35.0)]


def test_build_training_frame_rejects_an_unknown_actual_source():
    projections = _long_rows([(2024, 0, "sleeper", "p1", "WR", "reception", 80)])
    actuals = _long_rows([(2024, 1, "nflverse", "p1", "WR", "reception", 70)])

    with pytest.raises(ValueError, match="no actuals rows for source"):
        calibrate.build_training_frame(
            projections, actuals, RULES, actual_source="nope"
        )


def test_build_training_frame_derives_prior_season_features():
    projections = _long_rows(
        [
            (2023, 0, "sleeper", "p1", "WR", "reception", 80),
            (2024, 0, "sleeper", "p1", "WR", "reception", 90),
        ]
    )
    actuals = _long_rows(
        [
            (2023, 1, "nflverse", "p1", "WR", "reception", 70),
            (2024, 1, "nflverse", "p1", "WR", "reception", 85),
        ]
    )

    frame = calibrate.build_training_frame(projections, actuals, RULES).sort("season")

    assert frame["prior_actual_points"].to_list() == [None, pytest.approx(35.0)]
    assert frame["prior_games_played"].to_list() == [None, pytest.approx(1.0)]


def test_seasonal_totals_does_not_double_count_repeated_snapshots():
    """Regression: two `ingest snapshot` runs of one season/source must not
    double the seasonal total `build_training_frame` fits against."""

    def _rows(snapshot_date: str) -> pl.DataFrame:
        return pl.DataFrame(
            [
                {
                    "season": 2024,
                    "week": 0,
                    "source": "sleeper",
                    "snapshot_date": snapshot_date,
                    "player_id": "wr1",
                    "position": "WR",
                    "stat_name": "reception",
                    "stat_value": 80.0,
                }
            ]
        )

    once = calibrate._seasonal_totals(_rows("2024-08-01"), {"reception": 0.5})
    twice = calibrate._seasonal_totals(
        pl.concat([_rows("2024-08-01"), _rows("2024-08-15")]), {"reception": 0.5}
    )

    assert once["fantasy_points"][0] == pytest.approx(40.0)
    assert twice.height == 1
    assert twice["fantasy_points"][0] == pytest.approx(40.0)
