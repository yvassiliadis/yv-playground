"""CBS Sports seasonal projections source.

**Live-verified.** The real page markup was found by reading the R
`ffanalytics` package's own `scrape_cbs()` source (it has scraped this site
for years) and confirming its URL and selectors live:
`cbssports.com/fantasy/football/stats/<POS>/<season>/restofseason/projections/nonppr/`
returns a real, server-rendered `<table id="TableBase">` with genuine player
rows for QB/RB/WR/TE (K and DST have a real table too but a messier column
layout -- e.g. K's row cells shift when the "Longest Field Goal" value is a
literal em-dash -- and are left out of this task's scope; a future task can
extend `_POSITION_PATH` once that's handled).

**Important, confirmed by direct testing:** the `<season>` and week path
segments are silently ignored by CBS's current site -- `.../QB/2018/1/...`
and `.../QB/2023/restofseason/...` return byte-identical, always-current
data. This source is therefore only useful for the live/current season;
there is no way to pull CBS's historical archives through this endpoint
(confirmed by comparing three season/week combinations and finding them
byte-for-byte identical). Do not build a `CBSSource`-based historical
loader on this URL.

Each header `<th>` cell's text is the short code and the long display name
concatenated with a single space (e.g. `"yds Passing Yards"`) -- splitting
on the first space and looking up the remainder in `_COLUMN_LABELS` gives a
reliable, header-driven column mapping rather than a positional guess that
would silently misalign if CBS ever reorders columns. This mapping (short
internal key, and the exact long-label strings) was read directly from
`ffanalytics:::cbs_columns` in the R package, not guessed.

Player identity comes from `.CellPlayerName--long a`: its `href`
(`/nfl/players/<id>/<slug>/fantasy/`) is CBS's own numeric player ID, which
is exactly `ids.crosswalk.build_crosswalk()`'s `cbs_id` column (confirmed:
Josh Allen's URL id `2181054` equals his `cbs_id` in the crosswalk) -- so
this source joins by native ID like Sleeper/ESPN, not name-matching.
"""

from __future__ import annotations

import datetime as dt
import re

import polars as pl
from bs4 import BeautifulSoup

from ffdraft.ids.crosswalk import build_crosswalk
from ffdraft.sources.base import (
    CANONICAL_COLUMNS,
    make_http_client,
    resolve_player_id_by_native_id,
)

BASE_URL = "https://www.cbssports.com/fantasy/football/stats"

# QB/RB/WR/TE only -- see module docstring for why K/DST are left out.
_POSITION_PATH = {"QB": "QB", "RB": "RB", "WR": "WR", "TE": "TE"}

_PLAYER_ID_RE = re.compile(r"/players/(\d+)/")

# Long display label (as it appears in the header `<th>`, after the short
# code) -> our internal key. Read directly from `ffanalytics:::cbs_columns`.
_COLUMN_LABELS = {
    "Games Played": "games",
    "Pass Attempts": "pass_att",
    "Pass Completions": "pass_comp",
    "Passing Yards": "pass_yds",
    "Passing Yards Per Game": "pass_yds_g",
    "Touchdowns Passes": "pass_td",
    "Interceptions Thrown": "pass_int",
    "Passer Rating": "pass_rate",
    "Rushing Attempts": "rush_att",
    "Rushing Yards": "rush_yds",
    "Average Yards Per Rush": "rush_avg",
    "Rushing Touchdowns": "rush_td",
    "Targets": "rec_tgt",
    "Receptions": "rec",
    "Receiving Yards": "rec_yds",
    "Yards Per Game": "rec_yds_g",
    "Average Yards Per Reception": "rec_avg",
    "Receiving Touchdowns": "rec_td",
    "Fumbles Lost": "fumbles_lost",
    "Fantasy Points": "site_pts",
    "Fantasy Points Per Game": "site_fppg",
}

# Internal key -> canonical stat_name (data/scoring_rules.csv vocabulary).
# Attempts/completions/rate/per-game/targets have no scoring entry and are
# read but not mapped, same convention as fftoday.py/fantasypros.py.
_STAT_MAP = {
    "pass_yds": "passing yard",
    "pass_td": "passing td",
    "pass_int": "pass intercepted",
    "rush_yds": "rushing yard",
    "rush_td": "rushing td",
    "rec": "reception",
    "rec_yds": "receiving yard",
    "rec_td": "receiving td",
    "fumbles_lost": "fumble lost",
}

_EMPTY_SCHEMA = {
    "source_player_id": pl.String,
    "team": pl.String,
    "position": pl.String,
    "player_name_raw": pl.String,
    "stat_name": pl.String,
    "stat_value": pl.Float64,
}


def _header_keys(table) -> list[str | None]:
    """Per-column internal key, `None` for `Player` and any unknown label."""
    head_row = table.select_one("thead > tr.TableBase-headTr")
    keys: list[str | None] = []
    for th in head_row.find_all("th"):
        text = th.get_text(" ", strip=True)
        if text == "Player":
            keys.append(None)
            continue
        _short, _, label = text.partition(" ")
        keys.append(_COLUMN_LABELS.get(label))
    return keys


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
                url = f"{BASE_URL}/{path_segment}/{season}/restofseason/projections/nonppr/"
                response = client.get(url)
                response.raise_for_status()
                frames.append(self._parse_html(response.text, position, season))
        return pl.concat(frames, how="vertical")

    def _parse_html(self, html: str, position: str, season: int) -> pl.DataFrame:
        soup = BeautifulSoup(html, "lxml")
        table = soup.select_one("#TableBase table")

        rows: list[dict[str, object]] = []
        if table is not None:
            keys = _header_keys(table)
            for tr in table.select("tbody > tr"):
                cells = tr.find_all("td")
                if len(cells) != len(keys):
                    continue
                name_link = cells[0].select_one(".CellPlayerName--long a")
                team_span = cells[0].select_one(".CellPlayerName-team")
                if name_link is None:
                    continue
                match = _PLAYER_ID_RE.search(name_link.get("href", ""))
                row: dict[str, object] = {
                    "source_player_id": match.group(1) if match else None,
                    "player_name_raw": name_link.get_text(strip=True),
                    "team": team_span.get_text(strip=True) if team_span else None,
                }
                for key, cell in zip(keys[1:], cells[1:]):
                    if key is not None:
                        row[key] = cell.get_text(strip=True)
                rows.append(row)

        if not rows:
            long = pl.DataFrame(schema=_EMPTY_SCHEMA)
        else:
            df = pl.DataFrame(rows)
            value_vars = [c for c in _STAT_MAP if c in df.columns]
            long = (
                df.select(["source_player_id", "player_name_raw", "team"] + value_vars)
                .with_columns(
                    pl.col(c)
                    .str.replace_all(",", "")
                    .replace("—", None)
                    .cast(pl.Float64, strict=False)
                    for c in value_vars
                )
                .unpivot(
                    index=["source_player_id", "player_name_raw", "team"],
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
        long = resolve_player_id_by_native_id(long, reference, "cbs_id")

        snapshot_date = dt.datetime.now(tz=dt.UTC).date()
        return long.select(
            pl.lit(season).alias("season"),
            pl.lit(0).alias("week"),
            pl.lit(self.name).alias("source"),
            pl.lit(snapshot_date).alias("snapshot_date"),
            pl.col("source_player_id"),
            pl.col("player_id"),
            pl.col("player_name_raw"),
            pl.col("team"),
            pl.col("position"),
            pl.col("stat_name"),
            pl.col("stat_value").cast(pl.Float64),
        ).select(CANONICAL_COLUMNS)
