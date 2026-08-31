"""Sleeper seasonal projections source.

Host: `api.sleeper.app`, not `api.sleeper.com` (the plan.md text this task
was drafted against). Sleeper's own developer docs
(https://docs.sleeper.com) serve every documented endpoint -- players,
leagues, drafts, state -- from `api.sleeper.app`; `.com` is only ever the
docs/marketing site. The projections endpoint used here isn't in that
documented set (Sleeper has never published it), but it's been consistently
reverse-engineered by the fantasy-football developer community as living on
the same `api.sleeper.app` host as everything else Sleeper does publish, so
that's what's used here.

Response shape: **verified against a live response.** Despite this task's
brief stating live network access would not be available, this environment
turned out to have outbound network access after all, so
`tests/fixtures/sources/sleeper/sample_response.json` is a trimmed set of
*real* entries captured from
`https://api.sleeper.app/projections/nfl/2026?season_type=regular` (a QB, an
RB, a WR, plus one fabricated "unrostered" entry appended to exercise the
unmatched-native-ID path) -- not a guessed/synthetic shape. The real
response confirmed: a JSON array of per-player objects, each with a
top-level `player_id` (Sleeper's own numeric-string ID -- this is what
`ids.crosswalk.build_crosswalk()` exposes as `sleeper_id`), a top-level
`team` (frequently `null` even for rostered players -- Sleeper's own
`player.team` field is the more reliable fallback, which this parser also
checks), a nested `player` object carrying `first_name`/`last_name`/
`position`/`team`, and a `stats` dict of per-stat seasonal projections keyed
by Sleeper's short stat codes (`pass_yd`, `rush_td`, `rec`, etc, alongside a
lot of ADP/percentile fields this parser ignores). `_STAT_MAP`'s keys were
checked directly against real entries and are confirmed present verbatim.
"""

from __future__ import annotations

import datetime as dt

import polars as pl

from ffdraft.ids.crosswalk import build_crosswalk
from ffdraft.sources.base import (
    CANONICAL_COLUMNS,
    make_http_client,
    resolve_player_id_by_native_id,
)

BASE_URL = "https://api.sleeper.app"

# Sleeper stat code -> canonical stat_name. Kept aligned with
# `ingest.actuals._PLAYER_STAT_MAP` so a projection and an actual for the
# same underlying event score identically in `scoring.score`.
_STAT_MAP = {
    "pass_yd": "passing yard",
    "pass_td": "passing td",
    "pass_2pt": "passing 2-pt conversion",
    "pass_int": "pass intercepted",
    "rush_yd": "rushing yard",
    "rush_td": "rushing td",
    "rush_2pt": "rushing 2-pt conversion",
    "rec": "reception",
    "rec_yd": "receiving yard",
    "rec_td": "receiving td",
    "rec_2pt": "receiving 2-pt conversion",
    "fum_lost": "fumble lost",
}

_EMPTY_SCHEMA = {
    "source_player_id": pl.String,
    "team": pl.String,
    "position": pl.String,
    "player_name_raw": pl.String,
    "stat_name": pl.String,
    "stat_value": pl.Float64,
}


class SleeperSource:
    name = "sleeper"

    def fetch(self, season: int, week: int = 0) -> pl.DataFrame:
        if week != 0:
            raise NotImplementedError(
                "SleeperSource only supports seasonal (week=0) projections"
            )
        url = f"{BASE_URL}/projections/nfl/{season}?season_type=regular"
        with make_http_client() as client:
            response = client.get(url)
            response.raise_for_status()
            payload = response.json()
        return self._parse(payload, season)

    def _parse(self, payload: list[dict], season: int) -> pl.DataFrame:
        rows = self._rows_from_payload(payload)
        long = (
            pl.DataFrame(rows, schema=_EMPTY_SCHEMA)
            if rows
            else pl.DataFrame(schema=_EMPTY_SCHEMA)
        )

        reference = build_crosswalk()
        joined = resolve_player_id_by_native_id(long, reference, "sleeper_id")

        snapshot_date = dt.datetime.now(tz=dt.UTC).date()
        return joined.select(
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
            pl.col("stat_value"),
        ).select(CANONICAL_COLUMNS)

    @staticmethod
    def _rows_from_payload(payload: list[dict]) -> list[dict]:
        rows: list[dict] = []
        for entry in payload:
            source_player_id = entry.get("player_id")
            if source_player_id is None:
                continue
            source_player_id = str(source_player_id)

            player_meta = entry.get("player") or {}
            team = entry.get("team") or player_meta.get("team")
            position = entry.get("position") or player_meta.get("position")
            first = player_meta.get("first_name") or ""
            last = player_meta.get("last_name") or ""
            name_raw = f"{first} {last}".strip() or source_player_id

            stats = entry.get("stats") or {}
            for sleeper_key, stat_name in _STAT_MAP.items():
                value = stats.get(sleeper_key)
                if value is None:
                    continue
                rows.append(
                    {
                        "source_player_id": source_player_id,
                        "team": team,
                        "position": position,
                        "player_name_raw": name_raw,
                        "stat_name": stat_name,
                        "stat_value": float(value),
                    }
                )
        return rows
