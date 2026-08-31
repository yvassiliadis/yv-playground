"""ESPN unofficial fantasy football projections source.

Fetches ESPN's unofficial (undocumented, but widely reverse-engineered)
fantasy players endpoint:
`https://lm-api-reads.fantasy.espn.com/apis/v3/games/ffl/seasons/{season}/players`
with `scoringPeriodId=0` (ESPN's convention for "the whole season", matching
this project's `week=0`) and `view=kona_player_info`, plus the
`x-fantasy-filter` header ESPN's own web client sends to select which
players/stats come back.

Response shape: **verified against a live response, with the stat-ID
mapping partially decoded empirically rather than fully confirmed.** Despite
this task's brief stating live network access would not be available, this
environment turned out to have outbound network access after all. The URL,
the `x-fantasy-filter` header shape (`filterStatsForCurrentSeasonScoringPeriodId`
/ `useFullProjectionTable` / `sortAppliedStatTotal`, reconstructed from the
open-source `espn-api` Python package and community write-ups since ESPN
publishes no spec for this endpoint), and the nested `stats[].stats` dict
keyed by internal numeric stat IDs were all confirmed by fetching real 2026
season data. `tests/fixtures/sources/espn/sample_response.json` is a trimmed
set of real players from that response (one fabricated weekly-actual stats
block was appended to one player to exercise the season-vs-week filter, and
one fully fabricated "unrostered" player was added to exercise the
unmatched-native-ID path -- both called out where they appear).

`_STAT_ID_MAP`'s entries were individually checked against real players'
projected stat lines by magnitude (e.g. ID `3` tracks a starting QB's
passing yards in the 3000-4000 range, ID `53` tracks a WR1's reception count
in the 50-90 range, ID `72` stays near the single digits expected for
fumbles lost) rather than trusted from a single unverified source -- this is
still inference from magnitude, not an ESPN-published legend, so treat it as
higher-confidence-than-guessed but not certain. IDs with a home in the
scoring vocabulary that could *not* be corroborated this way were left out
rather than guessed.

`_PRO_TEAM_MAP` was calibrated the same empirical way: each entry was
checked against a real player's actual current team (a 2025-drafted Bears
rookie's `proTeamId` confirms `3` = CHI, a 2025-drafted Chargers rookie's
confirms `24` = LAC, etc) rather than copied from a possibly-stale
third-party ID table. It is deliberately partial -- an unrecognized
`proTeamId` surfaces as `None` rather than guessing.
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

BASE_URL = "https://lm-api-reads.fantasy.espn.com/apis/v3/games/ffl/seasons"

# statSourceId ESPN uses for a projection row within a player's `stats` list
# (as opposed to `0`, which marks an already-happened actual).
_PROJECTED_STAT_SOURCE_ID = 1
_SEASON_SCORING_PERIOD_ID = 0

# ESPN internal numeric stat ID -> canonical stat_name, mapped only for IDs
# with a home in the scoring vocabulary. See module docstring: each ID was
# checked against real players' projected stat magnitudes, not just copied
# from a third-party source.
_STAT_ID_MAP = {
    "3": "passing yard",
    "4": "passing td",
    "19": "passing 2-pt conversion",
    "20": "pass intercepted",
    "24": "rushing yard",
    "25": "rushing td",
    "26": "rushing 2-pt conversion",
    "42": "receiving yard",
    "43": "receiving td",
    "44": "receiving 2-pt conversion",
    "53": "reception",
    "72": "fumble lost",
}

_POSITION_ID_MAP = {
    1: "QB",
    2: "RB",
    3: "WR",
    4: "TE",
    5: "K",
    16: "DST",
}

# Deliberately partial -- covers only the teams checked against a real
# player's known team in this task (see module docstring). An unrecognized
# proTeamId returns None rather than a guessed abbreviation.
_PRO_TEAM_MAP = {
    3: "CHI",
    20: "LV",
    24: "LAC",
}

_EMPTY_SCHEMA = {
    "source_player_id": pl.String,
    "team": pl.String,
    "position": pl.String,
    "player_name_raw": pl.String,
    "stat_name": pl.String,
    "stat_value": pl.Float64,
}


class ESPNSource:
    name = "espn"

    def fetch(self, season: int, week: int = 0) -> pl.DataFrame:
        if week != 0:
            raise NotImplementedError(
                "ESPNSource only supports seasonal (week=0) projections"
            )
        url = f"{BASE_URL}/{season}/players"
        headers = {
            "x-fantasy-filter": (
                '{"players":{"limit":1000,"sortPercOwned":'
                '{"sortPriority":1,"sortAsc":false}}}'
            )
        }
        with make_http_client(headers=headers) as client:
            response = client.get(
                url,
                params={
                    "scoringPeriodId": _SEASON_SCORING_PERIOD_ID,
                    "view": "kona_player_info",
                },
            )
            response.raise_for_status()
            payload = response.json()
        return self._parse(payload, season)

    def _parse(self, payload: list[dict], season: int) -> pl.DataFrame:
        rows = self._rows_from_payload(payload, season)
        long = (
            pl.DataFrame(rows, schema=_EMPTY_SCHEMA)
            if rows
            else pl.DataFrame(schema=_EMPTY_SCHEMA)
        )

        reference = build_crosswalk()
        joined = resolve_player_id_by_native_id(long, reference, "espn_id")

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
    def _rows_from_payload(payload: list[dict], season: int) -> list[dict]:
        rows: list[dict] = []
        for player in payload:
            espn_id = player.get("id")
            if espn_id is None:
                continue
            source_player_id = str(espn_id)
            name_raw = player.get("fullName") or source_player_id
            position = _POSITION_ID_MAP.get(player.get("defaultPositionId"))
            team = _PRO_TEAM_MAP.get(player.get("proTeamId"))

            # `seasonId` must also match: ESPN can carry a prior season's
            # season-total block (same scoringPeriodId=0/statSourceId=1
            # shape) alongside the current one for the same player.
            season_projection = next(
                (
                    entry
                    for entry in player.get("stats") or []
                    if entry.get("scoringPeriodId") == _SEASON_SCORING_PERIOD_ID
                    and entry.get("statSourceId") == _PROJECTED_STAT_SOURCE_ID
                    and entry.get("seasonId") == season
                ),
                None,
            )
            if season_projection is None:
                continue

            stats = season_projection.get("stats") or {}
            for espn_stat_id, stat_name in _STAT_ID_MAP.items():
                value = stats.get(espn_stat_id)
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
