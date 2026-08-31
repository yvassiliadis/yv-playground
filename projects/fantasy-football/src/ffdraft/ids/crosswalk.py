"""Player-ID crosswalk: nflreadpy-backed resolution plus manual overrides.

Two resolution paths feed into the same `player_id` (nflverse's gsis_id,
matching the canonical ID used throughout ffdraft -- see
`ffdraft.ingest.actuals`):

- Sources with native player IDs (Sleeper, ESPN, FantasyPros) join directly
  against `build_crosswalk()`'s output on their own ID column.
- Sources without native IDs (HTML scrapers, historical archives) go through
  `resolve_by_name`, which matches on normalized name + position and
  tiebreaks on team.

`apply_overrides` is the last step in either path: it loads
`data/crosswalk_overrides.csv` and lets a human-maintained row win over
whatever the automatic resolution produced, including fixing a row that
failed to resolve at all.

Unmatched-row handling: `resolve_by_name` never raises on a genuine
non-match -- it returns `None`. This module does not itself write an
unmatched-rows report; the caller (a source's ingestion code) is expected to
collect rows where resolution returned `None` into its own DataFrame (e.g.
`df.filter(pl.col("player_id").is_null())`) and persist that however suits
its pipeline (a `data/derived/crosswalk_unmatched.csv`, a log, etc.). Keeping
`resolve_by_name` a pure function makes it trivial to test and keeps report
formatting a per-caller concern.
"""

from __future__ import annotations

from pathlib import Path

import nflreadpy as nfl
import polars as pl

from ffdraft.ids.normalize import normalize_dst, normalize_name

# every cross-platform ID column load_ff_playerids() exposes besides gsis_id
# (which becomes player_id below). Carried through even though only a few
# sources are wired up so far -- cheap now, expensive to add later once a
# source needs e.g. yahoo_id.
_OTHER_ID_COLUMNS = [
    "mfl_id",
    "sportradar_id",
    "fantasypros_id",
    "pff_id",
    "sleeper_id",
    "nfl_id",
    "espn_id",
    "yahoo_id",
    "fleaflicker_id",
    "cbs_id",
    "pfr_id",
    "cfbref_id",
    "rotowire_id",
    "rotoworld_id",
    "ktc_id",
    "stats_id",
    "stats_global_id",
    "fantasy_data_id",
    "swish_id",
]

REFERENCE_COLUMNS = [
    "player_id",
    *_OTHER_ID_COLUMNS,
    "name",
    "normalized_name",
    "position",
    "team",
]

_DST_POSITIONS = {"DST", "DEF", "D/ST"}


def build_crosswalk() -> pl.DataFrame:
    """Build the reference player-ID crosswalk from nflreadpy.

    Calls `nflreadpy.load_ff_playerids()` -- confirmed as the installed
    package's current function for this (nflreadpy 0.1.x still ships it
    under that exact name, sourced from DynastyProcess.com). Its `gsis_id`
    column is renamed to `player_id` since that's the ID ffdraft uses
    everywhere else (see `ffdraft.ingest.actuals`), and a `normalized_name`
    column is added so this same table can serve as the reference for
    `resolve_by_name`.
    """
    raw = nfl.load_ff_playerids()
    return raw.select(
        pl.col("gsis_id").alias("player_id"),
        *_OTHER_ID_COLUMNS,
        "name",
        pl.col("name")
        .map_elements(normalize_name, return_dtype=pl.String)
        .alias("normalized_name"),
        "position",
        "team",
    )


def resolve_by_name(
    name_raw: str,
    position: str,
    team: str | None,
    reference: pl.DataFrame,
) -> str | None:
    """Resolve a raw name/position/team to a `player_id` via name matching.

    For sources without native player IDs. `reference` must have
    `normalized_name`, `position`, `team`, and `player_id` columns (the
    output of `build_crosswalk`). DST positions skip the reference table
    entirely and resolve straight to a synthetic `DST_<TEAM>` ID via
    `normalize_dst`, matching the synthetic IDs `ingest.actuals` already
    produces for defenses -- nflreadpy's player-ID table has no DST rows. An
    unrecognized DST spelling from a scraped source is a genuine non-match
    like any other, so `normalize_dst`'s `ValueError` is caught here and
    turned into `None` rather than propagating.

    Ties among same-name, same-position players are broken by `team`. The
    comparison is a case-normalized exact match (`team.upper()` against the
    reference's `team` column as-is) -- if a source's team codes don't line
    up with nflreadpy's own (e.g. "JAC" vs "JAX"), this fails closed to
    `None` (unmatched) rather than risk tiebreaking onto the wrong player.

    If `team` doesn't narrow the match down to exactly one row -- because
    it's missing, or because it still doesn't disambiguate -- this returns
    `None` rather than guessing at a specific player. Never raises on a
    genuine non-match.
    """
    if position.upper() in _DST_POSITIONS:
        try:
            team_abbr = normalize_dst(team or name_raw)
        except ValueError:
            return None
        return f"DST_{team_abbr}"

    normalized = normalize_name(name_raw)
    matches = reference.filter(
        (pl.col("normalized_name") == normalized)
        & (pl.col("position") == position.upper())
    )

    if matches.height == 0:
        return None
    if matches.height > 1:
        if team:
            matches = matches.filter(pl.col("team") == team.upper())
        if matches.height != 1:
            return None
    return matches["player_id"].item()


def apply_overrides(df: pl.DataFrame, overrides_path: Path) -> pl.DataFrame:
    """Apply `data/crosswalk_overrides.csv` on top of resolved player IDs.

    `df` must have `source`, `source_player_id`, `player_id`, `player_name_raw`,
    and `position` columns (the canonical schema from `ingest.actuals`).
    Override rows win unconditionally over whatever `player_id` a row already
    has -- including `None` from an unmatched `resolve_by_name` call. A row
    is matched by `source` + `source_player_id` when the override supplies a
    `source_player_id`; otherwise it's matched by normalized name + position.

    Implementation note: id-matched and name-matched overrides are applied
    as two sequential left-joins (id-matched first, then name-matched), each
    coalescing its override column ahead of the running `player_id`. In the
    ordinary case a `df` row is touched by at most one override row, so
    order doesn't matter. If a single `df` row were somehow matched by both
    an id-matched *and* a name-matched override row (e.g. a malformed
    overrides file with contradictory entries for the same player), the
    name-matched pass runs second and wins.
    """
    overrides = pl.read_csv(
        overrides_path,
        schema_overrides={
            "source": pl.String,
            "source_player_id": pl.String,
            "player_id": pl.String,
            "player_name": pl.String,
            "position": pl.String,
            "team": pl.String,
            "note": pl.String,
        },
    )
    if overrides.height == 0:
        return df

    result = df.with_columns(pl.col("position").str.to_uppercase().alias("position"))

    by_id = overrides.filter(
        pl.col("source_player_id").is_not_null() & (pl.col("source_player_id") != "")
    )
    by_name = overrides.filter(
        pl.col("source_player_id").is_null() | (pl.col("source_player_id") == "")
    )

    if by_id.height > 0:
        result = (
            result.join(
                by_id.select("source", "source_player_id", "player_id").rename(
                    {"player_id": "_override_id"}
                ),
                on=["source", "source_player_id"],
                how="left",
            )
            .with_columns(pl.coalesce("_override_id", "player_id").alias("player_id"))
            .drop("_override_id")
        )

    if by_name.height > 0:
        by_name = by_name.with_columns(
            pl.col("player_name")
            .map_elements(normalize_name, return_dtype=pl.String)
            .alias("_normalized_name"),
            pl.col("position").str.to_uppercase().alias("position"),
        )
        result = (
            result.with_columns(
                pl.col("player_name_raw")
                .map_elements(normalize_name, return_dtype=pl.String)
                .alias("_normalized_name")
            )
            .join(
                by_name.select("_normalized_name", "position", "player_id").rename(
                    {"player_id": "_override_id"}
                ),
                on=["_normalized_name", "position"],
                how="left",
            )
            .with_columns(pl.coalesce("_override_id", "player_id").alias("player_id"))
            .drop("_override_id", "_normalized_name")
        )

    return result
