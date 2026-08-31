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

Repeated snapshots
------------------
The raw layer is append-only: `ingest snapshot` writes a new
`snap_<date>.parquet` per run and `ingest actuals` concatenates onto the
season's file. Any consumer that globs those files and sums gets one copy of
every stat *per snapshot run*, so a second snapshot of the same season and
source silently doubles every scored total. `score()` therefore runs
`latest_snapshot_rows()` over its input first: within each
`(season, week, source, player_id, stat_name)` group only the newest
`snapshot_date` survives. Because `score()` is the single scoring path for
projections, actuals, and derived rows alike, doing it here fixes every
consumer at once (`model.blend._wide_projections`,
`model.calibrate._seasonal_totals`, `metrics.consistency.compute_consistency`)
rather than needing three separate, drift-prone implementations.
"""

from __future__ import annotations

import logging
from pathlib import Path

import polars as pl

logger = logging.getLogger(__name__)

GROUP_COLUMNS = ["season", "week", "source", "player_id"]

#: Grouping key for `latest_snapshot_rows`. One stat, for one player, from one
#: source, in one season-week is a single fact -- if the raw layer holds it
#: more than once, that is a repeated ingest run, not extra information.
SNAPSHOT_KEY = ["season", "week", "source", "player_id", "stat_name"]


def latest_snapshot_rows(df: pl.DataFrame) -> pl.DataFrame:
    """Keep only the newest `snapshot_date` per `SNAPSHOT_KEY` group.

    Deduplicates the append-only raw layer (see this module's docstring).
    `snapshot_date` is compared lexicographically, which is correct for both
    the ISO `YYYY-MM-DD` strings and the `datetime.date` values the ingest
    modules write.

    No-ops on a frame without a `snapshot_date` column (e.g. `derive.py`'s
    synthesised DST frame), and on an empty frame. Rows whose
    `snapshot_date` is null are kept only if *every* row in their group is
    null -- a null date next to a real one cannot be established as the
    latest, so it is treated as the older duplicate.
    """
    if df.height == 0 or "snapshot_date" not in df.columns:
        return df
    key = [c for c in SNAPSHOT_KEY if c in df.columns]
    if not key:
        return df
    # `fill_null` on a sentinel that sorts below every real date keeps
    # all-null groups intact while letting a real date win over a null one.
    stamp = pl.col("snapshot_date").cast(pl.String).fill_null("")
    return df.filter(stamp == stamp.max().over(key))


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

    Input rows are first passed through `latest_snapshot_rows()`, so
    re-running an ingest for a season/source cannot double a player's total
    -- see this module's docstring.
    """
    df = latest_snapshot_rows(df)
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
