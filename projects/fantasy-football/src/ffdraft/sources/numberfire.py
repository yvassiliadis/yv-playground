"""numberFire seasonal projections source.

**Best-effort synthetic -- NOT verified against live markup.** This task's
brief assumed no live network access would be available in this
environment; for numberFire, that held: an outbound `curl` to
`numberfire.com/nfl/players/projections` returns HTTP 500, and the body it
does return is FanDuel's generic sportsbook error shell ("Aaand... no good!
Sorry, something went wrong.") -- confirmed by checking the response
`<title>` ("FanDuel Sportsbook") and body text directly, not just the
status code. numberFire was acquired by FanDuel some years ago and its
standalone player-projections pages appear to no longer be served in a
reachable form from this environment; there is no live markup to check
against at all, not even a JS-rendered shell with real page metadata (unlike
CBS, which at least returns the correct page).

Absent any live response, this parser is written against the conceptual
shape ffanalytics' R package documents for numberFire: a single HTML table
per position page, one `<tr>` per player, with the player name in an
anchor in the first cell and per-position stat columns following it
positionally -- structurally the same shape as every other source in this
task, since none of these five expose a JSON API. `stat_cols` below is a
plausible guess at column order, unconfirmed.

Given numberFire's current unreachability, this source is the lowest-
confidence of the five in this task -- flagged explicitly in the task
report as well.
"""

from __future__ import annotations

import datetime as dt

import polars as pl
from bs4 import BeautifulSoup

from ffdraft.ids.crosswalk import build_crosswalk, resolve_by_name
from ffdraft.sources.base import CANONICAL_COLUMNS, make_http_client

BASE_URL = "https://www.numberfire.com/nfl/players/projections"

_POSITION_QUERY = {"QB": "qb", "RB": "rb", "WR": "wr", "TE": "te"}

# Per-position stat columns, positionally after the leading player-name
# cell and a team cell. UNVERIFIED against a live page -- see module
# docstring.
_POSITION_COLUMNS: dict[str, list[str]] = {
    "QB": [
        "pass_att",
        "pass_cmp",
        "pass_yds",
        "pass_td",
        "int",
        "rush_att",
        "rush_yds",
        "rush_td",
    ],
    "RB": ["rush_att", "rush_yds", "rush_td", "rec", "rec_yds", "rec_td"],
    "WR": ["rec", "rec_yds", "rec_td", "rush_att", "rush_yds", "rush_td"],
    "TE": ["rec", "rec_yds", "rec_td"],
}

_STAT_MAP = {
    "pass_yds": "passing yard",
    "pass_td": "passing td",
    "int": "pass intercepted",
    "rush_yds": "rushing yard",
    "rush_td": "rushing td",
    "rec": "reception",
    "rec_yds": "receiving yard",
    "rec_td": "receiving td",
}


class NumberFireSource:
    name = "numberfire"

    def fetch(self, season: int, week: int = 0) -> pl.DataFrame:
        if week != 0:
            raise NotImplementedError(
                "NumberFireSource only supports seasonal (week=0) projections"
            )
        frames = []
        with make_http_client() as client:
            for position, query_position in _POSITION_QUERY.items():
                response = client.get(BASE_URL, params={"position": query_position})
                response.raise_for_status()
                frames.append(self._parse_html(response.text, position, season))
        return pl.concat(frames, how="vertical")

    def _parse_html(self, html: str, position: str, season: int) -> pl.DataFrame:
        soup = BeautifulSoup(html, "lxml")
        stat_cols = _POSITION_COLUMNS[position]

        rows = []
        for tr in soup.select("table tbody tr"):
            cells = tr.find_all("td")
            if len(cells) != len(stat_cols) + 2:
                continue
            name_cell, team_cell, *stat_cells = cells
            name_link = name_cell.find("a")
            name_raw = (
                name_link.get_text(strip=True)
                if name_link
                else name_cell.get_text(strip=True)
            )
            team = team_cell.get_text(strip=True)
            stat_values = {
                key: stat_cells[i].get_text(strip=True)
                for i, key in enumerate(stat_cols)
            }
            rows.append({"player_name_raw": name_raw, "team": team, **stat_values})

        if not rows:
            long = pl.DataFrame(
                schema={
                    "player_name_raw": pl.String,
                    "team": pl.String,
                    "stat_name": pl.String,
                    "stat_value": pl.Float64,
                }
            )
        else:
            df = pl.DataFrame(rows)
            value_vars = [c for c in stat_cols if c in _STAT_MAP]
            long = (
                df.select(["player_name_raw", "team"] + value_vars)
                .with_columns(
                    pl.col(c).str.replace_all(",", "").cast(pl.Float64)
                    for c in value_vars
                )
                .unpivot(
                    index=["player_name_raw", "team"],
                    on=value_vars,
                    variable_name="_raw_stat",
                    value_name="stat_value",
                )
                .filter(
                    pl.col("stat_value").is_not_null() & (pl.col("stat_value") != 0)
                )
                .with_columns(
                    pl.col("_raw_stat")
                    .replace_strict(_STAT_MAP, return_dtype=pl.String)
                    .alias("stat_name")
                )
                .drop("_raw_stat")
            )

        long = long.with_columns(pl.lit(position).alias("position"))

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
