"""Fill in scoring-relevant stats public projection sources don't provide.

`ingest.actuals` and the source modules under `ffdraft.sources` are explicit
about three gaps in the canonical stat vocabulary they can produce:

  - rushing/receiving 1st downs (a play-by-play concept no weekly-stats
    source, live or historical, exposes)
  - DST granular stats (3-and-outs, 4th-down stops, tackles for loss, pass
    defended) -- not present in nflreadpy's team-level weekly stats
  - FG-by-distance buckets ("0-39 FG made", "40-49 FG made", "5+ FG made")
    -- sources report only an undifferentiated FG-made count, if they report
    kickers at all (FantasyPros drops kickers entirely for this reason; see
    `sources/fantasypros.py`)

This module derives each of those three stat families so the rest of the
pipeline (`scoring.score()`, calibration) never has to special-case them.
Every function here takes/returns plain `pl.DataFrame`s in the canonical
long schema (`ingest.actuals.CANONICAL_COLUMNS`) -- no hidden I/O, no
caching -- so `derive_first_downs` in particular can be replayed against
historical projection snapshots during calibration (Task 8), not just
live ingests.
"""

from __future__ import annotations

from pathlib import Path

import polars as pl

from ffdraft import scoring
from ffdraft.ingest.actuals import CANONICAL_COLUMNS

# ---------------------------------------------------------------------------
# 1st downs
# ---------------------------------------------------------------------------

# Neither `ingest.actuals` nor any source in `ffdraft.sources` puts "carries"
# (rush attempts) into the canonical stat vocabulary -- rush attempts are
# read from raw source payloads but never mapped to a canonical stat_name,
# since the scoring CSV doesn't reward them directly. "reception" *is* a
# canonical stat_name (it's directly scored), so the receiving side uses a
# genuine per-reception rate. The rushing side substitutes "rushing yard" as
# its volume unit instead of carries: the shrinkage math below is unit-
# agnostic (rate = 1st downs / volume, then rate * projected volume), so
# this substitution only changes what "rate" means (1st downs per yard
# instead of per carry), not the correctness of the estimate.
_RUSH_VOLUME_STAT = "rushing yard"
_RUSH_FIRST_DOWN_STAT = "rushing 1st down"
_REC_VOLUME_STAT = "reception"
_REC_FIRST_DOWN_STAT = "receiving 1st down"

# Empirical-Bayes shrinkage prior weight, expressed in units of the volume
# stat (yards for rushing, receptions for receiving). This is the number of
# "prior" volume-units worth of trust placed in the positional mean before
# a player's own observed rate dominates -- i.e. shrunk_rate is a volume-
# weighted average of the player's own rate and the positional mean, where
# the positional mean gets a fixed vote of `prior` units and the player's
# own rate gets a vote of however many units they've actually accumulated.
# Chosen as a rough fraction of a full season's typical volume for a
# rate-bearing player at that position, so a player with a full season of
# history is trusted mostly on their own rate, while a rookie or a player
# with a handful of games is pulled hard toward the positional mean:
#   - rushing: ~300 yards is a below-average full season for a rotational
#     back, so a player with less history than that is shrunk more than
#     half of the way to the mean.
#   - receiving: ~40 receptions is a below-average full season for a
#     rotational receiver, same reasoning.
_RUSH_SHRINKAGE_PRIOR_VOLUME = 300.0
_REC_SHRINKAGE_PRIOR_VOLUME = 40.0


def _shrunk_rate_table(
    historical_actuals: pl.DataFrame,
    volume_stat: str,
    first_down_stat: str,
    prior_volume: float,
) -> pl.DataFrame:
    """Per-player shrunk 1st-down rate, plus per-position fallback rate.

    Returns columns: `player_id`, `position`, `shrunk_rate`. A second table
    of per-position mean rates (used as the estimate for players with zero
    matching history) is joined in and folded into the same `shrunk_rate`
    column, since a player with zero historical volume shrinks all the way
    to the positional mean by the formula below anyway (numerator and
    denominator both reduce to the prior term).
    """
    volume = (
        historical_actuals.filter(pl.col("stat_name") == volume_stat)
        .group_by("player_id")
        .agg(
            pl.col("stat_value").sum().alias("volume"),
            pl.col("position").first().alias("position"),
        )
    )
    first_downs = (
        historical_actuals.filter(pl.col("stat_name") == first_down_stat)
        .group_by("player_id")
        .agg(pl.col("stat_value").sum().alias("first_downs"))
    )

    per_player = volume.join(first_downs, on="player_id", how="left").with_columns(
        pl.col("first_downs").fill_null(0.0)
    )

    positional = (
        per_player.group_by("position")
        .agg(
            pl.col("volume").sum().alias("pos_volume"),
            pl.col("first_downs").sum().alias("pos_first_downs"),
        )
        .with_columns(
            (pl.col("pos_first_downs") / pl.col("pos_volume")).alias("positional_rate")
        )
    )

    return (
        per_player.join(positional, on="position", how="left")
        .with_columns(
            (
                (pl.col("first_downs") + prior_volume * pl.col("positional_rate"))
                / (pl.col("volume") + prior_volume)
            ).alias("shrunk_rate")
        )
        .select("player_id", "position", "shrunk_rate", "positional_rate")
    )


def _derive_one_first_down_stat(
    projections: pl.DataFrame,
    historical_actuals: pl.DataFrame,
    volume_stat: str,
    first_down_stat: str,
    prior_volume: float,
) -> pl.DataFrame:
    rate_table = _shrunk_rate_table(
        historical_actuals, volume_stat, first_down_stat, prior_volume
    )
    # Positional-mean fallback for a player with no rows at all in
    # historical_actuals (a rookie, or a player missing from the crosswalk
    # in the historical window): join by the projection row's own position
    # rather than dropping the player.
    positional_fallback = rate_table.select("position", "positional_rate").unique(
        subset=["position"]
    )

    volume_rows = projections.filter(pl.col("stat_name") == volume_stat)
    if volume_rows.is_empty():
        return pl.DataFrame(schema=CANONICAL_COLUMNS)

    joined = (
        volume_rows.join(
            rate_table.select("player_id", "shrunk_rate"), on="player_id", how="left"
        )
        .join(positional_fallback, on="position", how="left")
        .with_columns(pl.coalesce(["shrunk_rate", "positional_rate"]).alias("rate"))
    )

    return joined.select(
        pl.col("season"),
        pl.col("week"),
        pl.col("source"),
        pl.col("snapshot_date"),
        pl.col("source_player_id"),
        pl.col("player_id"),
        pl.col("player_name_raw"),
        pl.col("team"),
        pl.col("position"),
        pl.lit(first_down_stat).alias("stat_name"),
        (pl.col("rate") * pl.col("stat_value")).alias("stat_value"),
    )


def derive_first_downs(
    projections: pl.DataFrame, historical_actuals: pl.DataFrame
) -> pl.DataFrame:
    """Derive `"rushing 1st down"` / `"receiving 1st down"` projection rows.

    For each player with a projected `"rushing yard"` and/or `"reception"`
    row, estimates a per-volume-unit 1st-down rate from `historical_actuals`
    (typically 2-3 seasons), shrunk toward the positional mean via the
    empirical-Bayes formula in `_shrunk_rate_table`, then multiplies that
    rate by the player's projected volume to produce a new canonical-schema
    row. Only new rows are returned -- callers `pl.concat` this onto their
    projections DataFrame.

    Takes both DataFrames as plain arguments (no I/O, no baked-in season),
    so this is equally usable against a fresh live snapshot or against a
    historical projection snapshot during calibration (Task 8).
    """
    rushing = _derive_one_first_down_stat(
        projections,
        historical_actuals,
        _RUSH_VOLUME_STAT,
        _RUSH_FIRST_DOWN_STAT,
        _RUSH_SHRINKAGE_PRIOR_VOLUME,
    )
    receiving = _derive_one_first_down_stat(
        projections,
        historical_actuals,
        _REC_VOLUME_STAT,
        _REC_FIRST_DOWN_STAT,
        _REC_SHRINKAGE_PRIOR_VOLUME,
    )
    non_empty = [df for df in (rushing, receiving) if not df.is_empty()]
    if not non_empty:
        return pl.DataFrame(schema=CANONICAL_COLUMNS)
    return pl.concat(non_empty, how="vertical")


# ---------------------------------------------------------------------------
# DST expected points
# ---------------------------------------------------------------------------

# The granular DST stats the scoring CSV rewards that `ingest.actuals`
# explicitly does NOT produce (see its module docstring): 3-and-outs,
# 4th-down stops, tackles for loss, and pass defended. `historical_team_stats`
# is expected to already carry these as canonical rows (season, week, team,
# stat_name, stat_value) sourced from wherever that granular history comes
# from (e.g. play-by-play aggregation) -- building that source feed is out of
# this task's scope; this function only consumes it.
_DST_COUNTING_STATS = [
    "defense 3 and out",
    "defense 4th down stop",
    "tackle for loss",
    "pass defended",
]

_DST_PTS_ALLOWED_BUCKETS = [
    "0-13 pts allowed",
    "14-20 pts allowed",
    "21-27 pts allowed",
    "28-34 pts allowed",
    "35+ pts allowed",
]

_DST_YDS_ALLOWED_BUCKETS = [
    "0-349 yds allowed",
    "350-399 yds allowed",
    "400-449 yds allowed",
    "450-499 yds allowed",
    "500-549 yds allowed",
    "550+ yds allowed",
]

# All DST stats this function derives an expected per-game value for. Bucket
# flags (`stat_value == 1.0` on the one game-week they occurred, absent
# otherwise) decay-average into a fractional occurrence rate per game; fed
# through `scoring.score()` unchanged, `sum(rate * points_per_bucket)` over
# a team's mutually-exclusive buckets is exactly the expected value of the
# points that bucket category contributes per game -- no bucket-specific
# logic needed, matching `scoring.score()`'s "no bucket logic of its own"
# design.
_DST_DERIVED_STATS = (
    _DST_COUNTING_STATS + _DST_PTS_ALLOWED_BUCKETS + _DST_YDS_ALLOWED_BUCKETS
)

DEFAULT_SCORING_RULES_PATH = (
    Path(__file__).resolve().parents[2] / "data" / "scoring_rules.csv"
)


def derive_dst_expected_points(
    historical_team_stats: pl.DataFrame,
    decay: float = 0.35,
    scoring_rules_path: Path = DEFAULT_SCORING_RULES_PATH,
) -> pl.DataFrame:
    """Decay-weighted historical per-game DST expected fantasy points.

    For each team, computes a per-season per-game average of every granular
    DST stat_name in `_DST_DERIVED_STATS`, then combines seasons into a
    single decay-weighted average:

        weight(season) = decay ** (most_recent_season - season)
        weighted_avg   = sum(weight * season_avg) / sum(weight)

    i.e. the most recent season gets weight 1, the season before that gets
    weight `decay`, two seasons back gets `decay**2`, and so on -- a
    standard exponential recency decay indexed by season, since
    `historical_team_stats` spans full seasons of weekly rows rather than a
    single ordered game sequence. With the default `decay=0.35`, the most
    recent season dominates (weight 1 vs. ~0.35 for the prior season and
    ~0.12 two seasons back), reflecting that team defensive personnel and
    scheme turn over enough year to year that older seasons are a weak
    signal.

    The resulting per-team, per-stat decay-weighted averages are themselves
    canonical-schema rows (one row per team per stat, `week=0` to denote a
    per-game expected-value estimate rather than a specific game), which are
    then scored via `scoring.score()` -- the same shared scoring function
    used everywhere else, per this module's docstring. The returned
    DataFrame is `scoring.score()`'s own output shape: one row per team with
    a `fantasy_points` column giving the team's expected DST fantasy points
    for a single average game.

    Blending with a source's own DST projections (where available) is left
    as a documented TODO: DST is called out as "low-stakes" in the project
    spec, and no source currently supplies granular-enough DST projections
    to blend against reliably (see `sources/fantasypros.py`,
    `sources/sleeper.py`).
    """
    relevant = historical_team_stats.filter(
        pl.col("stat_name").is_in(_DST_DERIVED_STATS)
    )

    per_season = relevant.group_by(["team", "season", "stat_name"]).agg(
        pl.col("stat_value").mean().alias("season_avg")
    )

    most_recent_season = per_season["season"].max()
    per_season = per_season.with_columns(
        (decay ** (most_recent_season - pl.col("season"))).alias("weight")
    )

    weighted = (
        per_season.group_by(["team", "stat_name"])
        .agg(
            (pl.col("season_avg") * pl.col("weight")).sum().alias("_num"),
            pl.col("weight").sum().alias("_den"),
        )
        .with_columns((pl.col("_num") / pl.col("_den")).alias("stat_value"))
    )

    canonical = weighted.select(
        pl.lit(most_recent_season + 1).alias("season"),
        pl.lit(0).alias("week"),
        pl.lit("derived_dst").alias("source"),
        ("DST_" + pl.col("team")).alias("player_id"),
        pl.col("stat_name"),
        pl.col("stat_value"),
    )

    rules = scoring.load_scoring_rules(scoring_rules_path)
    return scoring.score(canonical, rules)


# ---------------------------------------------------------------------------
# Kicker FG-by-distance buckets
# ---------------------------------------------------------------------------

_FG_BUCKET_STATS = {"0-39 FG made", "40-49 FG made", "5+ FG made"}

# The canonical stat_name a source would use to report an undifferentiated
# FG-made total, since no current source in `ffdraft.sources` actually
# produces one (FantasyPros drops kickers rather than report this
# ambiguously; Sleeper's projection payload has no FG field mapped at all).
# This is a forward-looking assumption documented here so a future source
# that does add kicker support has an agreed name to emit.
TOTAL_FG_STAT_NAME = "FG made"

# Default league-average FG-by-distance split. Not pulled from a live data
# source (no network access in this environment to verify current-season
# numbers) -- this is a documented approximation of the shape recent NFL
# seasons' FG attempts have taken: the large majority of attempts are inside
# 40 yards, a substantial minority are 40-49, and 50+ attempts are a real
# but clearly smaller share (kickers have gotten better and more willing to
# attempt long field goals in recent years, but they remain the least common
# bucket). Callers with access to real historical splits (e.g. computed from
# nflreadpy play-by-play `kick_distance`) should pass their own dict instead
# of relying on this default.
DEFAULT_LEAGUE_FG_DISTANCE_DIST: dict[str, float] = {
    "0-39 FG made": 0.65,
    "40-49 FG made": 0.25,
    "5+ FG made": 0.10,
}


def derive_kicker_fg_buckets(
    projections: pl.DataFrame, league_fg_distance_dist: dict[str, float]
) -> pl.DataFrame:
    """Split undifferentiated total-FG projections into distance buckets.

    Rows already using one of the three bucket stat_names
    (`_FG_BUCKET_STATS`) pass through unchanged -- the source already did
    the work. Rows with `stat_name == TOTAL_FG_STAT_NAME` (a source
    reporting only a total FG count) are replaced by three new rows, one
    per bucket in `league_fg_distance_dist`, each with
    `stat_value = total * proportion`; the three `stat_value`s sum back to
    the original total by construction. Every other row in `projections`
    (non-kicker stats, PAT made, FG missed, etc.) is passed through
    unchanged.
    """
    already_bucketed = projections.filter(pl.col("stat_name").is_in(_FG_BUCKET_STATS))
    total_fg_rows = projections.filter(pl.col("stat_name") == TOTAL_FG_STAT_NAME)
    other_rows = projections.filter(
        ~pl.col("stat_name").is_in(_FG_BUCKET_STATS | {TOTAL_FG_STAT_NAME})
    )

    if total_fg_rows.is_empty():
        return projections

    split_rows = pl.concat(
        [
            total_fg_rows.with_columns(
                pl.lit(bucket_name).alias("stat_name"),
                (pl.col("stat_value") * proportion).alias("stat_value"),
            )
            for bucket_name, proportion in league_fg_distance_dist.items()
        ],
        how="vertical",
    )

    return pl.concat([other_rows, already_bucketed, split_rows], how="vertical").select(
        projections.columns
    )
