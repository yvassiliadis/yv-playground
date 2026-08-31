"""NFL.com (fantasy.nfl.com) seasonal projections source.

**Best-effort synthetic -- NOT verified against live markup.** This task's
brief assumed no live network access would be available in this
environment; for NFL.com, that held in effect even though the site itself
is reachable: an outbound `curl` to
`fantasy.nfl.com/research/projections?position=<id>&statCategory=projectedStats&statSeason=<season>&statType=seasonProjectedStats`
returns HTTP 200, but the response is NFL.com's generic "Fantasy News"
landing page (confirmed via its `<title>`), not a projections table -- the
`position`/`statSeason`/`statType` query params this parser sends either no
longer route to the intended report or require an authenticated session
(fantasy.nfl.com's research tools are gated behind league/account context
for most of their filtered views), similar to the FantasyPros CSV-auth gap
noted in that source's own module docstring. No `<table>` and no player
names appear anywhere in the response body.

Absent a live sample to parse, this parser is written against the
conceptual shape ffanalytics' R package documents for NFL.com: a single
results table (historically `id="researchTable"` or similar) with one
`<tr>` per player, player name and team/position packed into the first
cell (`"<Name> <TEAM> - <Pos>"`-style, split apart the same way this
parser's `_split_name_and_team` below does it), followed by positional stat
columns. This is a **plausible, not confirmed**, structure -- treat both the
column layout and the name-cell format as illustrative guesses, not
verified facts.
"""

from __future__ import annotations

import datetime as dt
import re

import polars as pl
from bs4 import BeautifulSoup

from ffdraft.ids.crosswalk import build_crosswalk, resolve_by_name
from ffdraft.sources.base import CANONICAL_COLUMNS, make_http_client

BASE_URL = "https://fantasy.nfl.com/research/projections"

# NFL.com's own numeric position codes, as used by their site's own
# position-filter links historically (1=QB, 2=RB, 3=WR, 4=TE). UNVERIFIED
# against a live response -- see module docstring.
_POSITION_IDS: dict[str, int] = {"QB": 1, "RB": 2, "WR": 3, "TE": 4}

# "<Name> <TEAM> - <Pos>" -- split off the space-separated team code and
# trailing " - Pos" suffix. UNVERIFIED -- see module docstring.
_NAME_TEAM_RE = re.compile(r"^(.*\S)\s+([A-Z]{2,3})\s*-\s*[A-Z]{1,3}$")

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


def _split_name_and_team(cell: str) -> tuple[str, str | None]:
    match = _NAME_TEAM_RE.match(cell.strip())
    if match:
        return match.group(1), match.group(2)
    return cell.strip(), None


class NFLSource:
    name = "nfl"

    def fetch(self, season: int, week: int = 0) -> pl.DataFrame:
        if week != 0:
            raise NotImplementedError(
                "NFLSource only supports seasonal (week=0) projections"
            )
        frames = []
        with make_http_client() as client:
            for position, position_id in _POSITION_IDS.items():
                response = client.get(
                    BASE_URL,
                    params={
                        "position": position_id,
                        "statCategory": "projectedStats",
                        "statSeason": season,
                        "statType": "seasonProjectedStats",
                    },
                )
                response.raise_for_status()
                frames.append(self._parse_html(response.text, position, season))
        return pl.concat(frames, how="vertical")

    def _parse_html(self, html: str, position: str, season: int) -> pl.DataFrame:
        soup = BeautifulSoup(html, "lxml")
        stat_cols = _POSITION_COLUMNS[position]

        rows = []
        for tr in soup.select("table tbody tr"):
            cells = tr.find_all("td")
            if len(cells) != len(stat_cols) + 1:
                continue
            name_raw, team = _split_name_and_team(cells[0].get_text(strip=True))
            stat_values = {
                key: cells[i + 1].get_text(strip=True)
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
