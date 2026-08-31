"""Ingest actual player and DST stats via nflreadpy.

Reshapes nflreadpy's wide weekly stat tables into the project's canonical
long schema:

    season, week, source, snapshot_date, source_player_id, player_id,
    player_name_raw, team, position, stat_name, stat_value

There is no crosswalk module yet (that lands in a later task), so
`player_id` is set directly from whatever identifier nflreadpy natively
provides (its `gsis_id`-based `player_id` for players, and a synthetic
`DST_<team>` id for defenses).

Known gaps, deliberately NOT filled in here (left for Task 6 / derive.py):
  - rushing/receiving 1st downs: nflreadpy does expose
    `rushing_first_downs`/`receiving_first_downs`, but they are deliberately
    NOT mapped. Nothing projects 1st downs, so scoring them in the actuals
    (which are the calibration target) while the historical projections the
    calibrators train on carry no 1st-down rows makes the calibrators absorb
    an implicit 1st-down uplift; adding an explicit derived 1st-down term at
    board time on top of that double-counts. Until that asymmetry is solved,
    1st-down points are simply not scored anywhere in the pipeline. See
    `derive.py`'s `derive_first_downs`, which stays available but unwired.
  - DST granular stats: 3-and-outs, 4th-down stops, tackles for loss, and
    pass defended are not available from nflreadpy's team-level weekly
    stats
  - FG-by-distance scoring buckets ("0-39 FG made", "40-49 FG made",
    "5+ FG made") are not mapped here
  - "2-pt conversion return", "special teams forced fumble", and "special
    teams fumble recovery" (scoring CSV stat names) have no corresponding
    nflreadpy team-stat column and are not mapped here

Known limitation: the points-allowed/yards-allowed bucketing below depends
on a join against `load_schedules` and a self-join against `team_stats` for
the opponent's game. If a game doesn't resolve (e.g. a bye week row, or a
schedule/team-stats mismatch), the joined value is null; such rows are
skipped entirely rather than falling into a bucket, so a missing bucket row
for a team/week means the join didn't resolve -- not that 0 points/yards
were allowed.
"""

from __future__ import annotations

import datetime as dt

import nflreadpy as nfl
import polars as pl

CANONICAL_COLUMNS = [
    "season",
    "week",
    "source",
    "snapshot_date",
    "source_player_id",
    "player_id",
    "player_name_raw",
    "team",
    "position",
    "stat_name",
    "stat_value",
]

# nflreadpy `load_player_stats` wide column -> canonical scoring-CSV stat_name.
_PLAYER_STAT_MAP = {
    "passing_yards": "passing yard",
    "passing_tds": "passing td",
    "passing_2pt_conversions": "passing 2-pt conversion",
    "passing_interceptions": "pass intercepted",
    "rushing_yards": "rushing yard",
    "rushing_tds": "rushing td",
    "rushing_2pt_conversions": "rushing 2-pt conversion",
    "receptions": "reception",
    "receiving_yards": "receiving yard",
    "receiving_tds": "receiving td",
    "receiving_2pt_conversions": "receiving 2-pt conversion",
}

# Fumbles lost can occur on a sack, a rush, or a reception; the scoring CSV
# has a single generic "fumble lost" stat, so these three sources are summed.
_FUMBLE_LOST_COLS = [
    "sack_fumbles_lost",
    "rushing_fumbles_lost",
    "receiving_fumbles_lost",
]

# nflreadpy `load_team_stats` wide column -> canonical scoring-CSV stat_name.
# def_tds, fumble_recovery_tds, and special_teams_tds were checked for
# overlap (rows where more than one is nonzero in the same team-week): that
# does happen, but it reflects a team scoring multiple distinct TDs of
# different types in the same week (e.g. one pick-six and one fumble-return
# TD), not double-counting of a single event -- each column is nflreadpy's
# own independent counter, so all three map to their own canonical stat.
_DST_COUNTING_STAT_MAP = {
    "def_sacks": "sacks",
    "def_interceptions": "defense interception",
    "fumble_recovery_opp": "fumble recovery",
    "def_tds": "defense td",
    "special_teams_tds": "special teams td",
    "fumble_recovery_tds": "fumble recovery td",
}

_PTS_ALLOWED_BUCKETS = [
    (13, "0-13 pts allowed"),
    (20, "14-20 pts allowed"),
    (27, "21-27 pts allowed"),
    (34, "28-34 pts allowed"),
]
_PTS_ALLOWED_OVERFLOW = "35+ pts allowed"

_YDS_ALLOWED_BUCKETS = [
    (349, "0-349 yds allowed"),
    (399, "350-399 yds allowed"),
    (449, "400-449 yds allowed"),
    (499, "450-499 yds allowed"),
    (549, "500-549 yds allowed"),
]
_YDS_ALLOWED_OVERFLOW = "550+ yds allowed"


def _bucket_expr(
    col: pl.Expr, buckets: list[tuple[int, str]], overflow: str
) -> pl.Expr:
    expr = pl.when(col <= buckets[0][0]).then(pl.lit(buckets[0][1]))
    for upper, label in buckets[1:]:
        expr = expr.when(col <= upper).then(pl.lit(label))
    return expr.otherwise(pl.lit(overflow))


def load_weekly_actuals(seasons: list[int]) -> pl.DataFrame:
    """Pull weekly player stats for `seasons` and reshape to canonical long form."""
    raw = nfl.load_player_stats(seasons=seasons, summary_level="week")
    return _reshape_player_stats(raw)


def _reshape_player_stats(raw: pl.DataFrame) -> pl.DataFrame:
    # Only rename columns nflreadpy actually returned. Its schema varies a
    # little by season (and test fixtures carry a subset), and a hard rename
    # of an absent column would crash the whole ingest for one missing stat.
    present_map = {k: v for k, v in _PLAYER_STAT_MAP.items() if k in raw.columns}
    df = raw.with_columns(
        pl.sum_horizontal([c for c in _FUMBLE_LOST_COLS if c in raw.columns]).alias(
            "fumble lost"
        )
    ).rename(present_map)

    value_vars = list(present_map.values()) + ["fumble lost"]
    id_vars = ["season", "week", "player_id", "player_display_name", "team", "position"]

    long = df.select(id_vars + value_vars).unpivot(
        index=id_vars, on=value_vars, variable_name="stat_name", value_name="stat_value"
    )
    long = long.filter(pl.col("stat_value") != 0)

    return long.select(
        pl.col("season"),
        pl.col("week"),
        pl.lit("actuals").alias("source"),
        pl.lit(dt.datetime.now(tz=dt.UTC).date()).alias("snapshot_date"),
        pl.col("player_id").alias("source_player_id"),
        pl.col("player_id"),
        pl.col("player_display_name").alias("player_name_raw"),
        pl.col("team"),
        pl.col("position"),
        pl.col("stat_name"),
        pl.col("stat_value").cast(pl.Float64),
    )


def load_dst_actuals(seasons: list[int]) -> pl.DataFrame:
    """Pull weekly team defensive stats for `seasons` and reshape to canonical long form."""
    team_stats = nfl.load_team_stats(seasons=seasons, summary_level="week")
    schedules = nfl.load_schedules(seasons=seasons)
    return _reshape_dst_stats(team_stats, schedules)


def _reshape_dst_stats(
    team_stats: pl.DataFrame, schedules: pl.DataFrame
) -> pl.DataFrame:
    counting = team_stats.rename(_DST_COUNTING_STAT_MAP)

    counting_value_vars = list(_DST_COUNTING_STAT_MAP.values())
    counting_id_vars = ["season", "week", "team"]
    counting_long = counting.select(counting_id_vars + counting_value_vars).unpivot(
        index=counting_id_vars,
        on=counting_value_vars,
        variable_name="stat_name",
        value_name="stat_value",
    )
    counting_long = counting_long.filter(pl.col("stat_value") != 0).with_columns(
        pl.col("stat_value").cast(pl.Float64)
    )

    # Points/yards allowed aren't columns nflreadpy exposes directly on
    # team_stats (which reports each team's own offensive output), so derive
    # them: yards allowed = the opponent's passing + rushing yards in the
    # same game; points allowed = the opponent's final score from schedules.
    opponent_yards = team_stats.select(
        pl.col("game_id"),
        pl.col("team").alias("opponent_team"),
        (pl.col("passing_yards") + pl.col("rushing_yards")).alias("yards_allowed"),
    )
    game_scores = schedules.select(
        "game_id", "home_team", "home_score", "away_team", "away_score"
    )

    merged = (
        team_stats.select("season", "week", "team", "opponent_team", "game_id")
        .join(opponent_yards, on=["game_id", "opponent_team"], how="left")
        .join(game_scores, on="game_id", how="left")
        .with_columns(
            pl.when(pl.col("team") == pl.col("home_team"))
            .then(pl.col("away_score"))
            .otherwise(pl.col("home_score"))
            .alias("points_allowed")
        )
        .with_columns(
            _bucket_expr(
                pl.col("points_allowed"), _PTS_ALLOWED_BUCKETS, _PTS_ALLOWED_OVERFLOW
            ).alias("pts_bucket"),
            _bucket_expr(
                pl.col("yards_allowed"), _YDS_ALLOWED_BUCKETS, _YDS_ALLOWED_OVERFLOW
            ).alias("yds_bucket"),
        )
    )

    # A null points_allowed/yards_allowed means the opponent-game join didn't
    # resolve (e.g. a bye week, or a schedule/team-stats mismatch) -- skip
    # bucketing entirely for that row rather than letting `_bucket_expr`
    # silently fall through to the overflow bucket.
    bucket_long = pl.concat(
        [
            merged.filter(pl.col("points_allowed").is_not_null())
            .select("season", "week", "team", pl.col("pts_bucket").alias("stat_name"))
            .with_columns(pl.lit(1.0).alias("stat_value")),
            merged.filter(pl.col("yards_allowed").is_not_null())
            .select("season", "week", "team", pl.col("yds_bucket").alias("stat_name"))
            .with_columns(pl.lit(1.0).alias("stat_value")),
        ],
        how="vertical",
    )

    long = pl.concat([counting_long, bucket_long], how="vertical")

    return long.select(
        pl.col("season"),
        pl.col("week"),
        pl.lit("actuals").alias("source"),
        pl.lit(dt.datetime.now(tz=dt.UTC).date()).alias("snapshot_date"),
        ("DST_" + pl.col("team")).alias("source_player_id"),
        ("DST_" + pl.col("team")).alias("player_id"),
        pl.col("team").alias("player_name_raw"),
        pl.col("team"),
        pl.lit("DST").alias("position"),
        pl.col("stat_name"),
        pl.col("stat_value").cast(pl.Float64),
    )
