"""CBS Sports seasonal projections source.

**Best-effort synthetic -- NOT verified against live markup.** This task's
brief assumed no live network access would be available in this
environment, and for CBS that assumption held in practice even though the
network itself is reachable: an outbound `curl` to
`cbssports.com/fantasy/football/stats/<POS>/<season>/season/projections/nonppr/`
returns a real 200 with the correct page `<title>` ("2026 Projections
Fantasy Football Stats - QB Points - CBS Sports"), but the response body
has **zero** `<table>` elements and no player names anywhere in the static
HTML (checked directly -- `"Josh"` doesn't appear in the response at all).
CBS Sports' stats pages are a client-side-rendered React app; the actual
projection table is populated by a JS-executed API call this environment's
plain `httpx` GET can't trigger or discover (no embedded JSON blob,
`__NEXT_DATA__`, or inline `<script type="application/json">` payload was
found in the response either).

Absent a live sample to parse, this parser is written against the
conceptual shape of CBS's classic server-rendered fantasy stats table
(before their front-end migrated to the current React app) -- a
`<table>` with one `<tr>` per player, a leading cell holding the player's
name as anchor text with the team abbreviation as trailing text in the same
cell (`"Josh Allen BUF"`-style, split the same way
`ids.crosswalk`-adjacent sources like FantasyPros split theirs), followed by
positional stat columns. This is a **plausible, not confirmed**, structure;
`tests/fixtures/sources/cbs/sample_<pos>.html` is a hand-authored fixture
built to exercise this parser, not a captured real response.

If CBS's current React-rendered page is to be scraped for real, the
prerequisite is finding CBS's underlying JSON API endpoint the React app
calls (visible only via a browser's network tab, not reachable via a plain
HTTP client) -- that's out of scope for what could be determined in this
task.
"""

from __future__ import annotations

import datetime as dt
import re

import polars as pl
from bs4 import BeautifulSoup

from ffdraft.ids.crosswalk import build_crosswalk, resolve_by_name
from ffdraft.sources.base import CANONICAL_COLUMNS, make_http_client

BASE_URL = "https://www.cbssports.com/fantasy/football/stats"

_POSITION_PATH = {"QB": "QB", "RB": "RB", "WR": "WR", "TE": "TE"}

# Player+team cell: "<Name> <TEAM>", a trailing all-caps 2-3 letter code,
# mirroring FantasyPros' combined-cell format (see fantasypros.py).
_TRAILING_TEAM_RE = re.compile(r"^(.*\S)\s+([A-Z]{2,3})$")

# Per-position stat columns, positionally after the leading Player+Team
# cell. UNVERIFIED against a live page -- see module docstring.
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
        "fl",
    ],
    "RB": ["rush_att", "rush_yds", "rush_td", "rec", "rec_yds", "rec_td", "fl"],
    "WR": ["rec", "rec_yds", "rec_td", "rush_att", "rush_yds", "rush_td", "fl"],
    "TE": ["rec", "rec_yds", "rec_td", "fl"],
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
    "fl": "fumble lost",
}


def _split_name_and_team(cell: str) -> tuple[str, str | None]:
    match = _TRAILING_TEAM_RE.match(cell.strip())
    if match:
        return match.group(1), match.group(2)
    return cell.strip(), None


class CBSSource:
    name = "cbs"

    def fetch(self, season: int, week: int = 0) -> pl.DataFrame:
        if week != 0:
            raise NotImplementedError(
                "CBSSource only supports seasonal (week=0) projections"
            )
        frames = []
        with make_http_client() as client:
            for position, path_segment in _POSITION_PATH.items():
                url = f"{BASE_URL}/{path_segment}/{season}/season/projections/nonppr/"
                response = client.get(url)
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
