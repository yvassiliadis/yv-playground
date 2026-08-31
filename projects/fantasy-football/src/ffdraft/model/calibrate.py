"""Calibrate multi-source projections into a single fantasy-points estimate.

Six calibrators sit behind one `Calibrator` protocol (`fit`/`predict`), a
leave-one-season-out (LOSO) harness scores them per position, and the winner
per position is persisted to `data/derived/source_weights.parquet` so
`model/blend.py` can apply it later without re-fitting.

Data shapes
-----------
Everything upstream of this module is the canonical *long* schema
(`season, week, source, snapshot_date, source_player_id, player_id,
player_name_raw, team, position, stat_name, stat_value`). Calibration needs
one column per source, so `build_training_frame()` is the boundary that
pivots long -> wide exactly once:

    season           i64    the projected season
    player_id        str    crosswalked canonical player id
    position         str    QB/RB/WR/TE/K/DST
    proj_<source>    f64    that source's *scored* seasonal projection
    actual_points    f64    that player-season's *scored* actual points
    games_played     f64    diagnostic ONLY -- games played in the season
                            being predicted, so it is not a model feature
                            (see EXTRA_FEATURE_COLUMNS)
    prior_actual_points   f64  feature: season-1 actual points
    prior_games_played    f64  feature: season-1 games played
    age              f64    feature, only if the caller supplies it
                            (the pipeline has no birthdate source yet)

Both the projection side and the actual side are scored with the *same*
`ffdraft.scoring.score()` call. There is deliberately no second scoring path
for actuals -- a blend fitted against a differently-scored target would be
silently mis-calibrated.

Recency weighting
-----------------
Every training row is weighted `exp(-lambda * years_ago)` where `years_ago`
is measured from the newest season in the supplied frame (not from the LOSO
held-out season), so a given row carries the same weight in every fold and
the folds stay comparable. `lambda` defaults to `RECENCY_LAMBDA = 0.35`
(~half weight after 2 seasons, ~12% after 6) and is threaded into every
estimator that accepts `sample_weight`.

Missing sources
---------------
At predict time a player may be missing sources that existed at fit time.
The weight-based calibrators (`EqualWeightMean`, `FantasyProsOnly`,
`RidgeBlend`) renormalise their weights over whichever sources are actually
non-null *for that row*: `sum(w_s * x_s) / sum(w_s)` over present `s`. A
missing source is therefore neutral, not a zero-point contribution. If a row
has no sources at all the prediction is null.

Sparse positions/eras
---------------------
Any calibrator that needs a real fit falls back to `EqualWeightMean` when it
has fewer than `MIN_FIT_ROWS = 50` usable training rows. 50 is a deliberately
round, conservative floor: with 3-6 source columns plus up to 3 extra
features it keeps roughly >=5 rows per parameter for the linear models, and
below that a fitted blend is noise dressed up as a model. Kickers and DSTs in
early archive seasons are the realistic trigger.

Out of scope: CNN and RL calibrators (explicitly excluded by the spec).
"""

from __future__ import annotations

import copy
import datetime as dt
import logging
import pickle
from pathlib import Path
from typing import Protocol, runtime_checkable

import numpy as np
import polars as pl
from scipy.stats import spearmanr

from ffdraft import scoring

logger = logging.getLogger(__name__)

PROJ_PREFIX = "proj_"
TARGET_COLUMN = "actual_points"
PREDICTION_COLUMN = "prediction"
KEY_COLUMNS = ["season", "player_id", "position"]

#: Extra (non-source) feature columns used by the learned calibrators. Any of
#: these that are absent from the training frame are simply not used.
#:
#: `games_played` is deliberately NOT here. The training frame carries it as a
#: diagnostic, but it counts games in the season *being predicted*, which is
#: near-deterministic of that season's fantasy points -- feeding it to the
#: learned calibrators (and not to the weight-based ones) would hand them a
#: leak and tilt LOSO winner selection for reasons unrelated to skill. It is
#: also null for an upcoming season, so a model fitted on it would be applied
#: by `blend.py` under a feature distribution it never saw.
EXTRA_FEATURE_COLUMNS = (
    "age",
    "prior_games_played",
    "prior_actual_points",
)

RECENCY_LAMBDA = 0.35
MIN_FIT_ROWS = 50
FANTASYPROS_SOURCE = "fantasypros"
QUANTILE_LEVELS = (0.25, 0.50, 0.75)
SOURCE_WEIGHTS_SCHEMA_VERSION = 1
DEFAULT_SOURCE_WEIGHTS_PATH = Path("data/derived/source_weights.parquet")


# --------------------------------------------------------------------------
# Training-frame construction
# --------------------------------------------------------------------------


def projection_columns(df: pl.DataFrame) -> list[str]:
    """Return the `proj_<source>` columns of `df`, in stable sorted order."""
    return sorted(c for c in df.columns if c.startswith(PROJ_PREFIX))


def source_name(column: str) -> str:
    """`proj_sleeper` -> `sleeper`."""
    return column[len(PROJ_PREFIX) :]


def projection_column(source: str) -> str:
    """`sleeper` -> `proj_sleeper`."""
    return f"{PROJ_PREFIX}{source}"


def _seasonal_totals(long_df: pl.DataFrame, rules: dict[str, float]) -> pl.DataFrame:
    """Score a long frame and total it per `(season, source, player_id)`.

    `scoring.score()` returns per-week rows (projection snapshots use week 0);
    summing over weeks gives the seasonal number both sides of the training
    row are expressed in.
    """
    return (
        scoring.score(long_df, rules)
        .group_by(["season", "source", "player_id"])
        .agg(pl.col("fantasy_points").sum())
    )


def _positions(*long_dfs: pl.DataFrame) -> pl.DataFrame:
    """First non-null `position` per `(season, player_id)` across long frames."""
    frames = [
        df.select(["season", "player_id", "position"]).drop_nulls()
        for df in long_dfs
        if df.height
    ]
    if not frames:
        return pl.DataFrame(
            schema={"season": pl.Int64, "player_id": pl.String, "position": pl.String}
        )
    return (
        pl.concat(frames, how="vertical")
        .group_by(["season", "player_id"])
        .agg(pl.col("position").first())
    )


def build_training_frame(
    projections: pl.DataFrame,
    actuals: pl.DataFrame,
    rules: dict[str, float],
    actual_source: str | None = None,
) -> pl.DataFrame:
    """Join scored projections to scored actuals into the wide training frame.

    `projections` and `actuals` are both canonical long-schema frames. Both
    are scored through `ffdraft.scoring.score()` -- there is no second
    scoring path here by design.

    `actuals` must carry exactly one `source` (nflverse). The target is
    summed over weeks, so a frame containing two sources for the same
    player-season would silently double the target; that is loud rather than
    silent here. Pass `actual_source` to select one source out of a
    multi-source frame instead.

    The join is an inner join on `(season, player_id)`: a projection with no
    actual has no target, and an actual with no projection has no features.
    """
    if actual_source is not None:
        actuals = actuals.filter(pl.col("source") == actual_source)
        if actuals.height == 0:
            raise ValueError(
                f"build_training_frame: no actuals rows for source {actual_source!r}"
            )

    actual_sources = actuals["source"].unique().sort().to_list()
    if len(actual_sources) > 1:
        raise ValueError(
            "build_training_frame: `actuals` must carry exactly one source "
            f"(summing over weeks would double-count the target), got "
            f"{actual_sources}. Pass actual_source=... to pick one."
        )

    proj_totals = _seasonal_totals(projections, rules)
    actual_totals = _seasonal_totals(actuals, rules)

    wide_proj = (
        proj_totals.with_columns(
            (pl.lit(PROJ_PREFIX) + pl.col("source")).alias("proj_column")
        )
        .pivot(on="proj_column", index=["season", "player_id"], values="fantasy_points")
        .sort(["season", "player_id"])
    )

    target = (
        actual_totals.group_by(["season", "player_id"])
        .agg(pl.col("fantasy_points").sum().alias(TARGET_COLUMN))
        .sort(["season", "player_id"])
    )

    games = (
        actuals.filter(pl.col("stat_value").is_not_null())
        .group_by(["season", "player_id"])
        .agg(pl.col("week").n_unique().cast(pl.Float64).alias("games_played"))
    )

    prior = target.join(games, on=["season", "player_id"], how="left").select(
        (pl.col("season") + 1).alias("season"),
        "player_id",
        pl.col(TARGET_COLUMN).alias("prior_actual_points"),
        pl.col("games_played").alias("prior_games_played"),
    )

    frame = (
        wide_proj.join(target, on=["season", "player_id"], how="inner")
        .join(games, on=["season", "player_id"], how="left")
        .join(prior, on=["season", "player_id"], how="left")
        .join(_positions(projections, actuals), on=["season", "player_id"], how="left")
    )

    proj_cols = projection_columns(frame)
    return frame.select(
        pl.col("season").cast(pl.Int64),
        pl.col("player_id").cast(pl.String),
        pl.col("position").cast(pl.String),
        *[pl.col(c).cast(pl.Float64) for c in proj_cols],
        pl.col(TARGET_COLUMN).cast(pl.Float64),
        pl.col("games_played").cast(pl.Float64),
        pl.col("prior_actual_points").cast(pl.Float64),
        pl.col("prior_games_played").cast(pl.Float64),
    ).sort(["season", "player_id"])


def recency_weights(
    df: pl.DataFrame,
    lambda_: float = RECENCY_LAMBDA,
    reference_season: int | None = None,
) -> np.ndarray:
    """`exp(-lambda * years_ago)` per row, measured from `reference_season`.

    `reference_season` defaults to the newest season in `df`. Seasons newer
    than the reference are clamped to `years_ago = 0` so no row is ever
    up-weighted above 1.
    """
    if df.height == 0:
        return np.empty(0, dtype=float)
    reference = reference_season if reference_season is not None else df["season"].max()
    years_ago = np.maximum(0.0, float(reference) - df["season"].to_numpy())
    return np.exp(-lambda_ * years_ago)


# --------------------------------------------------------------------------
# Protocol + shared helpers
# --------------------------------------------------------------------------


@runtime_checkable
class Calibrator(Protocol):
    """One blending strategy: fit on a wide training frame, then predict.

    `predict()` returns one row per input row with the key columns plus a
    `prediction` column (and `prediction_p25`/`prediction_p75` for
    `QuantileBlend`). Row order matches the input frame.
    """

    name: str

    def fit(self, train_df: pl.DataFrame) -> None: ...

    def predict(self, df: pl.DataFrame) -> pl.DataFrame: ...


def _keys(df: pl.DataFrame) -> pl.DataFrame:
    present = [c for c in KEY_COLUMNS if c in df.columns]
    return df.select(present)


def _renormalised_blend(df: pl.DataFrame, weights: dict[str, float]) -> pl.Series:
    """Blend `proj_*` columns by `weights`, renormalised over present sources.

    `weights` is keyed by source name. Sources absent from `df` entirely, or
    null for a given row, drop out of both numerator and denominator, so the
    surviving weights always sum to 1 for that row. Rows with no usable
    source get a null prediction.
    """
    numerator = pl.lit(0.0)
    denominator = pl.lit(0.0)
    for source, weight in weights.items():
        column = projection_column(source)
        if column not in df.columns or weight == 0.0:
            continue
        present = pl.col(column).is_not_null()
        numerator = numerator + pl.when(present).then(
            pl.col(column) * weight
        ).otherwise(0.0)
        denominator = denominator + pl.when(present).then(weight).otherwise(0.0)

    blended = (
        pl.when(denominator > 0)
        .then(numerator / denominator)
        .otherwise(None)
        .alias(PREDICTION_COLUMN)
    )
    if df.height == 0:
        return pl.Series(PREDICTION_COLUMN, [], dtype=pl.Float64)
    return df.select(blended).to_series()


def _normalise_nonnegative(weights: dict[str, float]) -> dict[str, float]:
    """Clip negatives to 0 and renormalise to sum to 1.

    If clipping wipes out every weight, fall back to equal weights rather
    than emitting an unusable all-zero blend.
    """
    clipped = {k: max(0.0, v) for k, v in weights.items()}
    total = sum(clipped.values())
    if total <= 0:
        if not clipped:
            return {}
        equal = 1.0 / len(clipped)
        return dict.fromkeys(clipped, equal)
    return {k: v / total for k, v in clipped.items()}


def _feature_columns(df: pl.DataFrame, proj_cols: list[str]) -> list[str]:
    extras = [c for c in EXTRA_FEATURE_COLUMNS if c in df.columns]
    return proj_cols + extras


def _feature_matrix(df: pl.DataFrame, columns: list[str]) -> np.ndarray:
    """Numpy is confined to this function and the estimator call sites."""
    if not columns:
        return np.zeros((df.height, 0), dtype=float)
    missing = [c for c in columns if c not in df.columns]
    frame = df.with_columns([pl.lit(None, dtype=pl.Float64).alias(c) for c in missing])
    return frame.select([pl.col(c).cast(pl.Float64) for c in columns]).to_numpy()


def _with_prediction(df: pl.DataFrame, values: np.ndarray | pl.Series) -> pl.DataFrame:
    series = (
        values
        if isinstance(values, pl.Series)
        else pl.Series(PREDICTION_COLUMN, values, dtype=pl.Float64)
    )
    return _keys(df).with_columns(series.alias(PREDICTION_COLUMN))


class _FittableMixin:
    """Shared fit bookkeeping: source discovery and the sparse-data fallback."""

    name: str = "unnamed"

    def __init__(
        self, lambda_: float = RECENCY_LAMBDA, min_fit_rows: int = MIN_FIT_ROWS
    ):
        self.lambda_ = lambda_
        self.min_fit_rows = min_fit_rows
        #: Season that `years_ago` is measured from. Left as None so a
        #: standalone fit anchors on its own newest season; the LOSO harness
        #: sets it to the newest season of the *whole* dataset so every fold
        #: weights a given row identically and the folds stay comparable.
        self.reference_season: int | None = None
        self.sources_: list[str] = []
        self.fallback_: EqualWeightMean | None = None
        self.n_train_rows_: int = 0

    def _weights_for(self, df: pl.DataFrame) -> np.ndarray:
        return recency_weights(df, self.lambda_, self.reference_season)

    def _prepare(self, train_df: pl.DataFrame) -> list[str]:
        self.sources_ = [source_name(c) for c in projection_columns(train_df)]
        self.n_train_rows_ = train_df.height
        self.fallback_ = None
        return projection_columns(train_df)

    def _use_fallback(self, train_df: pl.DataFrame, reason: str) -> None:
        logger.info(
            "%s: falling back to EqualWeightMean (%s, %d rows)",
            self.name,
            reason,
            train_df.height,
        )
        # With no proj_* columns the fallback cannot be fitted either. Raise
        # naming the calibrator the caller actually asked for, so the error
        # does not appear to come from EqualWeightMean out of nowhere.
        if not projection_columns(train_df):
            raise ValueError(
                f"{self.name}.fit: no proj_* columns in training frame "
                "(and so the EqualWeightMean fallback cannot be fitted either)"
            )
        self.fallback_ = EqualWeightMean(lambda_=self.lambda_)
        self.fallback_.reference_season = self.reference_season
        self.fallback_.fit(train_df)

    @property
    def is_fallback(self) -> bool:
        return self.fallback_ is not None


# --------------------------------------------------------------------------
# 1-3: weight-based calibrators
# --------------------------------------------------------------------------


class EqualWeightMean(_FittableMixin):
    """Unweighted mean of whichever sources are present for a player.

    Needs no real fit -- `fit()` only records which sources exist -- which is
    exactly why it is the fallback for every other calibrator.
    """

    name = "EqualWeightMean"

    def fit(self, train_df: pl.DataFrame) -> None:
        self._prepare(train_df)
        if not self.sources_:
            raise ValueError("EqualWeightMean.fit: no proj_* columns in training frame")

    @property
    def weights(self) -> dict[str, float]:
        return dict.fromkeys(self.sources_, 1.0 / len(self.sources_))

    def predict(self, df: pl.DataFrame) -> pl.DataFrame:
        return _with_prediction(df, _renormalised_blend(df, self.weights))


class FantasyProsOnly(_FittableMixin):
    """Passthrough of the FantasyPros scored projection (the market baseline)."""

    name = "FantasyProsOnly"

    def __init__(
        self,
        source: str = FANTASYPROS_SOURCE,
        lambda_: float = RECENCY_LAMBDA,
        min_fit_rows: int = MIN_FIT_ROWS,
    ):
        super().__init__(lambda_=lambda_, min_fit_rows=min_fit_rows)
        self.source = source

    def fit(self, train_df: pl.DataFrame) -> None:
        self._prepare(train_df)
        if self.source not in self.sources_:
            logger.warning(
                "FantasyProsOnly.fit: source %r absent from training frame; "
                "predictions will be null",
                self.source,
            )

    @property
    def weights(self) -> dict[str, float]:
        return {self.source: 1.0}

    def predict(self, df: pl.DataFrame) -> pl.DataFrame:
        return _with_prediction(df, _renormalised_blend(df, self.weights))


class RidgeBlend(_FittableMixin):
    """Ridge regression on the source-projection columns.

    Fitted without an intercept so the coefficients *are* the blend weights:
    negatives are clipped to 0 and the survivors renormalised to sum to 1,
    which then lets `predict()` renormalise again over whichever sources a
    given player actually has. An intercept would break that interpretation
    (you cannot renormalise a constant offset over present sources).

    Fitting uses complete cases only -- rows where every source is present.
    Imputing a missing source before fitting would teach the model a made-up
    number; renormalisation at predict time is the principled way to handle
    the gap.
    """

    name = "RidgeBlend"

    def __init__(
        self,
        alpha: float = 1.0,
        lambda_: float = RECENCY_LAMBDA,
        min_fit_rows: int = MIN_FIT_ROWS,
    ):
        super().__init__(lambda_=lambda_, min_fit_rows=min_fit_rows)
        self.alpha = alpha
        self.weights_: dict[str, float] = {}

    def fit(self, train_df: pl.DataFrame) -> None:
        from sklearn.linear_model import Ridge

        proj_cols = self._prepare(train_df)
        self.weights_ = {}
        complete = train_df.drop_nulls(subset=[*proj_cols, TARGET_COLUMN])
        if complete.height < self.min_fit_rows or not proj_cols:
            self._use_fallback(train_df, "too few complete-case rows")
            return

        x = _feature_matrix(complete, proj_cols)
        y = complete[TARGET_COLUMN].to_numpy()
        weights = self._weights_for(complete)
        model = Ridge(alpha=self.alpha, fit_intercept=False)
        model.fit(x, y, sample_weight=weights)
        raw = {source_name(c): float(w) for c, w in zip(proj_cols, model.coef_)}
        self.raw_coefficients_ = raw
        self.weights_ = _normalise_nonnegative(raw)

    @property
    def weights(self) -> dict[str, float]:
        if self.fallback_ is not None:
            return self.fallback_.weights
        return self.weights_

    def predict(self, df: pl.DataFrame) -> pl.DataFrame:
        if self.fallback_ is not None:
            return self.fallback_.predict(df)
        return _with_prediction(df, _renormalised_blend(df, self.weights_))


# --------------------------------------------------------------------------
# 4-6: learned calibrators
# --------------------------------------------------------------------------


class LightGBMBlend(_FittableMixin):
    """LightGBM on source projections plus prior-season/usage features.

    Feature set is `proj_*` plus whichever of `EXTRA_FEATURE_COLUMNS` the
    training frame carries (`age` only if the caller supplied it -- the
    pipeline has no birthdate source yet). LightGBM consumes nulls natively,
    so missing sources need no imputation here; the renormalisation rule is
    specific to the weight-based calibrators.
    """

    name = "LightGBMBlend"

    def __init__(
        self,
        lambda_: float = RECENCY_LAMBDA,
        min_fit_rows: int = MIN_FIT_ROWS,
        n_estimators: int = 200,
        learning_rate: float = 0.05,
        num_leaves: int = 15,
        random_state: int = 0,
    ):
        super().__init__(lambda_=lambda_, min_fit_rows=min_fit_rows)
        self.n_estimators = n_estimators
        self.learning_rate = learning_rate
        self.num_leaves = num_leaves
        self.random_state = random_state
        self.features_: list[str] = []
        self.model_ = None

    def _make_model(self):
        from lightgbm import LGBMRegressor

        return LGBMRegressor(
            n_estimators=self.n_estimators,
            learning_rate=self.learning_rate,
            num_leaves=self.num_leaves,
            min_child_samples=5,
            random_state=self.random_state,
            verbose=-1,
        )

    def fit(self, train_df: pl.DataFrame) -> None:
        proj_cols = self._prepare(train_df)
        self.model_ = None
        usable = train_df.drop_nulls(subset=[TARGET_COLUMN])
        if usable.height < self.min_fit_rows or not proj_cols:
            self._use_fallback(train_df, "too few rows for a tree ensemble")
            return
        self.features_ = _feature_columns(usable, proj_cols)
        x = _feature_matrix(usable, self.features_)
        y = usable[TARGET_COLUMN].to_numpy()
        self.model_ = self._make_model()
        self.model_.fit(x, y, sample_weight=self._weights_for(usable))

    def predict(self, df: pl.DataFrame) -> pl.DataFrame:
        if self.fallback_ is not None:
            return self.fallback_.predict(df)
        if self.model_ is None:
            raise RuntimeError("LightGBMBlend.predict called before fit")
        if df.height == 0:
            return _with_prediction(df, np.empty(0, dtype=float))
        features = _feature_matrix(df, self.features_)
        return _with_prediction(df, self.model_.predict(features))


class SmallMLP(_FittableMixin):
    """Small `MLPRegressor` (2 hidden layers) on the LightGBMBlend features.

    Two accommodations sklearn forces here:

    * `MLPRegressor` cannot impute or standardise, so it is wrapped in a
      `SimpleImputer(median) -> StandardScaler -> MLPRegressor` pipeline.
    * `MLPRegressor.fit()` accepts no `sample_weight`. Rather than silently
      dropping the recency weighting, we apply it by resampling the training
      rows with replacement with probability proportional to the weights,
      using a seeded RNG so the fit stays deterministic. This is an
      approximation to true importance weighting, not an equivalent.
    """

    name = "SmallMLP"

    def __init__(
        self,
        lambda_: float = RECENCY_LAMBDA,
        min_fit_rows: int = MIN_FIT_ROWS,
        hidden_layer_sizes: tuple[int, ...] = (32, 16),
        max_iter: int = 800,
        random_state: int = 0,
    ):
        super().__init__(lambda_=lambda_, min_fit_rows=min_fit_rows)
        self.hidden_layer_sizes = hidden_layer_sizes
        self.max_iter = max_iter
        self.random_state = random_state
        self.features_: list[str] = []
        self.model_ = None

    def fit(self, train_df: pl.DataFrame) -> None:
        from sklearn.impute import SimpleImputer
        from sklearn.neural_network import MLPRegressor
        from sklearn.pipeline import make_pipeline
        from sklearn.preprocessing import StandardScaler

        proj_cols = self._prepare(train_df)
        self.model_ = None
        usable = train_df.drop_nulls(subset=[TARGET_COLUMN])
        if usable.height < self.min_fit_rows or not proj_cols:
            self._use_fallback(train_df, "too few rows to train an MLP")
            return

        self.features_ = _feature_columns(usable, proj_cols)
        x = _feature_matrix(usable, self.features_)
        y = usable[TARGET_COLUMN].to_numpy()

        weights = self._weights_for(usable)
        rng = np.random.default_rng(self.random_state)
        probabilities = weights / weights.sum()
        picks = rng.choice(len(y), size=len(y), replace=True, p=probabilities)
        x, y = x[picks], y[picks]

        self.model_ = make_pipeline(
            SimpleImputer(strategy="median"),
            StandardScaler(),
            MLPRegressor(
                hidden_layer_sizes=self.hidden_layer_sizes,
                max_iter=self.max_iter,
                random_state=self.random_state,
            ),
        )
        import warnings

        from sklearn.exceptions import ConvergenceWarning

        with warnings.catch_warnings():
            warnings.simplefilter("ignore", ConvergenceWarning)
            self.model_.fit(x, y)

    def predict(self, df: pl.DataFrame) -> pl.DataFrame:
        if self.fallback_ is not None:
            return self.fallback_.predict(df)
        if self.model_ is None:
            raise RuntimeError("SmallMLP.predict called before fit")
        if df.height == 0:
            return _with_prediction(df, np.empty(0, dtype=float))
        features = _feature_matrix(df, self.features_)
        return _with_prediction(df, self.model_.predict(features))


class QuantileBlend(_FittableMixin):
    """Three LightGBM quantile regressors at p25/p50/p75 (floor/median/ceiling).

    LightGBM was chosen over `GradientBoostingRegressor(loss="quantile")`
    because it accepts nulls natively (so the feature matrix needs no
    imputation, matching `LightGBMBlend`) and is materially faster to fit
    three times over.

    `predict()` returns `prediction_p25`, `prediction` (the p50), and
    `prediction_p75`. The three are sorted row-wise afterwards so quantile
    crossing -- independently fitted quantiles occasionally coming out in the
    wrong order -- can never produce a floor above a ceiling.
    """

    name = "QuantileBlend"

    def __init__(
        self,
        lambda_: float = RECENCY_LAMBDA,
        min_fit_rows: int = MIN_FIT_ROWS,
        n_estimators: int = 200,
        learning_rate: float = 0.05,
        num_leaves: int = 15,
        random_state: int = 0,
    ):
        super().__init__(lambda_=lambda_, min_fit_rows=min_fit_rows)
        self.n_estimators = n_estimators
        self.learning_rate = learning_rate
        self.num_leaves = num_leaves
        self.random_state = random_state
        self.features_: list[str] = []
        self.models_: dict[float, object] = {}

    def fit(self, train_df: pl.DataFrame) -> None:
        from lightgbm import LGBMRegressor

        proj_cols = self._prepare(train_df)
        self.models_ = {}
        usable = train_df.drop_nulls(subset=[TARGET_COLUMN])
        if usable.height < self.min_fit_rows or not proj_cols:
            self._use_fallback(train_df, "too few rows for quantile ensembles")
            return

        self.features_ = _feature_columns(usable, proj_cols)
        x = _feature_matrix(usable, self.features_)
        y = usable[TARGET_COLUMN].to_numpy()
        sample_weight = self._weights_for(usable)

        for level in QUANTILE_LEVELS:
            model = LGBMRegressor(
                objective="quantile",
                alpha=level,
                n_estimators=self.n_estimators,
                learning_rate=self.learning_rate,
                num_leaves=self.num_leaves,
                min_child_samples=5,
                random_state=self.random_state,
                verbose=-1,
            )
            model.fit(x, y, sample_weight=sample_weight)
            self.models_[level] = model

    def predict(self, df: pl.DataFrame) -> pl.DataFrame:
        if self.fallback_ is None and not self.models_:
            raise RuntimeError("QuantileBlend.predict called before fit")
        if self.fallback_ is not None:
            fallback = self.fallback_.predict(df)
            return fallback.with_columns(
                pl.col(PREDICTION_COLUMN).alias("prediction_p25"),
                pl.col(PREDICTION_COLUMN).alias("prediction_p75"),
            )
        if df.height == 0:
            empty = np.empty(0, dtype=float)
            return _with_prediction(df, empty).with_columns(
                pl.Series("prediction_p25", empty, dtype=pl.Float64),
                pl.Series("prediction_p75", empty, dtype=pl.Float64),
            )

        x = _feature_matrix(df, self.features_)
        raw = np.stack([self.models_[level].predict(x) for level in QUANTILE_LEVELS])
        low, mid, high = np.sort(raw, axis=0)
        return _keys(df).with_columns(
            pl.Series("prediction_p25", low, dtype=pl.Float64),
            pl.Series(PREDICTION_COLUMN, mid, dtype=pl.Float64),
            pl.Series("prediction_p75", high, dtype=pl.Float64),
        )


def default_calibrators(
    lambda_: float = RECENCY_LAMBDA, min_fit_rows: int = MIN_FIT_ROWS
) -> dict[str, Calibrator]:
    """The six spec calibrators, keyed by name, ready to hand to LOSO."""
    return {
        c.name: c
        for c in (
            EqualWeightMean(lambda_=lambda_, min_fit_rows=min_fit_rows),
            FantasyProsOnly(lambda_=lambda_, min_fit_rows=min_fit_rows),
            RidgeBlend(lambda_=lambda_, min_fit_rows=min_fit_rows),
            LightGBMBlend(lambda_=lambda_, min_fit_rows=min_fit_rows),
            SmallMLP(lambda_=lambda_, min_fit_rows=min_fit_rows),
            QuantileBlend(lambda_=lambda_, min_fit_rows=min_fit_rows),
        )
    }


# --------------------------------------------------------------------------
# LOSO evaluation
# --------------------------------------------------------------------------

LOSO_COLUMNS = [
    "position",
    "calibrator",
    "n_folds",
    "n_rows",
    "coverage",
    "mae",
    "rank_corr",
    "is_winner",
]


def _rank_correlation(actual: np.ndarray, predicted: np.ndarray) -> float | None:
    """Spearman rho, or None when it is undefined (n < 3 or a constant vector)."""
    if len(actual) < 3:
        return None
    if np.all(actual == actual[0]) or np.all(predicted == predicted[0]):
        return None
    rho = spearmanr(actual, predicted).statistic
    return None if np.isnan(rho) else float(rho)


def run_loso_evaluation(
    train_df: pl.DataFrame,
    calibrators: dict[str, Calibrator] | None = None,
    lambda_: float = RECENCY_LAMBDA,
) -> pl.DataFrame:
    """Leave-one-season-out cross-validation, per position and calibrator.

    For each position, each season present is held out in turn, every
    calibrator is fitted fresh (a `deepcopy`, so the caller's instances are
    never mutated) on the remaining seasons and predicts the held-out one.

    Every calibrator in a fold is scored on the fold's **common support**:
    the held-out rows on which *every* calibrator produced a non-null
    prediction. Without this, a calibrator that declines to predict for some
    players (`FantasyProsOnly` when that source is missing, any weight-based
    calibrator for a player with no sources at all) would be scored on a
    smaller and systematically easier subset than one that predicts
    everywhere, and the resulting MAEs would not be comparable. `coverage`
    reports what was given up: the mean fraction of held-out rows each
    calibrator could predict on its own, before intersection.

    Metrics, per `(position, calibrator)`:

    * `mae`   -- pooled mean absolute error over every common-support row
                 across all folds. Pooled rather than averaged-over-folds so
                 seasons with more players count proportionally.
    * `rank_corr` -- Spearman rho computed *within* each held-out season and
                 then averaged over folds. Ranking players is a within-season
                 question; pooling seasons would let cross-season scoring
                 drift inflate the correlation.
    * `coverage` -- mean over folds of (rows this calibrator could predict) /
                 (held-out rows). 1.0 means it never abstained. Because
                 `mae` is computed on common support, a low-coverage
                 calibrator no longer gets an unearned advantage -- but a
                 winner with `coverage < 1.0` still needs a fallback in
                 `blend.py` for the players it cannot score.

    `is_winner` marks the best calibrator per position: lowest `mae`, with
    `rank_corr` (higher better) as the tiebreak. Positions with fewer than
    two seasons of data are skipped with a warning -- LOSO is undefined there.

    Returns one row per position x calibrator.
    """
    calibrators = calibrators or default_calibrators(lambda_=lambda_)
    reference_season = int(train_df["season"].max()) if train_df.height else None
    records: list[dict[str, object]] = []

    for (position,) in train_df.select("position").unique().sort("position").rows():
        position_df = train_df.filter(pl.col("position") == position)
        seasons = sorted(position_df["season"].unique().to_list())
        if len(seasons) < 2:
            logger.warning(
                "LOSO: skipping position %s -- only %d season(s) of data",
                position,
                len(seasons),
            )
            continue

        errors: dict[str, list[np.ndarray]] = {n: [] for n in calibrators}
        correlations: dict[str, list[float]] = {n: [] for n in calibrators}
        coverages: dict[str, list[float]] = {n: [] for n in calibrators}
        folds: dict[str, int] = dict.fromkeys(calibrators, 0)

        for holdout in seasons:
            train_fold = position_df.filter(pl.col("season") != holdout)
            test_fold = position_df.filter(pl.col("season") == holdout)
            if train_fold.height == 0 or test_fold.height == 0:
                continue

            # Fit and predict everything first, then intersect: MAE is only
            # meaningful between calibrators scored on the same rows.
            predictions: dict[str, np.ndarray] = {}
            for name, template in calibrators.items():
                model = copy.deepcopy(template)
                model.lambda_ = lambda_
                model.reference_season = reference_season
                model.fit(train_fold)
                values = model.predict(test_fold)[PREDICTION_COLUMN].to_numpy(
                    allow_copy=True
                )
                predictions[name] = values.astype(float)
                coverages[name].append(
                    float(np.mean(~np.isnan(predictions[name])))
                    if test_fold.height
                    else 0.0
                )

            actual = test_fold[TARGET_COLUMN].to_numpy()
            common = ~np.isnan(actual)
            for values in predictions.values():
                common &= ~np.isnan(values)
            if not common.any():
                logger.warning(
                    "LOSO: position %s season %d has no rows every calibrator "
                    "can predict -- fold skipped",
                    position,
                    holdout,
                )
                continue

            actual_values = actual[common]
            for name, values in predictions.items():
                predicted_values = values[common]
                errors[name].append(np.abs(actual_values - predicted_values))
                rho = _rank_correlation(actual_values, predicted_values)
                if rho is not None:
                    correlations[name].append(rho)
                folds[name] += 1

        for name in calibrators:
            pooled = np.concatenate(errors[name]) if errors[name] else np.empty(0)
            records.append(
                {
                    "position": position,
                    "calibrator": name,
                    "n_folds": folds[name],
                    "n_rows": int(pooled.size),
                    "coverage": (
                        float(np.mean(coverages[name])) if coverages[name] else None
                    ),
                    "mae": float(pooled.mean()) if pooled.size else None,
                    "rank_corr": (
                        float(np.mean(correlations[name]))
                        if correlations[name]
                        else None
                    ),
                }
            )

    if not records:
        return pl.DataFrame(
            schema={
                "position": pl.String,
                "calibrator": pl.String,
                "n_folds": pl.Int64,
                "n_rows": pl.Int64,
                "coverage": pl.Float64,
                "mae": pl.Float64,
                "rank_corr": pl.Float64,
                "is_winner": pl.Boolean,
            }
        )

    # Nulls sort last on both keys: a calibrator that could not be evaluated
    # must never win a position by default.
    results = pl.DataFrame(records).with_columns(
        pl.col("mae").fill_null(float("inf")).alias("_mae"),
        (-pl.col("rank_corr").fill_null(-float("inf"))).alias("_neg_rank_corr"),
    )
    winners = (
        results.sort(["position", "_mae", "_neg_rank_corr", "calibrator"])
        .group_by("position", maintain_order=True)
        .agg(pl.col("calibrator").first().alias("_winner"))
    )
    return (
        results.join(winners, on="position", how="left")
        .with_columns((pl.col("calibrator") == pl.col("_winner")).alias("is_winner"))
        .select(LOSO_COLUMNS)
        .sort(["position", "mae"], nulls_last=True)
    )


def print_loso_table(loso_df: pl.DataFrame, console: object | None = None) -> None:
    """Render the LOSO comparison table via `rich`, winners highlighted.

    `console` is injectable so callers (and tests) can control width; rich
    otherwise sizes to the terminal and abbreviates calibrator names.
    """
    from rich.console import Console
    from rich.table import Table

    table = Table(title="LOSO calibration comparison (per position)")
    table.add_column("Position")
    table.add_column("Calibrator")
    table.add_column("Folds", justify="right")
    table.add_column("Rows", justify="right")
    table.add_column("Coverage", justify="right")
    table.add_column("MAE", justify="right")
    table.add_column("Rank corr", justify="right")
    table.add_column("Winner", justify="center")

    for row in loso_df.iter_rows(named=True):
        style = "bold green" if row["is_winner"] else None
        table.add_row(
            row["position"],
            row["calibrator"],
            str(row["n_folds"]),
            str(row["n_rows"]),
            "n/a" if row["coverage"] is None else f"{row['coverage']:.1%}",
            "n/a" if row["mae"] is None else f"{row['mae']:.3f}",
            "n/a" if row["rank_corr"] is None else f"{row['rank_corr']:.3f}",
            "*" if row["is_winner"] else "",
            style=style,
        )

    (console or Console()).print(table)


# --------------------------------------------------------------------------
# data/derived/source_weights.parquet
# --------------------------------------------------------------------------

#: Schema of `data/derived/source_weights.parquet` -- ONE ROW PER POSITION,
#: describing the LOSO-winning calibrator for that position:
#:
#:   position        str        QB/RB/WR/TE/K/DST
#:   calibrator      str        winning calibrator's `name`
#:   is_fallback     bool       True if it degraded to EqualWeightMean for
#:                              lack of training rows
#:   sources         list[str]  source names, index-aligned with `weights`
#:   weights         list[f64]  non-negative blend weights summing to 1, or
#:                              null for calibrators with no closed-form
#:                              weights (LightGBMBlend/SmallMLP/QuantileBlend)
#:   intercept       f64        0.0 for the weight-based calibrators (they are
#:                              fitted intercept-free); null otherwise
#:   model_pickle    binary     `pickle.dumps()` of the *fitted* Calibrator
#:   mae             f64        winning LOSO pooled MAE
#:   rank_corr       f64        winning LOSO mean within-season Spearman rho
#:   n_train_rows    i64        rows used for the final full-data refit
#:   fitted_at       str        ISO-8601 UTC timestamp of the refit
#:   schema_version  i32        currently 1
#:
#: `model/blend.py` (Task 9) has two ways to consume this and should prefer
#: the first:
#:   1. `load_fitted_calibrators(path)` -> `{position: Calibrator}` and call
#:      `.predict(wide_df)`. Works for all six calibrators, no re-fitting.
#:      Unpickling requires the same `ffdraft.model.calibrate` module, so
#:      regenerate the parquet whenever this module's classes change.
#:   2. Read `sources`/`weights` directly and apply
#:      `sum(w*x)/sum(w)` over non-null sources -- a dependency-free path,
#:      but only defined for the weight-based winners.
SOURCE_WEIGHTS_COLUMNS = [
    "position",
    "calibrator",
    "is_fallback",
    "sources",
    "weights",
    "intercept",
    "model_pickle",
    "mae",
    "rank_corr",
    "n_train_rows",
    "fitted_at",
    "schema_version",
]


def fit_winning_calibrators(
    train_df: pl.DataFrame,
    loso_df: pl.DataFrame,
    calibrators: dict[str, Calibrator] | None = None,
    lambda_: float = RECENCY_LAMBDA,
) -> pl.DataFrame:
    """Refit each position's LOSO winner on that position's full data.

    LOSO picks the winner; the shipped model is then refitted on every
    season, since throwing away a season of training data in the artefact
    would be strictly worse than the fold models it was selected from.
    """
    calibrators = calibrators or default_calibrators(lambda_=lambda_)
    fitted_at = dt.datetime.now(dt.UTC).isoformat(timespec="seconds")
    records: list[dict[str, object]] = []

    reference_season = int(train_df["season"].max()) if train_df.height else None
    winners = loso_df.filter(pl.col("is_winner"))
    for row in winners.iter_rows(named=True):
        position = row["position"]
        position_df = train_df.filter(pl.col("position") == position)
        model = copy.deepcopy(calibrators[row["calibrator"]])
        model.lambda_ = lambda_
        model.reference_season = reference_season
        model.fit(position_df)

        # A learned calibrator that degraded to EqualWeightMean does have
        # closed-form weights -- read them off the fallback, not the shell.
        weights = getattr(model, "weights", None)
        if weights is None and model.is_fallback:
            weights = model.fallback_.weights
        records.append(
            {
                "position": position,
                "calibrator": row["calibrator"],
                "is_fallback": bool(getattr(model, "is_fallback", False)),
                "sources": (
                    list(weights) if weights else list(getattr(model, "sources_", []))
                ),
                "weights": list(weights.values()) if weights else None,
                "intercept": 0.0 if weights else None,
                "model_pickle": pickle.dumps(model),
                "mae": row["mae"],
                "rank_corr": row["rank_corr"],
                "n_train_rows": position_df.height,
                "fitted_at": fitted_at,
                "schema_version": SOURCE_WEIGHTS_SCHEMA_VERSION,
            }
        )

    schema = {
        "position": pl.String,
        "calibrator": pl.String,
        "is_fallback": pl.Boolean,
        "sources": pl.List(pl.String),
        "weights": pl.List(pl.Float64),
        "intercept": pl.Float64,
        "model_pickle": pl.Binary,
        "mae": pl.Float64,
        "rank_corr": pl.Float64,
        "n_train_rows": pl.Int64,
        "fitted_at": pl.String,
        "schema_version": pl.Int32,
    }
    return pl.DataFrame(records, schema=schema).select(SOURCE_WEIGHTS_COLUMNS)


def write_source_weights(
    weights_df: pl.DataFrame, path: Path = DEFAULT_SOURCE_WEIGHTS_PATH
) -> Path:
    """Write the source-weights artefact, creating parent directories."""
    path.parent.mkdir(parents=True, exist_ok=True)
    weights_df.write_parquet(path)
    return path


def load_source_weights(path: Path = DEFAULT_SOURCE_WEIGHTS_PATH) -> pl.DataFrame:
    """Read `source_weights.parquet` as-is (see `SOURCE_WEIGHTS_COLUMNS`)."""
    return pl.read_parquet(path)


def load_fitted_calibrators(
    path: Path = DEFAULT_SOURCE_WEIGHTS_PATH,
) -> dict[str, Calibrator]:
    """Unpickle the per-position winning calibrators, ready to `.predict()`."""
    weights_df = load_source_weights(path)
    return {
        row["position"]: pickle.loads(row["model_pickle"])
        for row in weights_df.iter_rows(named=True)
    }


def run_calibration(
    projections: pl.DataFrame,
    actuals: pl.DataFrame,
    rules: dict[str, float],
    lambda_: float = RECENCY_LAMBDA,
    out_path: Path = DEFAULT_SOURCE_WEIGHTS_PATH,
) -> tuple[pl.DataFrame, pl.DataFrame]:
    """End-to-end: build training rows, run LOSO, refit winners, write parquet.

    Returns `(loso_df, source_weights_df)`.
    """
    train_df = build_training_frame(projections, actuals, rules)
    loso_df = run_loso_evaluation(train_df, lambda_=lambda_)
    weights_df = fit_winning_calibrators(train_df, loso_df, lambda_=lambda_)
    write_source_weights(weights_df, out_path)
    return loso_df, weights_df
