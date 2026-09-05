"""FantasyPros seasonal projections source.

============================================================================
RULING: this source deviates from the task's "prefer a native-ID crosswalk
join" requirement. It resolves `player_id` via `ids.crosswalk.resolve_by_name`
(name+position+team matching), NOT via `fantasypros_id`. This is a
deliberate, considered deviation, not an oversight -- see "Why this is out
of scope" below for exactly what was checked and what closing the gap would
require.
============================================================================

Fetches FantasyPros' public per-position CSV export
(`fantasypros.com/nfl/projections/<position>.php?week=draft&export=xls` --
despite the `xls` name, this returns CSV text, a long-standing quirk of that
endpoint) and reshapes it into canonical rows.

**Live-verified finding: the CSV/XLS export requires an authenticated
FantasyPros session.** This task's brief assumed no live network access
would be available, but this environment turned out to have outbound access
after all -- fetching the export URL anonymously (with or without
`week=draft`, the value the site's own canonical link uses instead of
`week=0`) returns the ordinary HTML page, not CSV, meaning the export is
gated on login. `make_http_client()` has no credential-handling story (none
of these three sources need one), so `fetch()` will raise on a non-CSV
response the same way it would on any other unexpected shape -- an
authenticated session is a real prerequisite for this source in production,
not something worked around here.

That HTML page, however, renders the *same underlying report* in a `<table
id="data">`, which was used (read, not scraped into production code) to
verify the report's real column layout and -- more importantly -- a
non-obvious quirk this task's original synthetic guess got wrong: **there is
no separate Team column.** Every skill-position row's first cell is a single
string of the player's name immediately followed by their team abbreviation
("Jahmyr Gibbs DET", "Ja'Marr Chase CIN"); DST rows' first cell is just the
franchise's full name ("Houston Texans") with no code at all. `_parse_csv`
below splits the trailing team code back off with a regex for skill
positions, and resolves a DST row's team via `ids.normalize.normalize_dst`
(already built to parse "Houston Texans"-style full names) via
`ids.crosswalk.resolve_by_name`'s existing DST path.

The per-position stat *column order* (`_POSITION_COLUMNS` below) was
directly confirmed against that live table's header row for all five
positions fetched here (qb/rb/wr/te/dst) -- unlike the Player+Team column,
this part of the original guess was already exactly right. The numeric
values in `tests/fixtures/sources/fantasypros/sample_response_<pos>.csv` are
still hand-built/illustrative (a real anonymous fetch can't produce a
genuine CSV row to sample verbatim, only the HTML table, and the CSV export
itself may format numbers slightly differently, e.g. without thousands
separators) -- treat the *shape* as verified and the specific *numbers* in
the fixtures as illustrative.

Why this is out of scope (native-ID join deviation, in detail)
---------------------------------------------------------------
Unlike Sleeper and ESPN, the CSV/XLS export response itself has no ID
column at all -- confirmed by inspecting the live column layout above,
which is Player+Team followed only by stat columns, nothing else.

FantasyPros' own player ID *does* exist and *is* publicly visible, but only
as a CSS class (`fp-id-<n>`, e.g. `fp-id-17298` for Josh Allen) on the
player-name `<a>` tag in the server-rendered HTML report page -- the same
page that returns HTML instead of CSV for an unauthenticated `export=xls`
request (see above). Extracting it therefore requires parsing that page's
DOM, i.e. genuine HTML scraping. I checked for an alternative: the page's
own JavaScript makes no `fetch()`/XHR call to a JSON projections endpoint to
populate the table (checked the page's inline scripts for `fetch(`, `/api/`,
`.json` references -- the only API calls found are unrelated, e.g.
favorite-links bookmarking) -- the table is server-rendered directly into
the HTML with no JSON API alternative to reach for the ID instead.

This task's own scope statement is explicit that these three sources "have
JSON/CSV APIs, no HTML scraping needed" -- FantasyPros' ID is only reachable
by scraping, which contradicts that scope statement. Rather than silently
add scraping (`beautifulsoup4`/`lxml` are already project dependencies, so
it's technically easy) to a source this task defined as scrape-free, I kept
the CSV-only implementation and its name-based fallback, and am flagging the
gap explicitly here instead.

**What a future task would need to do to close this gap:** add a
`BeautifulSoup`/`lxml` parse step (either replacing the CSV fetch entirely,
or run alongside it keyed by row order/player name) against
`fantasypros.com/nfl/projections/<position>.php?week=draft` (the plain,
unauthenticated HTML page -- no `export=xls`, which just redirects to this
same page anyway without credentials), select each row's `.player-name`
anchor, and pull the trailing digits off its `fp-id-<n>` CSS class via a
regex. That ID is `ids.crosswalk.build_crosswalk()`'s `fantasypros_id`
column, joinable exactly like `sleeper_id`/`espn_id` are here via
`resolve_player_id_by_native_id`. Doing this would also incidentally fix the
CSV-auth problem noted above, since the plain HTML report page requires no
authentication at all -- only the `export=xls` CSV variant does.

Until that happens, `player_id` here comes from
`ids.crosswalk.resolve_by_name` instead, same as any other source without a
usable native ID.

Scope note: kicker ("k") projections are intentionally not fetched here.
FantasyPros' basic CSV export reports raw field-goal/attempt counts with no
distance breakdown, but the scoring vocabulary
(`data/scoring_rules.csv`) prices field goals by distance bucket
("0-39 FG made", "40-49 FG made", "5+ FG made") -- there's no lossless way
to map this export's undifferentiated FG count into those buckets, so
kickers are left out of this source rather than mapped incorrectly.
"""

from __future__ import annotations

import datetime as dt
import io
import re

import polars as pl

from ffdraft.ids.crosswalk import build_crosswalk, resolve_by_name
from ffdraft.sources.base import CANONICAL_COLUMNS, make_http_client

BASE_URL = "https://www.fantasypros.com/nfl/projections"

# Player+team cell for a skill-position row is "<Name> <TEAM>" -- a trailing
# all-caps 2-3 letter token. DST rows have no trailing code at all (see
# module docstring), so a row that doesn't match this pattern is treated as
# a DST/team-name row instead of an error.
_TRAILING_TEAM_RE = re.compile(r"^(.*\S)\s+([A-Z]{2,3})$")

# Per-position stat columns, in the order they appear positionally after the
# leading combined Player+Team column. Confirmed against the live HTML
# report's header row for all five positions (see module docstring).
_POSITION_COLUMNS: dict[str, list[str]] = {
    "qb": [
        "pass_att",
        "pass_cmp",
        "pass_yds",
        "pass_tds",
        "pass_int",
        "rush_att",
        "rush_yds",
        "rush_tds",
        "fl",
    ],
    "rb": ["rush_att", "rush_yds", "rush_tds", "rec", "rec_yds", "rec_tds", "fl"],
    "wr": ["rec", "rec_yds", "rec_tds", "rush_att", "rush_yds", "rush_tds", "fl"],
    "te": ["rec", "rec_yds", "rec_tds", "fl"],
    "dst": ["sacks", "def_int", "fr", "ff", "def_td", "safety", "pa", "yds_agn"],
}

_POSITION_LABELS = {"qb": "QB", "rb": "RB", "wr": "WR", "te": "TE", "dst": "DST"}

# FantasyPros stat code -> canonical stat_name. Only codes with a home in
# the scoring vocabulary are mapped; attempts/completions/FPTS/points-
# allowed-yards-allowed raw counts (pa, yds_agn) aren't scoring inputs on
# their own (DST scoring buckets points/yards allowed, it doesn't score the
# raw count) and are read but not mapped to a stat_name.
_STAT_MAP = {
    "pass_yds": "passing yard",
    "pass_tds": "passing td",
    "pass_int": "pass intercepted",
    "rush_yds": "rushing yard",
    "rush_tds": "rushing td",
    "rec": "reception",
    "rec_yds": "receiving yard",
    "rec_tds": "receiving td",
    "fl": "fumble lost",
    "sacks": "sacks",
    "def_int": "defense interception",
    "fr": "fumble recovery",
    "ff": "forced fumble",
    "def_td": "defense td",
    "safety": "safety",
}


def _split_name_and_team(cell: str) -> tuple[str, str | None]:
    """Split a combined "Name TEAM" cell; DST rows (no trailing code) pass
    the whole cell through as the name with `team=None`."""
    match = _TRAILING_TEAM_RE.match(cell.strip())
    if match:
        return match.group(1), match.group(2)
    return cell.strip(), None


class FantasyProsSource:
    name = "fantasypros"

    def fetch(self, season: int, week: int = 0) -> pl.DataFrame:
        if week != 0:
            raise NotImplementedError(
                "FantasyProsSource only supports seasonal (week=0) projections"
            )
        frames = []
        with make_http_client() as client:
            for position in _POSITION_COLUMNS:
                url = f"{BASE_URL}/{position}.php"
                response = client.get(url, params={"week": "draft", "export": "xls"})
                response.raise_for_status()
                frames.append(self._parse_csv(response.text, position, season))
        return pl.concat(frames, how="vertical")

    def _parse_csv(self, csv_text: str, position: str, season: int) -> pl.DataFrame:
        raw = pl.read_csv(io.StringIO(csv_text))
        stat_cols = _POSITION_COLUMNS[position]

        rename_map = {raw.columns[0]: "player_and_team"}
        rename_map.update(
            {raw.columns[i + 1]: stat_cols[i] for i in range(len(stat_cols))}
        )
        df = raw.rename(rename_map).with_columns(
            pl.col("player_and_team")
            .map_elements(
                lambda cell: _split_name_and_team(cell)[0], return_dtype=pl.String
            )
            .alias("player_name_raw"),
            pl.col("player_and_team")
            .map_elements(
                lambda cell: _split_name_and_team(cell)[1], return_dtype=pl.String
            )
            .alias("team"),
        )

        value_vars = [c for c in stat_cols if c in _STAT_MAP]
        id_vars = ["player_name_raw", "team"]
        long = (
            df.select(id_vars + value_vars)
            .unpivot(
                index=id_vars,
                on=value_vars,
                variable_name="_raw_stat",
                value_name="stat_value",
            )
            .filter(pl.col("stat_value").is_not_null() & (pl.col("stat_value") != 0))
            .with_columns(
                pl.col("_raw_stat")
                .replace_strict(_STAT_MAP, return_dtype=pl.String)
                .alias("stat_name"),
                pl.lit(_POSITION_LABELS[position]).alias("position"),
            )
            .drop("_raw_stat")
        )

        reference = build_crosswalk()
        long = long.with_columns(
            pl.struct(["player_name_raw", "position", "team"])
            .map_elements(
                lambda row: resolve_by_name(
                    row["player_name_raw"], row["position"], row["team"], reference
                ),
                return_dtype=pl.String,
            )
            .alias("player_id")
        )

        snapshot_date = dt.datetime.now(tz=dt.UTC).date()
        return long.select(
            pl.lit(season).alias("season"),
            pl.lit(0).alias("week"),
            pl.lit(self.name).alias("source"),
            pl.lit(snapshot_date).alias("snapshot_date"),
            pl.lit(None, dtype=pl.String).alias("source_player_id"),
            pl.col("player_id"),
            pl.col("player_name_raw"),
            pl.col("team"),
            pl.col("position"),
            pl.col("stat_name"),
            pl.col("stat_value").cast(pl.Float64),
        ).select(CANONICAL_COLUMNS)
