"""Historical projection archive loaders: Fantasy Football Analytics (FFA)
and fantasyfootballdatapros (ffdp).

Both loaders parse **already-downloaded local files** -- neither function
makes a network call. Actually fetching/scraping the FFA or ffdp sites is
out of scope for this task; the caller is responsible for populating `path`
with whatever files it downloaded ahead of time.

============================================================================
HONESTY NOTE -- archive file formats are UNVERIFIED against real downloaded
files. This environment has no real FFA or ffdp archive samples available,
so both formats below are hand-built fixture formats based on general
knowledge of each project's public output shape, not a real downloaded file
inspected directly (contrast with `ffdraft.sources.fantasypros`, whose
column layout was confirmed against a live page). Treat both as a
reasonable starting guess that the user should verify against a real
downloaded archive before relying on this in production.
============================================================================

FFA (Fantasy Football Analytics / the open-source `ffanalytics` R package,
fantasyfootballanalytics.net) publishes per-source seasonal projection
archives -- one file per (season, source) pair, since its
`scrape_data()`/`projections_table()` pipeline keeps each contributing
source (CBS, ESPN, NFL, FFToday, etc.) as its own row set before any
aggregation step. This loader assumes that on-disk shape carries through to
the downloaded archive: one CSV per `<season>_<source>.csv` file (e.g.
`2020_espn.csv`), with a header of `player, team, position` plus a subset of
per-stat columns named after the package's own stat vocabulary (`pass_att`,
`pass_cmp`, `pass_yds`, `pass_td`, `pass_int`, `rush_att`, `rush_yds`,
`rush_td`, `rec`, `rec_yds`, `rec_td`, `fum_lost`, `sacks`, `def_int`,
`fum_rec`, `force_fum`, `def_td`, `safety`) -- not every file need carry
every column (e.g. a QB-only source file has no `rec` column). `source` in
the canonical output is set to `ffa_<source>` (e.g. `ffa_espn`) so Task 8's
per-source calibration can still distinguish FFA's own re-scrape of a
source from that source's own live feed.

fantasyfootballdatapros (github.com/fantasyfootballdatapros) ships one CSV
per season of realized/projected seasonal stat lines, in a layout modeled
on Pro Football Reference's fantasy stats table (the project's own scripts
scrape PFR). This loader assumes one `<season>.csv` file per season, with
columns `Player, Team, Position` plus PFR-style per-category stat columns
(`PassAtt, PassCmp, PassYds, PassTD, Int, RushAtt, RushYds, RushTD, Rec,
RecYds, RecTD, FumblesLost`). `source` in the canonical output is always
`ffdp` (a single archive, not a per-source aggregation like FFA).

Both formats are name-based (no native player ID column), so both loaders
resolve `player_id` via `ids.crosswalk.resolve_by_name` -- the exact
mechanism `ffdraft.sources.fantasypros` already uses for the same reason.
`resolve_by_name` never raises on a genuine non-match; it returns `None`,
which is carried straight through into the `player_id` column here. Per
`ids.crosswalk`'s own docstring, there is no separate unmatched-report
writer to call -- the established mechanism is that the caller collects
those rows itself (`df.filter(pl.col("player_id").is_null())`); neither
loader here does anything more than that, matching every other source in
this codebase.

Neither archive format is known to carry the date the projection was
captured, so `snapshot_date` is set to September 1st of the season (a
stand-in for "preseason draft-time snapshot", consistent with these being
seasonal, not weekly, projections) -- another assumption to verify once
real files are available. `week` is always `0`.
"""

from __future__ import annotations

import datetime as dt
import re
from pathlib import Path

import polars as pl

from ffdraft.ids.crosswalk import build_crosswalk, resolve_by_name
from ffdraft.ingest.actuals import CANONICAL_COLUMNS

_CANONICAL_SCHEMA: dict[str, pl.DataType] = {
    "season": pl.Int64,
    "week": pl.Int64,
    "source": pl.String,
    "snapshot_date": pl.Date,
    "source_player_id": pl.String,
    "player_id": pl.String,
    "player_name_raw": pl.String,
    "team": pl.String,
    "position": pl.String,
    "stat_name": pl.String,
    "stat_value": pl.Float64,
}

# Canonical stat_name values, matching the scoring vocabulary in
# data/scoring_rules.csv (same target names ffdraft.sources.fantasypros maps
# to). Raw archive columns not present in a given mapping (e.g. pass_att,
# pass_cmp, rush_att, PFR's Int column already covered by pass_int, etc.)
# are read but intentionally not mapped, same as fantasypros's _STAT_MAP.
_FFA_STAT_MAP = {
    "pass_yds": "passing yard",
    "pass_td": "passing td",
    "pass_int": "pass intercepted",
    "rush_yds": "rushing yard",
    "rush_td": "rushing td",
    "rec": "reception",
    "rec_yds": "receiving yard",
    "rec_td": "receiving td",
    "fum_lost": "fumble lost",
    "sacks": "sacks",
    "def_int": "defense interception",
    "fum_rec": "fumble recovery",
    "force_fum": "forced fumble",
    "def_td": "defense td",
    "safety": "safety",
}

_FFDP_STAT_MAP = {
    "PassYds": "passing yard",
    "PassTD": "passing td",
    "Int": "pass intercepted",
    "RushYds": "rushing yard",
    "RushTD": "rushing td",
    "Rec": "reception",
    "RecYds": "receiving yard",
    "RecTD": "receiving td",
    "FumblesLost": "fumble lost",
}

# `<season>_<source>.csv`, e.g. "2020_espn.csv" -- see module docstring.
_FFA_FILENAME_RE = re.compile(r"^(?P<season>\d{4})_(?P<source>[A-Za-z0-9]+)\.csv$")

# `<season>.csv`, e.g. "2020.csv" -- see module docstring.
_FFDP_FILENAME_RE = re.compile(r"^(?P<season>\d{4})\.csv$")


def _empty_canonical() -> pl.DataFrame:
    return pl.DataFrame(schema=_CANONICAL_SCHEMA)


def _add_player_id(df: pl.DataFrame, reference: pl.DataFrame) -> pl.DataFrame:
    """Resolve `player_id` via `ids.crosswalk.resolve_by_name` for every row.

    Same mechanism `ffdraft.sources.fantasypros` uses: unmatched rows get
    `player_id = None` rather than raising, so a caller can collect them via
    `df.filter(pl.col("player_id").is_null())`.
    """
    return df.with_columns(
        pl.struct(["player_name_raw", "position", "team"])
        .map_elements(
            lambda row: resolve_by_name(
                row["player_name_raw"], row["position"], row["team"], reference
            ),
            return_dtype=pl.String,
        )
        .alias("player_id")
    )


def _to_canonical(
    long: pl.DataFrame, season: int, source: str, snapshot_date: dt.date
) -> pl.DataFrame:
    return long.select(
        pl.lit(season).alias("season"),
        pl.lit(0).alias("week"),
        pl.lit(source).alias("source"),
        pl.lit(snapshot_date).alias("snapshot_date"),
        pl.lit(None, dtype=pl.String).alias("source_player_id"),
        pl.col("player_id"),
        pl.col("player_name_raw"),
        pl.col("team"),
        pl.col("position"),
        pl.col("stat_name"),
        pl.col("stat_value").cast(pl.Float64),
    ).select(CANONICAL_COLUMNS)


def _parse_ffa_file(
    raw: pl.DataFrame, season: int, source: str, reference: pl.DataFrame
) -> pl.DataFrame:
    df = raw.rename({"player": "player_name_raw"})
    stat_cols = [c for c in _FFA_STAT_MAP if c in df.columns]

    id_vars = ["player_name_raw", "team", "position"]
    long = (
        df.select(id_vars + stat_cols)
        .unpivot(
            index=id_vars,
            on=stat_cols,
            variable_name="_raw_stat",
            value_name="stat_value",
        )
        .filter(pl.col("stat_value").is_not_null() & (pl.col("stat_value") != 0))
        .with_columns(
            pl.col("_raw_stat")
            .replace_strict(_FFA_STAT_MAP, return_dtype=pl.String)
            .alias("stat_name"),
            pl.col("position").str.to_uppercase().alias("position"),
        )
        .drop("_raw_stat")
    )
    long = _add_player_id(long, reference)
    snapshot_date = dt.date(season, 9, 1)
    return _to_canonical(long, season, f"ffa_{source.lower()}", snapshot_date)


def _parse_ffdp_file(
    raw: pl.DataFrame, season: int, reference: pl.DataFrame
) -> pl.DataFrame:
    df = raw.rename(
        {"Player": "player_name_raw", "Team": "team", "Position": "position"}
    )
    stat_cols = [c for c in _FFDP_STAT_MAP if c in df.columns]

    id_vars = ["player_name_raw", "team", "position"]
    long = (
        df.select(id_vars + stat_cols)
        .unpivot(
            index=id_vars,
            on=stat_cols,
            variable_name="_raw_stat",
            value_name="stat_value",
        )
        .filter(pl.col("stat_value").is_not_null() & (pl.col("stat_value") != 0))
        .with_columns(
            pl.col("_raw_stat")
            .replace_strict(_FFDP_STAT_MAP, return_dtype=pl.String)
            .alias("stat_name"),
            pl.col("position").str.to_uppercase().alias("position"),
        )
        .drop("_raw_stat")
    )
    long = _add_player_id(long, reference)
    snapshot_date = dt.date(season, 9, 1)
    return _to_canonical(long, season, "ffdp", snapshot_date)


def load_ffa_archives(path: Path) -> pl.DataFrame:
    """Parse every `<season>_<source>.csv` file in `path` into canonical rows.

    See the module docstring for the assumed FFA archive format (unverified
    against a real downloaded archive) and for how unmatched names surface
    (`player_id` is `None`, filterable by the caller). Files in `path` that
    don't match the expected filename pattern are skipped rather than
    raising, since a real download directory may contain other files
    (READMEs, checksums, etc.) alongside the CSVs.
    """
    reference = build_crosswalk()
    frames = []
    for file in sorted(path.glob("*.csv")):
        match = _FFA_FILENAME_RE.match(file.name)
        if match is None:
            continue
        season = int(match.group("season"))
        source = match.group("source")
        raw = pl.read_csv(file)
        frames.append(_parse_ffa_file(raw, season, source, reference))

    if not frames:
        return _empty_canonical()
    return pl.concat(frames, how="vertical")


def load_ffdp_archives(path: Path) -> pl.DataFrame:
    """Parse every `<season>.csv` file in `path` into canonical rows.

    See the module docstring for the assumed ffdp archive format (unverified
    against a real downloaded archive) and for how unmatched names surface
    (`player_id` is `None`, filterable by the caller). Files in `path` that
    don't match the expected filename pattern are skipped rather than
    raising.
    """
    reference = build_crosswalk()
    frames = []
    for file in sorted(path.glob("*.csv")):
        match = _FFDP_FILENAME_RE.match(file.name)
        if match is None:
            continue
        season = int(match.group("season"))
        raw = pl.read_csv(file)
        frames.append(_parse_ffdp_file(raw, season, reference))

    if not frames:
        return _empty_canonical()
    return pl.concat(frames, how="vertical")
