"""FFToday seasonal projections source.

**Live-verified.** This task's brief assumed no live network access would be
available in this environment, but an outbound `curl` to
`fftoday.com/rankings/playerproj.php?Season=2026&PosID=<id>` returned a real
200 response with live 2026-season projection data for all four skill
positions checked (`PosID=10` QB, `20` RB, `30` WR, `40` TE) -- these codes
were confirmed by checking each response's actual player rows (e.g.
`PosID=20` returned Jahmyr Gibbs et al with rushing+receiving columns, not
guessed from FFToday's URL naming).

Response shape, confirmed against the live HTML: the page nests a small
table inside a large one; the actual data table is *not* the first `<table>`
`BeautifulSoup.find_all` returns (that's an outer layout table containing
everything, including this one, as a descendant) -- it's identified here as
the table with the most *direct* (non-nested) `<tr>` children. Its rows are
plain `<tr>` (no `id`/`class` on the row itself); data rows are identified
by every cell carrying `class="smallbody"` (the header row's cells don't).
Column layout is positional, not by header text, since the header cells
carry nested sort-link `<a>`/`<img>` markup that makes header text
unreliable to parse (e.g. the player column's header text reads
"PlayerSort First:Last:" once flattened) -- this parser never reads header
text, only cell position, confirmed against the live column order for each
position below. Player name lives inside an `<a href="/stats/players/<id>/...">`
in the second cell; the numeric id in that URL is FFToday's own internal
player id, but it has no column in `ids.crosswalk.build_crosswalk()`'s
reference table (unlike `cbs_id`, there is no `fftoday_id`), so it's read
but not used for matching -- name+position+team via `resolve_by_name` is
used instead, per this task's brief.

Kicker and DST projections are not fetched: FFToday's own PosID scheme for
those (unconfirmed which numeric ids they are) wasn't checked, and -- as
with FantasyPros -- there is no reliable, low-risk way to extend the
existing four-position pattern to them without another live check outside
this task's time budget. This mirrors FantasyPros' precedent of leaving
gaps explicit rather than guessing.
"""

from __future__ import annotations

import datetime as dt

import polars as pl
from bs4 import BeautifulSoup

from ffdraft.ids.crosswalk import build_crosswalk, resolve_by_name
from ffdraft.sources.base import CANONICAL_COLUMNS, make_http_client

BASE_URL = "https://fftoday.com/rankings/playerproj.php"

# PosID -> our position label. Confirmed against live player rows returned
# for each id (see module docstring).
_POSITION_IDS: dict[str, int] = {"QB": 10, "RB": 20, "WR": 30, "TE": 40}

# Per-position column layout *after* the leading Chg(0)/Player(1)/Tm(2)/
# Bye(3) cells and before the trailing FPts cell -- confirmed against each
# position's live header row.
_POSITION_COLUMNS: dict[str, list[str]] = {
    "QB": [
        "cmp",
        "pass_att",
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

# FFToday stat key -> canonical stat_name. Attempts/completions have no
# scoring vocabulary entry and are read but not mapped, same treatment
# FantasyPros gives its own attempt/completion columns.
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


def _find_data_table(soup: BeautifulSoup):
    """Return the table with the most direct `<tr>` children.

    FFToday nests the real data table inside a page-layout table; picking
    the table with the most *direct* rows (rather than the first `<table>`
    tag) reliably lands on the actual player-row table regardless of how
    much layout markup wraps it (verified against the live page -- see
    module docstring).
    """
    tables = soup.find_all("table")
    return max(tables, key=lambda t: len(t.find_all("tr", recursive=False)))


def _parse_number(text: str) -> float:
    return float(text.replace(",", "").strip())


class FFTodaySource:
    name = "fftoday"

    def fetch(self, season: int, week: int = 0) -> pl.DataFrame:
        if week != 0:
            raise NotImplementedError(
                "FFTodaySource only supports seasonal (week=0) projections"
            )
        frames = []
        with make_http_client() as client:
            for position, pos_id in _POSITION_IDS.items():
                response = client.get(
                    BASE_URL, params={"Season": season, "PosID": pos_id}
                )
                response.raise_for_status()
                frames.append(self._parse_html(response.text, position, season))
        return pl.concat(frames, how="vertical")

    def _parse_html(self, html: str, position: str, season: int) -> pl.DataFrame:
        soup = BeautifulSoup(html, "lxml")
        table = _find_data_table(soup)
        stat_cols = _POSITION_COLUMNS[position]

        rows = []
        for tr in table.find_all("tr", recursive=False):
            cells = tr.find_all("td", recursive=False)
            if len(cells) != len(stat_cols) + 5:
                continue
            if not all("smallbody" in (c.get("class") or []) for c in cells):
                continue

            name_cell = cells[1]
            name_link = name_cell.find("a")
            name_raw = (
                name_link.get_text(strip=True)
                if name_link
                else name_cell.get_text(strip=True)
            )
            team = cells[2].get_text(strip=True)

            stat_values = {
                key: _parse_number(cells[4 + i].get_text(strip=True))
                for i, key in enumerate(stat_cols)
            }
            rows.append(
                {
                    "player_name_raw": name_raw,
                    "team": team,
                    **stat_values,
                }
            )

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
