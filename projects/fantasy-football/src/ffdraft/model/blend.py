"""Apply Task 8's fitted per-position calibrators to a season's projections.

`apply_blend` is the one function this module exports. It takes the current
season's raw source projections in the canonical long schema, scores them
with `ffdraft.scoring.score()`, pivots to the wide `proj_<source>` shape
`calibrate.py`'s calibrators expect, and applies each position's LOSO-winning
calibrator (loaded from `data/derived/source_weights.parquet`) to produce one
blended `EP` (expected points) row per player.

Two things this module is deliberately careful about, both flagged in
Task 8's review:

Coverage < 1.0
--------------
A winning calibrator can abstain (return a null prediction) for some
players -- `FantasyProsOnly` when its source is missing for that player, or
any weight-based calibrator for a player with zero present sources. This
module never ships a null EP for a player who has *any* usable source: any
row the winning calibrator could not score is re-scored with
`calibrate.EqualWeightMean`, renormalised over whatever sources that player
actually has. Only a player with literally no source data at all keeps a
null EP -- there is nothing left to fall back to.

`schema_version` / stale pickles
---------------------------------
`calibrate.load_fitted_calibrators` unpickles `model_pickle` with no version
check, and unpickling a `Calibrator` built by an older `calibrate.py` can
raise an arbitrary, unhelpful exception. `_load_checked_calibrators` in this
module checks `schema_version` itself and wraps the unpickle in a
try/except, so a stale or corrupt artefact raises a clear `BlendArtifactError`
naming the offending position instead of crashing the board-building
pipeline with a cryptic pickle traceback.
"""

from __future__ import annotations

import pickle
from pathlib import Path

import polars as pl

from ffdraft import scoring
from ffdraft.model import calibrate

EP_COLUMN = "EP"
EP_FLOOR_COLUMN = "EP_p25"
EP_CEILING_COLUMN = "EP_p75"

#: Metadata columns carried through from the input frame, alongside the key
#: columns (`season`, `player_id`, `position`), purely for the convenience of
#: downstream consumers (VOR, the board). Never used as model features.
META_COLUMNS = ["team", "player_name_raw"]


class BlendArtifactError(RuntimeError):
    """`source_weights.parquet` is missing, version-mismatched, or corrupt."""


def _load_checked_calibrators(path: Path) -> dict[str, calibrate.Calibrator]:
    """Load per-position calibrators, validating `schema_version` and the pickle.

    `calibrate.load_fitted_calibrators` does neither check -- see this
    module's docstring for why both matter here.
    """
    weights_df = calibrate.load_source_weights(path)

    bad_versions = sorted(
        v
        for v in weights_df["schema_version"].unique().to_list()
        if v != calibrate.SOURCE_WEIGHTS_SCHEMA_VERSION
    )
    if bad_versions:
        raise BlendArtifactError(
            f"{path}: schema_version {bad_versions} in the artefact does not "
            f"match the schema_version {calibrate.SOURCE_WEIGHTS_SCHEMA_VERSION} "
            "this build of ffdraft expects. Re-run calibration to regenerate "
            "source_weights.parquet against the current ffdraft.model.calibrate."
        )

    models: dict[str, calibrate.Calibrator] = {}
    for row in weights_df.iter_rows(named=True):
        try:
            models[row["position"]] = pickle.loads(row["model_pickle"])
        except Exception as exc:
            raise BlendArtifactError(
                f"{path}: could not unpickle the {row['calibrator']} calibrator "
                f"for position {row['position']!r}: {exc!r}. This usually means "
                "the pickle was written by a different version of "
                "ffdraft.model.calibrate -- re-run calibration to regenerate "
                "the artefact."
            ) from exc
    return models


def _wide_projections(
    projections: pl.DataFrame, rules: dict[str, float]
) -> pl.DataFrame:
    """Score `projections` and pivot to one row per `(season, player_id)`.

    Mirrors `calibrate.build_training_frame`'s projection-side pivot exactly
    (same seasonal-sum-then-pivot shape), since the calibrators were fitted
    against that shape and must be predicted against the same one.
    """
    scored = (
        scoring.score(projections, rules)
        .group_by(["season", "source", "player_id"])
        .agg(pl.col("fantasy_points").sum())
    )

    wide = scored.with_columns(
        (pl.lit(calibrate.PROJ_PREFIX) + pl.col("source")).alias("proj_column")
    ).pivot(on="proj_column", index=["season", "player_id"], values="fantasy_points")

    meta_columns = [c for c in ["position", *META_COLUMNS] if c in projections.columns]
    meta = (
        projections.select(["season", "player_id", *meta_columns])
        .drop_nulls(subset=["player_id"])
        .group_by(["season", "player_id"])
        .agg([pl.col(c).drop_nulls().first().alias(c) for c in meta_columns])
    )
    return wide.join(meta, on=["season", "player_id"], how="left").sort(
        ["season", "player_id"]
    )


def _key_columns(df: pl.DataFrame) -> list[str]:
    return [c for c in calibrate.KEY_COLUMNS if c in df.columns]


def _equal_weight_ep(wide: pl.DataFrame) -> pl.DataFrame:
    """`EqualWeightMean` blend, renamed to `EP`. Null iff no source is present."""
    if wide.height == 0:
        return wide.select(_key_columns(wide)).with_columns(
            pl.Series(EP_COLUMN, [], dtype=pl.Float64)
        )
    model = calibrate.EqualWeightMean()
    model.fit(wide)
    return model.predict(wide).rename({calibrate.PREDICTION_COLUMN: EP_COLUMN})


def _predict_position(
    position: str, group: pl.DataFrame, model: object | None
) -> pl.DataFrame:
    """Blend one position's rows, filling any coverage gap with an equal-weight mean."""
    fallback = _equal_weight_ep(group)

    if model is None:
        # No LOSO winner recorded for this position at all (e.g. too little
        # history to run LOSO) -- equal-weight mean over present sources is
        # the whole answer, not just a gap-filler.
        return fallback

    predicted = model.predict(group)
    blended = predicted.rename({calibrate.PREDICTION_COLUMN: EP_COLUMN})
    if "prediction_p25" in predicted.columns:
        blended = blended.rename(
            {"prediction_p25": EP_FLOOR_COLUMN, "prediction_p75": EP_CEILING_COLUMN}
        )

    join_keys = [c for c in calibrate.KEY_COLUMNS if c in blended.columns]
    blended = blended.join(
        fallback.rename({EP_COLUMN: "_fallback_ep"}), on=join_keys, how="left"
    ).with_columns(pl.col(EP_COLUMN).fill_null(pl.col("_fallback_ep")))
    return blended.drop("_fallback_ep")


def apply_blend(
    projections: pl.DataFrame,
    weights_path: Path,
    rules: dict[str, float],
) -> pl.DataFrame:
    """Blend a season's raw source projections into one `EP` row per player.

    `projections` is the canonical long-schema frame for the season being
    drafted (`season, week, source, snapshot_date, source_player_id,
    player_id, player_name_raw, team, position, stat_name, stat_value`).
    `weights_path` points at Task 8's `source_weights.parquet`. `rules` is
    the same scoring-rules dict `scoring.score()` always takes.

    Returns one row per `(season, player_id)` with `position`, `team`,
    `player_name_raw`, `EP`, and -- for positions whose winning calibrator is
    `QuantileBlend` -- `EP_p25`/`EP_p75`. Renormalisation over whichever
    sources a player actually has, and the coverage<1.0 fallback, are exactly
    Task 8's missing-source semantics; see the module docstring.
    """
    wide = _wide_projections(projections, rules)
    calibrators = _load_checked_calibrators(weights_path)

    parts = []
    for position in wide["position"].unique(maintain_order=True).to_list():
        group = wide.filter(pl.col("position") == position)
        blended = _predict_position(position, group, calibrators.get(position))
        meta_cols = [c for c in [*META_COLUMNS] if c in group.columns]
        meta = group.select(["season", "player_id", "position", *meta_cols])
        parts.append(
            blended.join(meta, on=["season", "player_id", "position"], how="left")
        )

    if not parts:
        schema = {
            "season": pl.Int64,
            "player_id": pl.String,
            "position": pl.String,
            EP_COLUMN: pl.Float64,
            "team": pl.String,
            "player_name_raw": pl.String,
        }
        return pl.DataFrame(schema=schema)

    result = pl.concat(parts, how="diagonal_relaxed")
    front = ["season", "player_id", "position", EP_COLUMN]
    rest = [c for c in result.columns if c not in front]
    return result.select([*front, *rest]).sort(["position", "player_id"])
