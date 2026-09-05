"""FantasySharks seasonal projections source.

**Live-verified**, with one non-obvious correction to what a naive guess
would produce. This task's brief assumed no live network access would be
available in this environment, but an outbound `curl` to
`fantasysharks.com/apps/bert/forecasts/projections.php?Position=<id>`
returned real 2026-season projection data. The `Position` query-param codes
are **not** the obvious sequential 1/2/3/4 guess: `Position=1` is QB and
`Position=2` is RB as expected, but `Position=3` returns FantasySharks'
generic homepage (no `#toolData` player rows at all -- the param is either
retired or maps to something else entirely), `Position=4` is actually WR
(confirmed by its returned rows -- Ja'Marr Chase, Puka Nacua, Drake London,
receiving-first column order), and `Position=5` is TE (confirmed by its
rows -- Trey McBride, Brock Bowers, George Kittle). `_POSITION_IDS` below
reflects this confirmed mapping, not the naive sequential one.

Response shape: a `<table id="toolData">` with a header `<tr>`, a
"Points Awarded" scoring-key `<tr>` immediately after it (skipped here, not
a player row), then one `<tr>` per player. Player name arrives as
"Last, First" (e.g. "Allen, Josh"), which `_split_last_first_name` below
reverses into FantasyPros/FFToday-style "First Last" so the name normalizer
downstream (`ids.normalize.normalize_name`, used inside
`ids.crosswalk.resolve_by_name`) sees the same shape it does for every other
source. Every position's column layout below (`_POSITION_COLUMNS`) was read
directly off each position's live header row, including the position-
specific placement of the touchdown-distance-bucket columns (e.g. QB's
"0-9 Pass TDs".."50+ Pass TDs"): those bucket columns are read but not
mapped to a `stat_name` -- like FantasyPros' raw attempt/completion counts,
the scoring vocabulary (`data/scoring_rules.csv`) has no distance-bucketed
touchdown stat to map them onto, only the unbucketed total ("Pass TDs" /
"Rsh TDs" / "Rec TDs"), which is what's used here instead.

FantasySharks exposes no native player ID in this table at all (no `<a
href="/players/<id>">`-style link on the name cell, unlike FFToday) --
`ids.crosswalk.build_crosswalk()`'s reference table likewise has no
`fantasysharks_id` column, so name+position+team matching via
`resolve_by_name` is the only available path here, consistent with the
other four sources in this task.

Kicker and DST projections are not fetched, for the same reason as
FFToday/FantasyPros: their `Position` codes weren't checked live and
guessing risks silently mis-mapping a whole position's worth of rows.
"""

from __future__ import annotations

import datetime as dt

import polars as pl
from bs4 import BeautifulSoup

from ffdraft.ids.crosswalk import build_crosswalk, resolve_by_name
from ffdraft.sources.base import CANONICAL_COLUMNS, make_http_client

BASE_URL = "https://www.fantasysharks.com/apps/bert/forecasts/projections.php"

# Position query-param -> our position label. NOT sequential -- see module
# docstring for how this was confirmed (and why 3 is skipped entirely).
_POSITION_IDS: dict[str, int] = {"QB": 1, "RB": 2, "WR": 4, "TE": 5}

# Per-position column layout after the leading #(0)/Player(1)/Tm(2)/Opp(3)
# cells, confirmed against each position's live header row. Distance-bucket
# TD columns are included here (so column-count validation stays accurate)
# but are not in `_STAT_MAP`, so they're read and then dropped.
_POSITION_COLUMNS: dict[str, list[str]] = {
    "QB": [
        "pass_att",
        "pass_cmp",
        "pass_yds",
        "pass_td",
        "pass_td_0_9",
        "pass_td_10_19",
        "pass_td_20_29",
        "pass_td_30_39",
        "pass_td_40_49",
        "pass_td_50p",
        "int",
        "sacks_taken",
        "rush_att",
        "rush_yds",
        "rush_td",
        "fum",
    ],
    "RB": [
        "rush_att",
        "rush_yds",
        "rush_td",
        "rush_td_0_9",
        "rush_td_10_19",
        "rush_td_20_29",
        "rush_td_30_39",
        "rush_td_40_49",
        "rush_td_50p",
        "tgt",
        "rz_tgt",
        "rec",
        "rec_yds",
        "rec_td",
        "gte_50yd",
        "gte_100yd",
        "kick_ret_yds",
        "fum",
    ],
    "WR": [
        "tgt",
        "rz_tgt",
        "rec",
        "rec_yds",
        "rec_td",
        "rec_td_0_9",
        "rec_td_10_19",
        "rec_td_20_29",
        "rec_td_30_39",
        "rec_td_40_49",
        "rec_td_50p",
        "rush_yds",
        "rush_td",
        "kick_ret_yds",
        "fum",
    ],
    "TE": [
        "tgt",
        "rz_tgt",
        "rec",
        "rec_yds",
        "rec_td",
        "rec_td_0_9",
        "rec_td_10_19",
        "rec_td_20_29",
        "rec_td_30_39",
        "rec_td_40_49",
        "rec_td_50p",
        "rush_yds",
        "rush_td",
        "kick_ret_yds",
        "fum",
    ],
}

# FantasySharks stat key -> canonical stat_name. Distance-bucket columns and
# raw attempt/target/completion counts are intentionally excluded -- no
# scoring vocabulary entry to map them onto (see module docstring).
_STAT_MAP = {
    "pass_yds": "passing yard",
    "pass_td": "passing td",
    "int": "pass intercepted",
    "rush_yds": "rushing yard",
    "rush_td": "rushing td",
    "rec": "reception",
    "rec_yds": "receiving yard",
    "rec_td": "receiving td",
    "fum": "fumble lost",
}


def _split_last_first_name(cell: str) -> str:
    """ "Allen, Josh" -> "Josh Allen"; passes through unchanged if there's no comma."""
    if "," not in cell:
        return cell.strip()
    last, _, first = cell.partition(",")
    return f"{first.strip()} {last.strip()}"


def _parse_number(text: str) -> float | None:
    text = text.strip()
    if not text:
        return None
    return float(text.replace(",", ""))


class FantasySharksSource:
    name = "fantasysharks"

    def fetch(self, season: int, week: int = 0) -> pl.DataFrame:
        if week != 0:
            raise NotImplementedError(
                "FantasySharksSource only supports seasonal (week=0) projections"
            )
        frames = []
        with make_http_client() as client:
            for position, position_id in _POSITION_IDS.items():
                response = client.get(BASE_URL, params={"Position": position_id})
                response.raise_for_status()
                frames.append(self._parse_html(response.text, position, season))
        return pl.concat(frames, how="vertical")

    def _parse_html(self, html: str, position: str, season: int) -> pl.DataFrame:
        soup = BeautifulSoup(html, "lxml")
        stat_cols = _POSITION_COLUMNS[position]
        table = soup.find("table", id="toolData")

        rows = []
        if table is not None:
            trs = table.find_all("tr")
            # Skip the header row (index 0) and the "Points Awarded" scoring-
            # key row (index 1); every row after that is a player row.
            for tr in trs[2:]:
                cells = tr.find_all("td", recursive=False)
                # 4 leading cells (#/Player/Tm/Opp) + stat_cols + 2 trailing
                # cells (a second "Opp" points-allowed column, then "Pts").
                if len(cells) != len(stat_cols) + 6:
                    continue
                rank_text = cells[0].get_text(strip=True)
                if not rank_text.isdigit():
                    # A "Tier N" separator row or similar, not a player row.
                    continue

                name_raw = _split_last_first_name(cells[1].get_text(strip=True))
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
