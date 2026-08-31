"""Score canonical long-schema stat rows into fantasy points.

`score()` is the single scoring path used for both source projections and
nflverse actuals -- calibration (a later task) compares the two directly, so
there must be exactly one function computing fantasy points, not two
similar-but-diverging ones.

Bucketed stat names (kicking-distance buckets like "0-39 FG made" and DST
points/yards-allowed buckets like "0-13 pts allowed") are treated as plain,
distinct dict keys here -- `score()` has no bucket logic of its own. Upstream
ingestion (Task 5) and `derive.py` (Task 6) are responsible for already
having bucketed a raw stat into one of these exact `stat_name` strings before
it reaches `score()`.
"""

from __future__ import annotations

import logging
from pathlib import Path

import polars as pl

logger = logging.getLogger(__name__)

GROUP_COLUMNS = ["season", "week", "source", "player_id"]


def load_scoring_rules(path: Path) -> dict[str, float]:
    """Parse `data/scoring_rules.csv` (`Stat,Points`) into `{stat_name: points}`.

    Stat names are used verbatim from the CSV -- they already match the
    canonical `stat_name` vocabulary produced by ingestion (including mixed
    case in bucket labels like "0-39 FG made"), so no case folding is
    applied. Surrounding whitespace is stripped defensively.
    """
    rules_df = pl.read_csv(path)
    stats = rules_df["Stat"].str.strip_chars().to_list()
    points = rules_df["Points"].cast(pl.Float64).to_list()
    return dict(zip(stats, points))


def score(df: pl.DataFrame, rules: dict[str, float]) -> pl.DataFrame:
    """Score a canonical long-schema DataFrame into per-player fantasy points.

    Returns one row per `(season, week, source, player_id)` with a
    `fantasy_points` column equal to `sum(stat_value * rules[stat_name])`
    over that group's rows. Rows whose `stat_name` has no entry in `rules`
    are excluded from the sum (not treated as zero-point contributions to a
    total that includes them) and are logged as a warning rather than
    silently dropped or allowed to crash the computation.
    """
    known_stats = set(rules)
    present_stats = set(df["stat_name"].unique().to_list())
    unmapped = present_stats - known_stats
    if unmapped:
        logger.warning(
            "score(): dropping %d unmapped stat_name(s) not in scoring rules: %s",
            len(unmapped),
            sorted(unmapped),
        )

    scored = df.filter(pl.col("stat_name").is_in(list(known_stats))).with_columns(
        pl.col("stat_name")
        .replace_strict(rules, return_dtype=pl.Float64)
        .alias("points_per_unit")
    )

    return (
        scored.with_columns(
            (pl.col("stat_value") * pl.col("points_per_unit")).alias("fantasy_points")
        )
        .group_by(GROUP_COLUMNS)
        .agg(pl.col("fantasy_points").sum())
        .sort(GROUP_COLUMNS)
    )
