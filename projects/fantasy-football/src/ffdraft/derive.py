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

Where this module is wired in
-----------------------------
`board.build_board` is the single caller, and currently uses exactly one
entry point:

* `derive_dst_seasonal_ep()` -- called **after** `apply_blend`, because it
  returns fantasy points already, not stat rows: it is an EP-level estimate
  used to fill in DSTs the blend could not produce.

`augment_projections()`/`derive_first_downs()` are **not currently wired in
anywhere**, and `ingest/actuals.py` correspondingly does not map nflreadpy's
`rushing_first_downs`/`receiving_first_downs` columns, so 1st-down points are
not scored at all right now. The reason is an asymmetry in calibration:
`model/calibrate.py` trains on historical *projection* snapshots, none of
which carry 1st-down rows, against a scored-actuals target. If the target
scores 1st downs, the calibrators silently absorb a 1st-down uplift through
correlated features (yards, receptions); adding an explicit derived 1st-down
term at board time on top of that double-counts it. Folding the derivation
into the training frame instead is not a fix either, because the rate would
come from the same `actuals` frame that supplies the target, leaking the
predicted season's own production into its features. Doing this properly
needs a season-aware (`seasons < S` only) rate table on the training side;
until then, the 0.5 pts/1st down is simply left on the table -- a documented
gap, uniform within a position, and therefore near-neutral for the
within-position ranking the board is actually read for. `derive_first_downs`
itself is correct and stays tested, ready to be wired back in.

Every function here takes/returns plain `pl.DataFrame`s in the canonical
long schema (`ingest.actuals.CANONICAL_COLUMNS`) -- no hidden I/O, no
caching -- so `derive_first_downs` in particular can be replayed against
historical projection snapshots during calibration (Task 8), not just
live ingests.
"""

from __future__ import annotations

import logging
from pathlib import Path

import polars as pl

from ffdraft import scoring

logger = logging.getLogger(__name__)

# Typed empty-frame schema for the canonical long format, mirroring the
# `_EMPTY_SCHEMA` convention in `sources/sleeper.py`/`sources/espn.py` --
# used wherever this module needs to return "no rows" while still giving
# callers a correctly-typed (rather than all-Null) DataFrame to concat onto.
_EMPTY_CANONICAL_SCHEMA: dict[str, pl.DataType] = {
    "season": pl.Int64,
    "week": pl.Int64,
    "source": pl.String,
    "snapshot_date": pl.String,
    "source_player_id": pl.String,
    "player_id": pl.String,
    "player_name_raw": pl.String,
    "team": pl.String,
    "position": pl.String,
    "stat_name": pl.String,
    "stat_value": pl.Float64,
}

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
            # A position with zero total historical volume (e.g. a position
            # entirely absent from historical_actuals) has no defined mean
            # rate -- leave it null rather than letting `x / 0` silently
            # become inf/NaN, which would then poison every shrunk_rate for
            # that position downstream.
            pl.when(pl.col("pos_volume") > 0)
            .then(pl.col("pos_first_downs") / pl.col("pos_volume"))
            .otherwise(None)
            .alias("positional_rate")
        )
    )

    zero_volume_positions = positional.filter(pl.col("positional_rate").is_null())[
        "position"
    ].to_list()
    if zero_volume_positions:
        logger.warning(
            "_shrunk_rate_table(): %d position(s) have zero total historical "
            "volume for stat_name=%r and get no positional-mean fallback: %s",
            len(zero_volume_positions),
            volume_stat,
            sorted(zero_volume_positions),
        )

    joined = per_player.join(positional, on="position", how="left")
    return joined.with_columns(
        # Standard case: blend the player's own rate with the positional
        # mean. If the positional mean is undefined (null, from the guard
        # above), fall back to the player's own rate when they have any
        # volume at all, else 0.0 -- logged below so a silent zero doesn't
        # get mistaken for a genuinely observed rate.
        pl.when(pl.col("positional_rate").is_not_null())
        .then(
            (pl.col("first_downs") + prior_volume * pl.col("positional_rate"))
            / (pl.col("volume") + prior_volume)
        )
        .when(pl.col("volume") > 0)
        .then(pl.col("first_downs") / pl.col("volume"))
        .otherwise(0.0)
        .alias("shrunk_rate")
    ).select("player_id", "position", "shrunk_rate", "positional_rate")


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
        return pl.DataFrame(schema=_EMPTY_CANONICAL_SCHEMA)

    joined = (
        volume_rows.join(
            rate_table.select("player_id", "shrunk_rate"), on="player_id", how="left"
        )
        .join(positional_fallback, on="position", how="left")
        .with_columns(pl.coalesce(["shrunk_rate", "positional_rate"]).alias("rate"))
    )

    unresolved = joined.filter(pl.col("rate").is_null())
    if not unresolved.is_empty():
        # A projected player whose position has zero historical volume for
        # this stat (so no positional-mean fallback exists either, per the
        # `_shrunk_rate_table` guard) -- fall back to a rate of 0.0 rather
        # than emitting a null stat_value, and say so explicitly instead of
        # letting the gap pass silently.
        logger.warning(
            "_derive_one_first_down_stat(): %d projection row(s) for "
            "stat_name=%r have no derivable rate (no player history and no "
            "positional-mean fallback); defaulting %r to 0.0",
            unresolved.height,
            volume_stat,
            first_down_stat,
        )
        joined = joined.with_columns(pl.col("rate").fill_null(0.0))

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
        return pl.DataFrame(schema=_EMPTY_CANONICAL_SCHEMA)
    return pl.concat(non_empty, how="vertical")


_FIRST_DOWN_INPUT_COLUMNS = ("stat_name", "stat_value", "player_id", "position")


def augment_projections(
    projections: pl.DataFrame, historical_actuals: pl.DataFrame
) -> pl.DataFrame:
    """`projections` with derived 1st-down rows appended.

    The pipeline entry point for `derive_first_downs`. Currently unused --
    see this module's docstring for the calibration asymmetry that keeps
    1st-down points out of the pipeline for now. Returns `projections` unchanged (rather than raising) when either frame
    is empty or is missing the columns the derivation needs, so callers can
    always route projections through this without pre-checking.
    """
    for frame in (projections, historical_actuals):
        if frame.height == 0 or any(
            c not in frame.columns for c in _FIRST_DOWN_INPUT_COLUMNS
        ):
            logger.info(
                "augment_projections(): no usable 1st-down inputs; "
                "returning projections unchanged"
            )
            return projections

    derived = derive_first_downs(projections, historical_actuals)
    if derived.is_empty():
        return projections
    return pl.concat(
        [projections, derived.select(projections.columns)], how="vertical_relaxed"
    )


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

# The positive-scoring DST stats. The first six are exactly what
# `ingest.actuals._DST_COUNTING_STAT_MAP` writes; the last three are scoring
# CSV stat names no current feed produces but which cost nothing to list (a
# stat with no rows contributes nothing). Leaving these out was a real bug:
# with only `_DST_COUNTING_STATS` (which nothing currently produces either)
# and the allowed-buckets in the derived set, a fallback-derived DST score
# was the *negative half* of a defense's points and nothing else.
_DST_POSITIVE_STATS = [
    "sacks",
    "defense interception",
    "fumble recovery",
    "defense td",
    "special teams td",
    "fumble recovery td",
    "safety",
    "forced fumble",
    "defense blocked kick",
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
# flags (`stat_value == 1.0` on the game-weeks they occurred, and *no row at
# all* otherwise -- `ingest.actuals` writes them sparse, not as explicit
# zeroes) average into a fractional occurrence rate per game, which is why
# the per-game average below has to divide by games played rather than by
# the number of rows present. Fed through `scoring.score()` unchanged,
# `sum(rate * points_per_bucket)` over a team's mutually-exclusive buckets is
# exactly the expected value of the points that bucket category contributes
# per game -- no bucket-specific logic needed, matching `scoring.score()`'s
# "no bucket logic of its own" design.
_DST_DERIVED_STATS = (
    _DST_COUNTING_STATS
    + _DST_POSITIVE_STATS
    + _DST_PTS_ALLOWED_BUCKETS
    + _DST_YDS_ALLOWED_BUCKETS
)

DEFAULT_SCORING_RULES_PATH = (
    Path(__file__).resolve().parents[2] / "data" / "scoring_rules.csv"
)

# Typed empty-frame schema matching `scoring.score()`'s own output shape
# (its GROUP_COLUMNS plus `fantasy_points`), returned when
# `historical_team_stats` has no rows for any DST-derived stat -- guards
# against `per_season["season"].max()` coming back `None` and poisoning the
# decay-exponent/season arithmetic below with a silent crash or null.
_EMPTY_DST_SCORE_SCHEMA: dict[str, pl.DataType] = {
    "season": pl.Int64,
    "week": pl.Int64,
    "source": pl.String,
    "player_id": pl.String,
    "fantasy_points": pl.Float64,
}


def derive_dst_expected_points(
    historical_team_stats: pl.DataFrame,
    decay: float = 0.35,
    scoring_rules_path: Path = DEFAULT_SCORING_RULES_PATH,
    rules: dict[str, float] | None = None,
) -> pl.DataFrame:
    """Decay-weighted historical per-game DST expected fantasy points.

    For each team, computes a per-season per-game average of every DST
    stat_name in `_DST_DERIVED_STATS`, then combines seasons into a single
    decay-weighted average:

        weight(season) = decay ** (most_recent_season - season)
        weighted_avg   = sum(weight * season_avg) / sum(weight)

    i.e. the most recent season gets weight 1, the season before that gets
    weight `decay`, two seasons back gets `decay**2`, and so on -- a
    standard exponential recency decay indexed by season, since
    `historical_team_stats` spans full seasons of weekly rows rather than a
    single ordered game sequence.

    Per-game denominator
    --------------------
    The per-season average is `sum(stat_value) / games_played`, NOT the mean
    over the rows that happen to be present. `ingest.actuals` writes the
    points/yards-allowed buckets *sparsely* -- `stat_value = 1.0` in the
    weeks a bucket applied and no row at all in the weeks it didn't -- and
    filters zero-valued counting stats out entirely, so a mean over present
    rows answers "how big was this stat in the games where it happened",
    which for a bucket flag is always exactly 1.0. That charged every team
    as if every bucket it ever landed in applied to every game.

    `games_played` is the number of distinct `week` values that team has any
    row for in that season, counted over the whole input frame (not just the
    `_DST_DERIVED_STATS` subset). That is the cleanest correct denominator
    available at this call site: the input is `ingest.actuals`' own DST
    output, which emits a points-allowed and a yards-allowed bucket row for
    every game whose schedule join resolved, so "weeks with any row" is
    "games played" in practice. It undercounts only for a game where the
    schedule join failed *and* the team recorded no counting stat at all --
    the same games `ingest.actuals` documents as unbucketable -- which
    leaves the average computed over the games actually observed rather than
    silently inflating it. Nothing else in scope (no schedule frame reaches
    this function) gives a better count without new plumbing.

    With the default `decay=0.35`, the most
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
    if "week" not in historical_team_stats.columns:
        # `week` is the denominator source (see the "Per-game denominator"
        # note in this function's docstring); without it there is no honest
        # per-game average to compute.
        logger.warning(
            "derive_dst_expected_points(): historical_team_stats has no "
            "'week' column, so games played per team-season cannot be "
            "counted; returning an empty result."
        )
        return pl.DataFrame(schema=_EMPTY_DST_SCORE_SCHEMA)

    relevant = historical_team_stats.filter(
        pl.col("stat_name").is_in(_DST_DERIVED_STATS)
    )

    # Games played per team-season: the number of distinct weeks that team
    # has *any* row for, counted over the whole input frame rather than over
    # `relevant`, so a quiet game still counts toward the denominator.
    games = historical_team_stats.group_by(["team", "season"]).agg(
        pl.col("week").n_unique().alias("games")
    )

    per_season = (
        relevant.group_by(["team", "season", "stat_name"])
        .agg(pl.col("stat_value").sum().alias("stat_total"))
        .join(games, on=["team", "season"], how="left")
        .with_columns((pl.col("stat_total") / pl.col("games")).alias("season_avg"))
    )

    if per_season.is_empty():
        # No rows in historical_team_stats matched any DST-derived
        # stat_name -- `.max()` on an empty column returns None, which
        # would otherwise blow up (or silently null out) the decay-exponent
        # and season arithmetic below. Return an empty, correctly-typed
        # result and say why, rather than crashing uninformatively or
        # propagating nulls.
        logger.warning(
            "derive_dst_expected_points(): historical_team_stats has no "
            "rows matching any DST-derived stat_name (%s); returning an "
            "empty result.",
            sorted(_DST_DERIVED_STATS),
        )
        return pl.DataFrame(schema=_EMPTY_DST_SCORE_SCHEMA)

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

    # `rules` lets a caller that already holds the league's scoring dict
    # (e.g. `board.build_board`) pass it straight through, so the board and
    # this derivation can never end up scored by two different rule sets.
    if rules is None:
        rules = scoring.load_scoring_rules(scoring_rules_path)
    return scoring.score(canonical, rules)


#: Regular-season games per team. Used to turn `derive_dst_expected_points`'s
#: per-average-game number into the seasonal EP the board works in.
GAMES_PER_SEASON = 17

_EMPTY_DST_EP_SCHEMA: dict[str, pl.DataType] = {
    "player_id": pl.String,
    "position": pl.String,
    "EP": pl.Float64,
}


def derive_dst_seasonal_ep(
    historical_team_stats: pl.DataFrame,
    decay: float = 0.35,
    scoring_rules_path: Path = DEFAULT_SCORING_RULES_PATH,
    rules: dict[str, float] | None = None,
    games: int = GAMES_PER_SEASON,
) -> pl.DataFrame:
    """`(player_id, position, EP)` seasonal DST expected points.

    The pipeline entry point for `derive_dst_expected_points`: multiplies its
    per-average-game number by `games` so the result is directly comparable
    to `blend.apply_blend`'s seasonal `EP`. `board.build_board` uses it to
    fill in defenses the blend could not produce -- FantasyPros is the only
    source that projects DST at all, and it is the source most likely to fail
    a live snapshot, so without this a failed FantasyPros fetch means a board
    with no defenses on it.
    """
    if historical_team_stats.height == 0 or any(
        c not in historical_team_stats.columns
        for c in ("team", "season", "week", "stat_name", "stat_value")
    ):
        return pl.DataFrame(schema=_EMPTY_DST_EP_SCHEMA)

    per_game = derive_dst_expected_points(
        historical_team_stats,
        decay=decay,
        scoring_rules_path=scoring_rules_path,
        rules=rules,
    )
    if per_game.is_empty():
        return pl.DataFrame(schema=_EMPTY_DST_EP_SCHEMA)

    return per_game.select(
        pl.col("player_id"),
        pl.lit("DST").alias("position"),
        (pl.col("fantasy_points") * games).alias("EP"),
    ).sort("player_id")


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
