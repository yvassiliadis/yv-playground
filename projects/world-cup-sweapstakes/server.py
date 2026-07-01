# /// script
# requires-python = ">=3.12"
# dependencies = [
#     "fastapi>=0.111.0",
#     "httpx>=0.27.0",
#     "uvicorn[standard]>=0.30.0",
#     "python-dotenv>=1.0.0",
# ]
# ///

import asyncio
import json
import logging
import os
import urllib.parse
from contextlib import suppress
from datetime import datetime, timedelta, timezone
from pathlib import Path

import httpx
import slack_poll
import trivia
import uvicorn
from dotenv import load_dotenv
from fastapi import BackgroundTasks, FastAPI, Header, HTTPException, Request
from fastapi.responses import HTMLResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles

load_dotenv(Path(__file__).parent / ".env")

FOOTBALL_DATA_API_KEY = os.environ.get("FOOTBALL_DATA_API_KEY", "")
if not FOOTBALL_DATA_API_KEY:
    logging.warning("FOOTBALL_DATA_API_KEY is not set — /api/scores will fail")
FOOTBALL_DATA_BASE = "https://api.football-data.org/v4"

ZAFRONIX_WC_API_KEY = os.environ.get("ZAFRONIX_WC_API_KEY", "")
if not ZAFRONIX_WC_API_KEY:
    logging.warning("ZAFRONIX_WC_API_KEY is not set — Zafronix endpoints will fail")
ZAFRONIX_BASE = "https://api.zafronix.com/fifa/worldcup/v1"

SLACK_WEBHOOK_URL = os.environ.get("SLACK_WEBHOOK_URL", "")
TRIVIA_TRIGGER_TOKEN = os.environ.get("TRIVIA_TRIGGER_TOKEN", "")
SLACK_BOT_TOKEN = os.environ.get("SLACK_BOT_TOKEN", "")
SLACK_SIGNING_SECRET = os.environ.get("SLACK_SIGNING_SECRET", "")
SLACK_CHANNEL_ID = os.environ.get("SLACK_CHANNEL_ID", "")

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger(__name__)

app = FastAPI()
app.mount("/static", StaticFiles(directory=Path(__file__).parent), name="static")

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_KNOWN_TEAMS = {
    "Spain",
    "France",
    "England",
    "Portugal",
    "Brazil",
    "Argentina",
    "Germany",
    "Netherlands",
    "Norway",
    "Belgium",
    "Colombia",
    "Morocco",
    "Mexico",
    "Japan",
    "Switzerland",
    "USA",
    "Uruguay",
    "Ecuador",
    "Türkiye",
    "Croatia",
    "Senegal",
    "Austria",
    "Sweden",
    "Côte d'Ivoire",
    "Scotland",
    "Canada",
    "Czechia",
    "Paraguay",
    "Korea Republic",
    "Australia",
    "Algeria",
    "Egypt",
    "Bosnia and Herzegovina",
    "Ghana",
    "Tunisia",
    "Iran",
    "South Africa",
    "Congo DR",
    "Qatar",
    "Saudi Arabia",
    "Cabo Verde",
    "Iraq",
    "Panama",
    "Uzbekistan",
    "New Zealand",
    "Jordan",
    "Curaçao",
    "Haiti",
}

_NAME_MAP: dict[str, str] = {
    # football-data.org name variants → canonical (Zafronix) names
    "United States":         "USA",
    "Ivory Coast":           "Côte d'Ivoire",
    "Bosnia & Herzegovina":  "Bosnia and Herzegovina",
    "Bosnia-Herzegovina":    "Bosnia and Herzegovina",
    "Cape Verde":            "Cabo Verde",
    "Cape Verde Islands":    "Cabo Verde",
    "DR Congo":              "Congo DR",
    "South Korea":           "Korea Republic",
    "Turkey":                "Türkiye",
}

# Corrections for known Zafronix data errors. Key is matchId; value overrides home/away.
_ZAFRONIX_CORRECTIONS: dict[str, dict] = {
    "2026-074": {"home": "Germany", "away": "Paraguay"},
    "2026-077": {"home": "France", "away": "Sweden"},
    "2026-082": {"home": "Belgium", "away": "Senegal"},
    "2026-085": {"home": "Switzerland", "away": "Algeria"},
}
# Applied in _build_bracket() when consuming bracket stage data.

# Golden Boot points are awarded once the tournament is over (after the Final on Jul 19 2026)
_GOLDEN_BOOT_AFTER = datetime(2026, 7, 19, tzinfo=timezone.utc)


def _golden_boot_final() -> bool:
    return datetime.now(tz=timezone.utc) >= _GOLDEN_BOOT_AFTER


# Ordered from least to most advanced — used for ko comparison
_KO_ORDER = ["", "r32", "r16", "qf", "sf", "final", "winner"]

_STAGE_TO_KO: dict[str, str] = {
    "LAST_32": "r32",
    "LAST_16": "r16",
    "QUARTER_FINALS": "qf",
    "SEMI_FINALS": "sf",
    "FINAL": "final",
    "THIRD_PLACE": "sf",  # sets `third`, ko stays sf
}

_GROUP_STAGES = {
    "GROUP_STAGE",
    "PRELIMINARY_ROUND",
    "QUALIFICATION",
}

# ---------------------------------------------------------------------------
# Cache
# ---------------------------------------------------------------------------


class Cache:
    scores: dict | None = None
    matches: list | None = None
    fetched_at: datetime | None = None
    zafronix_bracket: dict | None = None
    zafronix_standings: dict | None = None
    zafronix_fetched_at: datetime | None = None


_cache = Cache()
_fetch_lock = asyncio.Lock()
_zafronix_fetch_lock = asyncio.Lock()
_subscribers: set[asyncio.Queue] = set()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


# No matches are scheduled during 03:00–12:00 ET (07:00–16:00 UTC).
_ZAFRONIX_QUIET_START_UTC = 7
_ZAFRONIX_QUIET_END_UTC = 16


def _in_zafronix_fetch_window(dt: datetime | None = None) -> bool:
    """Return True when it is appropriate to make new Zafronix requests."""
    hour = (dt or datetime.now(tz=timezone.utc)).hour
    return not (_ZAFRONIX_QUIET_START_UTC <= hour < _ZAFRONIX_QUIET_END_UTC)


def _normalize_name(name: str) -> str:
    return _NAME_MAP.get(name, name)


def _ko_level(ko: str) -> int:
    try:
        return _KO_ORDER.index(ko)
    except ValueError:
        return 0


def _set_ko(scores: dict, team: str, level: str) -> None:
    """Set ko for team if level is higher than what's already recorded."""
    if _ko_level(level) > _ko_level(scores[team]["ko"]):
        scores[team]["ko"] = level


def _ko_match_loser(score_data: dict, home: str, away: str, hg: int | None, ag: int | None) -> str | None:
    """Loser of a finished knockout match, or None if undecided.

    Prefers the explicit winner field (covers penalty shootouts where full-time
    is level); falls back to goals when it's absent.
    """
    winner_side = score_data.get("winner")
    if winner_side == "HOME_TEAM":
        return away
    if winner_side == "AWAY_TEAM":
        return home
    if hg is not None and ag is not None:
        if hg > ag:
            return away
        if ag > hg:
            return home
    return None


def _ensure_team(scores: dict, team: str) -> None:
    if team not in scores:
        scores[team] = {
            "gw": 0,
            "gd": 0,
            "gl": 0,
            "gf": 0,
            "ga": 0,
            "ko": "",
            "third": "",
            "boot": 0,
            "out": False,
        }


def _is_group_stage(stage: str) -> bool:
    return stage.startswith("GROUP_STAGE") or stage in _GROUP_STAGES


def _zafronix_advanced_teams(standings: dict) -> set[str]:
    """Return teams with advanced: true from Zafronix group standings."""
    result: set[str] = set()
    for group_teams in standings.get("groups", {}).values():
        for entry in group_teams:
            if entry.get("advanced") and entry.get("team"):
                result.add(_normalize_name(entry["team"]))
    return result


_ZAFRONIX_STAGE_TO_ROUND: dict[str, tuple[str, str]] = {
    "round_of_32":   ("r32",   "Round of 32"),
    "round_of_16":   ("r16",   "Round of 16"),
    "quarter_final": ("qf",    "Quarter-finals"),
    "semi_final":    ("sf",    "Semi-finals"),
    "third_place":   ("final", "3rd Place"),
    "final":         ("final", "Final"),
}


def _live_score(score_data: dict) -> tuple[int | None, int | None]:
    """Return (home_goals, away_goals) using the best available score field.

    football-data.org sets fullTime to null during a live match; halfTime
    carries the end-of-first-half score once it is known.
    """
    for field in ("fullTime", "halfTime"):
        block = score_data.get(field) or {}
        h, a = block.get("home"), block.get("away")
        if h is not None and a is not None:
            return h, a
    return None, None


def _match_score_detail(score_data: dict) -> tuple[int | None, int | None, int | None, int | None]:
    """Return (home, away, pen_home, pen_away) for display.

    For matches decided in extra time or on penalties, the headline score is the
    on-pitch result (regulation + extra time) — the level score going into a
    shootout — and the shootout tally is reported separately. Falls back to the
    live/full-time score otherwise.
    """
    duration = score_data.get("duration")
    reg = score_data.get("regularTime") or {}
    if duration in ("EXTRA_TIME", "PENALTY_SHOOTOUT") and reg.get("home") is not None:
        et = score_data.get("extraTime") or {}
        home = (reg.get("home") or 0) + (et.get("home") or 0)
        away = (reg.get("away") or 0) + (et.get("away") or 0)
    else:
        home, away = _live_score(score_data)

    if duration == "PENALTY_SHOOTOUT":
        # football-data folds the shootout into fullTime, so the tally is
        # fullTime minus the on-pitch score. Prefer that over the explicit
        # penalties block, which this feed can report as an impossible tie.
        ft = score_data.get("fullTime") or {}
        fth, fta = ft.get("home"), ft.get("away")
        if home is not None and fth is not None and fta is not None and (fth, fta) != (home, away):
            return home, away, fth - home, fta - away
        pens = score_data.get("penalties") or {}
        return home, away, pens.get("home"), pens.get("away")
    return home, away, None, None


def _fmt_date_range(utc_dates: list[str]) -> str:
    """Return a human-readable date range like 'Jun 28 – Jul 1' or 'Jul 19'."""
    parsed = []
    for d in utc_dates:
        if not d:
            continue
        try:
            parsed.append(datetime.fromisoformat(d.replace("Z", "+00:00")))
        except ValueError:
            pass
    if not parsed:
        return ""
    lo, hi = min(parsed), max(parsed)
    if lo.month == hi.month and lo.day == hi.day:
        return f"{lo.strftime('%b')} {lo.day}"
    elif lo.month == hi.month:
        return f"{lo.strftime('%b')} {lo.day} – {hi.day}"
    else:
        return f"{lo.strftime('%b')} {lo.day} – {hi.strftime('%b')} {hi.day}"


def _bracket_order_key(stages_data: dict) -> dict:
    """Map matchNo -> display position, threading rounds via homeRef/awayRef.

    Each knockout match references its feeders ("W74"); rooting at the final
    and ordering every round by its leftmost feeding leaf lays the rounds out
    as a consistent bracket tree (feeders of one match sit adjacent in the
    previous round). Returns {} when the tree can't be resolved, so callers
    fall back to matchNo order.
    """
    by_no: dict = {}
    for matches in stages_data.values():
        for m in matches:
            no = m.get("matchNo")
            if no is not None:
                by_no[no] = m

    def feeder(ref):
        if isinstance(ref, str) and ref.startswith("W"):
            try:
                return by_no.get(int(ref[1:]))
            except ValueError:
                return None
        return None

    def leaf_order(m):
        home_feeder = feeder(m.get("homeRef"))
        away_feeder = feeder(m.get("awayRef"))
        if home_feeder is None and away_feeder is None:
            return [m.get("matchNo")]
        home_leaves = leaf_order(home_feeder) if home_feeder else [m.get("matchNo")]
        away_leaves = leaf_order(away_feeder) if away_feeder else [m.get("matchNo")]
        return home_leaves + away_leaves

    final = (stages_data.get("final") or [None])[0]
    if not final:
        return {}
    leaf_idx = {no: i for i, no in enumerate(leaf_order(final))}

    def leftmost(m):
        # Follow the home feeder down to a leaf; its leaf position is the
        # match's display rank. The fallback only applies to matches not
        # reachable from the final (e.g. third-place), which are ordered
        # within their own stage anyway, so it just sends them to the end.
        home_feeder = feeder(m.get("homeRef"))
        return leftmost(home_feeder) if home_feeder else leaf_idx.get(m.get("matchNo"), 1_000_000)

    return {
        m.get("matchNo"): leftmost(m)
        for matches in stages_data.values()
        for m in matches
    }


def _build_bracket(zafronix_bracket: dict, fd_matches: list) -> dict:
    """Build bracket from Zafronix structure overlaid with fd live scores."""
    if not zafronix_bracket:
        return {"rounds": []}

    # Build fd lookup by normalised team pair (KO matches only)
    fd_by_teams: dict[tuple[str, str], dict] = {}
    for m in fd_matches:
        h = _normalize_name(m.get("homeTeam", {}).get("name", ""))
        a = _normalize_name(m.get("awayTeam", {}).get("name", ""))
        stage = m.get("stage", "")
        if h and a and not _is_group_stage(stage):
            fd_by_teams[(h, a)] = m

    stages_data = zafronix_bracket.get("stages", {})
    order_key = _bracket_order_key(stages_data)

    # Process stages in order; third_place and final share the "final" round id
    stage_order = [
        "round_of_32",
        "round_of_16",
        "quarter_final",
        "semi_final",
        ("third_place", "final"),  # combined into one output round
    ]

    result: list[dict] = []

    for stage_entry in stage_order:
        if isinstance(stage_entry, tuple):
            # Combined round: third_place + final
            z_stages = list(stage_entry)
            round_id = "final"
            round_label = "Final"
        else:
            z_stages = [stage_entry]
            round_id, round_label = _ZAFRONIX_STAGE_TO_ROUND[stage_entry]

        round_matches: list[dict] = []
        utc_dates: list[str] = []

        for z_stage in z_stages:
            stage_matches = stages_data.get(z_stage, [])
            stage_matches = sorted(
                stage_matches,
                key=lambda m: order_key.get(m.get("matchNo"), m.get("matchNo") or 0),
            )

            for zm in stage_matches:
                # Apply corrections
                match_id = zm.get("matchId")
                home_raw = zm.get("home")
                away_raw = zm.get("away")
                home = _normalize_name(home_raw) if home_raw else None
                away = _normalize_name(away_raw) if away_raw else None
                if match_id and match_id in _ZAFRONIX_CORRECTIONS:
                    correction = _ZAFRONIX_CORRECTIONS[match_id]
                    log.info("Applying Zafronix correction for %s: %s vs %s", match_id, correction.get("home"), correction.get("away"))
                    home = correction.get("home", home)
                    away = correction.get("away", away)

                # Treat unknown teams as None
                if home and home not in _KNOWN_TEAMS:
                    home = None
                if away and away not in _KNOWN_TEAMS:
                    away = None

                # Look up fd match (only possible when both teams are known)
                fd_match = None
                if home is not None and away is not None:
                    fd_match = fd_by_teams.get((home, away)) or fd_by_teams.get((away, home))

                # Score and status
                pen_home = pen_away = None
                if fd_match is not None:
                    score = fd_match.get("score", {})
                    hg, ag, pen_home, pen_away = _match_score_detail(score)
                    status = fd_match.get("status", "SCHEDULED")
                    minute = fd_match.get("minute")
                    duration = score.get("duration")
                else:
                    hg = zm.get("homeScore")
                    ag = zm.get("awayScore")
                    # Infer status from Zafronix when fd is unavailable
                    if hg is not None and ag is not None:
                        fallback_status = "FINISHED"
                    else:
                        fallback_status = "SCHEDULED"
                    status = fallback_status
                    minute = None
                    duration = None

                # Determine match label
                if z_stage == "final":
                    match_label = "Final"
                elif z_stage == "third_place":
                    match_label = "3rd Place"
                else:
                    match_label = None

                kickoff = zm.get("kickoffUtc")
                if kickoff:
                    utc_dates.append(kickoff)

                round_matches.append({
                    "home":      home,
                    "away":      away,
                    "homeScore": hg,
                    "awayScore": ag,
                    "penHome":   pen_home,
                    "penAway":   pen_away,
                    "status":    status,
                    "utcDate":   kickoff,
                    "label":     match_label,
                    "minute":    minute,
                    "duration":  duration,
                    "stadium":   zm.get("stadium"),
                    "city":      zm.get("city"),
                })

        if round_matches:
            result.append({
                "id":      round_id,
                "label":   round_label,
                "dates":   _fmt_date_range(utc_dates),
                "matches": round_matches,
            })

    return {"rounds": result}


def _build_groups(matches: list) -> dict:
    standings: dict[str, dict[str, dict]] = {}  # group_letter -> {team_name -> stats}
    group_matches: dict[str, list] = {}  # group_letter -> [match_entry]

    stages_seen: set[str] = set()
    groups_seen: set[str] = set()

    for match in matches:
        stage = match.get("stage", "")
        stages_seen.add(stage)

        if not _is_group_stage(stage):
            continue

        home_raw = match.get("homeTeam", {}).get("name", "")
        away_raw = match.get("awayTeam", {}).get("name", "")
        home = _normalize_name(home_raw)
        away = _normalize_name(away_raw)

        if not home or not away:
            continue

        group_field = match.get("group") or ""
        groups_seen.add(group_field)
        if group_field.startswith("GROUP_"):
            letter = group_field[len("GROUP_"):]
        elif len(group_field) == 1 and group_field.isalpha():
            letter = group_field.upper()
        else:
            letter = None  # group field absent or unrecognised

        if letter and (len(letter) != 1 or not letter.isalpha()):
            letter = None

        status = match.get("status", "")
        score_data = match.get("score", {})
        hg, ag = _live_score(score_data)

        match_entry = {
            "home": home,
            "away": away,
            "homeScore": hg,
            "awayScore": ag,
            "status": status,
            "utcDate": match.get("utcDate"),
            "matchday": match.get("matchday"),
            "minute": match.get("minute"),
            "duration": score_data.get("duration"),
        }

        # Matches without a parseable group letter still appear in the schedule
        # but are excluded from standings (we can't group them correctly).
        group_key = letter or "_"
        if group_key not in group_matches:
            group_matches[group_key] = []
        group_matches[group_key].append(match_entry)

        if letter is None:
            continue

        # Standings only for matches with a known group letter
        if letter not in standings:
            standings[letter] = {}
        for team in (home, away):
            if team not in standings[letter]:
                standings[letter][team] = {
                    "name": team,
                    "played": 0,
                    "won": 0,
                    "drawn": 0,
                    "lost": 0,
                    "gf": 0,
                    "ga": 0,
                }

        if status == "FINISHED" and hg is not None and ag is not None:
            standings[letter][home]["played"] += 1
            standings[letter][away]["played"] += 1
            standings[letter][home]["gf"] += hg
            standings[letter][home]["ga"] += ag
            standings[letter][away]["gf"] += ag
            standings[letter][away]["ga"] += hg
            if hg > ag:
                standings[letter][home]["won"] += 1
                standings[letter][away]["lost"] += 1
            elif hg < ag:
                standings[letter][away]["won"] += 1
                standings[letter][home]["lost"] += 1
            else:
                standings[letter][home]["drawn"] += 1
                standings[letter][away]["drawn"] += 1

    log.info(
        "build_groups: stages=%s groups=%s",
        sorted(stages_seen),
        sorted(groups_seen),
    )

    result = {}
    for letter in sorted(standings.keys()):
        team_rows = []
        for entry in standings[letter].values():
            pts = entry["won"] * 3 + entry["drawn"]
            gd = entry["gf"] - entry["ga"]
            team_rows.append({**entry, "pts": pts, "gd": gd})

        team_rows.sort(key=lambda t: (-t["pts"], -t["gd"], -t["gf"], t["name"]))

        sorted_matches = sorted(
            group_matches[letter],
            key=lambda m: (m.get("matchday") or 0, m.get("utcDate") or ""),
        )

        result[letter] = {
            "standings": team_rows,
            "matches": sorted_matches,
        }

    # Include ungrouped matches so the schedule tab can display them.
    # The "_" key is intentionally not a valid group letter — clients
    # that render group standings should skip it.
    if "_" in group_matches:
        ungrouped = sorted(
            group_matches["_"],
            key=lambda m: (m.get("matchday") or 0, m.get("utcDate") or ""),
        )
        result["_"] = {"standings": [], "matches": ungrouped}

    return result


def _build_scores(matches: list, scorers: list, zafronix_standings: dict | None = None) -> dict:
    scores: dict = {}

    for match in matches:
        home_raw = match.get("homeTeam", {}).get("name", "")
        away_raw = match.get("awayTeam", {}).get("name", "")
        home = _normalize_name(home_raw)
        away = _normalize_name(away_raw)

        known_home = home in _KNOWN_TEAMS
        known_away = away in _KNOWN_TEAMS

        stage = match.get("stage", "")
        status = match.get("status", "")
        score_data = match.get("score", {})
        full_time = score_data.get("fullTime", {})
        hg = full_time.get("home")
        ag = full_time.get("away")

        if _is_group_stage(stage):
            # Group stage: skip if either team is unknown (can't attribute stats correctly)
            if not known_home or not known_away:
                continue
            _ensure_team(scores, home)
            _ensure_team(scores, away)
            # Only tally finished group matches
            if status == "FINISHED" and hg is not None and ag is not None:
                scores[home]["gf"] += hg
                scores[home]["ga"] += ag
                scores[away]["gf"] += ag
                scores[away]["ga"] += hg
                if hg > ag:
                    scores[home]["gw"] += 1
                    scores[away]["gl"] += 1
                elif hg < ag:
                    scores[away]["gw"] += 1
                    scores[home]["gl"] += 1
                else:
                    scores[home]["gd"] += 1
                    scores[away]["gd"] += 1
        else:
            # Knockout match: credit known teams even if opponent is TBD
            if not known_home and not known_away:
                continue

            ko_level = _STAGE_TO_KO.get(stage)
            if ko_level is None:
                continue

            if known_home:
                _ensure_team(scores, home)
            if known_away:
                _ensure_team(scores, away)

            if stage == "THIRD_PLACE":
                # Both teams are SF losers; update third if finished
                if known_home:
                    _set_ko(scores, home, "sf")
                if known_away:
                    _set_ko(scores, away, "sf")
                if known_home and known_away and status == "FINISHED" and hg is not None and ag is not None:
                    # Also tally goals for the third place match
                    scores[home]["gf"] += hg
                    scores[home]["ga"] += ag
                    scores[away]["gf"] += ag
                    scores[away]["ga"] += hg
                    if hg > ag:
                        scores[home]["third"] = "won"
                        scores[away]["third"] = "lost"
                    elif hg < ag:
                        scores[away]["third"] = "won"
                        scores[home]["third"] = "lost"
                    # Draw shouldn't happen but leave third="" if so
            elif stage == "FINAL":
                # Both teams reached the final
                if known_home:
                    _set_ko(scores, home, "final")
                if known_away:
                    _set_ko(scores, away, "final")
                if known_home and known_away and status == "FINISHED" and hg is not None and ag is not None:
                    scores[home]["gf"] += hg
                    scores[home]["ga"] += ag
                    scores[away]["gf"] += ag
                    scores[away]["ga"] += hg
                    # Determine winner (may go to penalties)
                    winner_side = score_data.get("winner")  # "HOME_TEAM" | "AWAY_TEAM"
                    if winner_side == "HOME_TEAM":
                        _set_ko(scores, home, "winner")
                    elif winner_side == "AWAY_TEAM":
                        _set_ko(scores, away, "winner")
                    loser = _ko_match_loser(score_data, home, away, hg, ag)
                    if loser:
                        scores[loser]["out"] = True
            else:
                # Regular knockout (LAST_32, LAST_16, QF, SF)
                if known_home:
                    _set_ko(scores, home, ko_level)
                if known_away:
                    _set_ko(scores, away, ko_level)
                if known_home and known_away and status == "FINISHED" and hg is not None and ag is not None:
                    scores[home]["gf"] += hg
                    scores[home]["ga"] += ag
                    scores[away]["gf"] += ag
                    scores[away]["ga"] += hg
                    loser = _ko_match_loser(score_data, home, away, hg, ag)
                    if loser:
                        scores[loser]["out"] = True

    # Credit confirmed group-stage qualifiers with their R32 advancement.
    qualifiers = _zafronix_advanced_teams(zafronix_standings) if zafronix_standings is not None else set()
    for team in qualifiers:
        if team in scores:
            _set_ko(scores, team, "r32")

    # Golden boot — dead-heat rules: 5 pts split equally among N tied leaders.
    # Points are only awarded once GOLDEN_BOOT_FINAL is True.
    if scorers:
        top_goals = max((s.get("goals", 0) or 0 for s in scorers), default=0)
        if top_goals > 0:
            leaders = [s for s in scorers if (s.get("goals", 0) or 0) == top_goals]
            boot_pts = 5 / len(leaders)
            if _golden_boot_final():
                for leader in leaders:
                    team = _normalize_name(leader.get("team", {}).get("name", ""))
                    if team in scores:
                        scores[team]["boot"] = boot_pts
            scores["_goldenBoot"] = {
                "leaders": [
                    {
                        "player": l.get("player", {}).get("name", ""),
                        "team": _normalize_name(l.get("team", {}).get("name", "")),
                    }
                    for l in leaders
                ],
                "goals": top_goals,
                "bootPts": boot_pts,
                "awarded": _golden_boot_final(),
            }

    return scores


def _in_match_window(matches: list) -> bool:
    """True when a match is live or kicking off within the next 15 min or finished within 3 h."""
    now = datetime.now(tz=timezone.utc)
    for match in matches:
        if match.get("status") in ("IN_PLAY", "PAUSED"):
            return True
        utc_date = match.get("utcDate")
        if not utc_date:
            continue
        try:
            kickoff = datetime.fromisoformat(utc_date.replace("Z", "+00:00"))
        except ValueError:
            continue
        if kickoff - timedelta(minutes=15) <= now <= kickoff + timedelta(hours=3):
            return True
    return False


async def _broadcast(payload: dict) -> None:
    dead = set()
    for q in _subscribers:
        try:
            q.put_nowait(payload)
        except asyncio.QueueFull:
            dead.add(q)
    _subscribers.difference_update(dead)


async def _poll_loop() -> None:
    while True:
        await asyncio.sleep(30)
        if not _subscribers:
            continue
        if not _in_match_window(_cache.matches or []):
            continue
        try:
            zafronix = await _get_zafronix_data()
            scores, matches = await _fetch_scores(zafronix_standings=zafronix.get("standings"))
            _cache.scores = scores
            _cache.matches = matches
            _cache.fetched_at = datetime.now(tz=timezone.utc)
            payload = {
                "scores":  scores,
                "groups":  _build_groups(matches),
                "bracket": _build_bracket(zafronix["bracket"], matches),
            }
            await _broadcast(payload)
            log.info("Live update broadcast to %d subscriber(s)", len(_subscribers))
        except Exception as exc:
            log.warning("Background poll failed: %s", exc)


_poll_task: asyncio.Task | None = None


def _ensure_poll_task() -> None:
    global _poll_task
    if _poll_task is None or _poll_task.done():
        _poll_task = asyncio.create_task(_poll_loop())
        log.info("Background poll task started")


def _needs_refresh(matches: list) -> bool:
    """Return True if any unfinished match is in its result window (kickoff+2h to kickoff+6h)."""
    now = datetime.now(tz=timezone.utc)
    for match in matches:
        if match.get("status") == "FINISHED":
            continue
        utc_date = match.get("utcDate")
        if not utc_date:
            continue
        try:
            kickoff = datetime.fromisoformat(utc_date.replace("Z", "+00:00"))
        except ValueError:
            continue
        window_start = kickoff + timedelta(hours=2)
        window_end = kickoff + timedelta(hours=6)
        if window_start <= now <= window_end:
            return True
    return False


async def _fetch_zafronix_data() -> tuple[dict, dict]:
    """Fetch bracket and standings from Zafronix. Returns (bracket, standings)."""
    headers = {"X-API-Key": ZAFRONIX_WC_API_KEY}
    async with httpx.AsyncClient(timeout=15.0) as client:
        bracket_resp, standings_resp = await asyncio.gather(
            client.get(f"{ZAFRONIX_BASE}/bracket", params={"year": 2026}, headers=headers),
            client.get(f"{ZAFRONIX_BASE}/standings", params={"year": 2026}, headers=headers),
        )
    bracket_resp.raise_for_status()
    standings_resp.raise_for_status()
    return bracket_resp.json(), standings_resp.json()


async def _get_zafronix_data() -> dict:
    """Return cached Zafronix data, refreshing when cache is stale and in fetch window."""
    async with _zafronix_fetch_lock:
        now = datetime.now(tz=timezone.utc)
        cache_age = (
            (now - _cache.zafronix_fetched_at).total_seconds()
            if _cache.zafronix_fetched_at
            else float("inf")
        )

        if _cache.zafronix_bracket is None:
            # Cold start: only attempt if no prior attempt, or last attempt was >60s ago
            if _cache.zafronix_fetched_at is None or cache_age > 60:
                try:
                    bracket, standings = await _fetch_zafronix_data()
                    _cache.zafronix_bracket = bracket
                    _cache.zafronix_standings = standings
                    _cache.zafronix_fetched_at = now
                    log.info("Zafronix cold-start fetch complete")
                except Exception as exc:
                    log.warning("Zafronix cold-start fetch failed: %s", exc)
                    _cache.zafronix_fetched_at = now  # gate retries
        elif cache_age > 3600 and _in_zafronix_fetch_window(now):
            # Cache expired and in fetch window: refresh
            try:
                bracket, standings = await _fetch_zafronix_data()
                _cache.zafronix_bracket = bracket
                _cache.zafronix_standings = standings
                _cache.zafronix_fetched_at = now
                log.info("Zafronix cache refreshed")
            except Exception as exc:
                log.warning("Zafronix refresh failed, using stale cache: %s", exc)

        return {
            "bracket": _cache.zafronix_bracket or {},
            "standings": _cache.zafronix_standings or {},
        }


async def _fetch_scores(zafronix_standings: dict | None = None) -> tuple[dict, list]:
    headers = {"X-Auth-Token": FOOTBALL_DATA_API_KEY}
    async with httpx.AsyncClient(timeout=15.0) as client:
        matches_resp, scorers_resp = await asyncio.gather(
            client.get(
                f"{FOOTBALL_DATA_BASE}/competitions/WC/matches", headers=headers
            ),
            client.get(
                f"{FOOTBALL_DATA_BASE}/competitions/WC/scorers", headers=headers
            ),
        )
    matches_resp.raise_for_status()
    scorers_resp.raise_for_status()

    matches = matches_resp.json().get("matches", [])
    scorers = scorers_resp.json().get("scorers", [])
    if not zafronix_standings:
        zafronix = await _get_zafronix_data()
        zafronix_standings = zafronix.get("standings")
    scores = _build_scores(matches, scorers, zafronix_standings)
    return scores, matches


async def _todays_poll_fixtures(now: datetime) -> list:
    """Fetch all WC matches and return today's fixtures; [] on any fetch failure."""
    try:
        zafronix = await _get_zafronix_data()
        _, matches = await _fetch_scores(zafronix_standings=zafronix.get("standings"))
        return slack_poll.todays_fixtures(matches, now)
    except Exception as exc:
        log.warning("Fixture fetch failed — posting trivia only: %s", exc)
        return []


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------


@app.get("/", response_class=HTMLResponse)
async def serve_html() -> HTMLResponse:
    html_path = Path(__file__).parent / "sweepstakes.html"
    if not html_path.exists():
        raise HTTPException(status_code=404, detail="sweepstakes.html not found")
    return HTMLResponse(content=html_path.read_text(encoding="utf-8"))


@app.get("/api/scores")
async def get_scores() -> dict:
    async with _fetch_lock:
        now = datetime.now(tz=timezone.utc)

        # Cold cache — fetch unconditionally
        if _cache.scores is None:
            log.info("Cache empty — fetching scores")
            try:
                scores, matches = await _fetch_scores()
            except Exception as exc:
                log.error("Fetch failed: %s", exc)
                raise HTTPException(
                    status_code=503, detail="Failed to fetch scores"
                ) from exc
            _cache.scores = scores
            _cache.matches = matches
            _cache.fetched_at = now
            return scores

        # Warm cache — check result window
        if _needs_refresh(_cache.matches):  # type: ignore[arg-type]
            stale = (now - _cache.fetched_at).total_seconds() > 300  # type: ignore[operator]
            if stale:
                log.info("Result window active and cache stale — refreshing")
                try:
                    scores, matches = await _fetch_scores()
                    _cache.scores = scores
                    _cache.matches = matches
                    _cache.fetched_at = now
                except Exception as exc:
                    log.warning("Refresh failed, returning stale cache: %s", exc)
            else:
                log.debug(
                    "Result window active but cache fresh (< 5 min) — skipping refresh"
                )
        else:
            log.debug("No result window active — returning cached data")

        return _cache.scores  # type: ignore[return-value]


@app.get("/api/groups")
async def get_groups() -> dict:
    async with _fetch_lock:
        if _cache.matches is None:
            log.info("Cache empty — fetching scores for groups")
            try:
                scores, matches = await _fetch_scores()
            except Exception as exc:
                log.error("Fetch failed: %s", exc)
                raise HTTPException(
                    status_code=503, detail="Failed to fetch scores"
                ) from exc
            _cache.scores = scores
            _cache.matches = matches
            _cache.fetched_at = datetime.now(tz=timezone.utc)

    return _build_groups(_cache.matches)  # type: ignore[arg-type]


@app.get("/api/bracket")
async def get_bracket() -> dict:
    async with _fetch_lock:
        if _cache.matches is None:
            log.info("Cache empty — fetching scores for bracket")
            try:
                scores, matches = await _fetch_scores()
            except Exception as exc:
                log.error("Fetch failed: %s", exc)
                raise HTTPException(
                    status_code=503, detail="Failed to fetch scores"
                ) from exc
            _cache.scores = scores
            _cache.matches = matches
            _cache.fetched_at = datetime.now(tz=timezone.utc)

    zafronix = await _get_zafronix_data()
    return _build_bracket(zafronix["bracket"], _cache.matches or [])  # type: ignore[arg-type]


@app.get("/api/live")
async def live_updates() -> StreamingResponse:
    _ensure_poll_task()

    try:
        async with _fetch_lock:
            now = datetime.now(tz=timezone.utc)
            if _cache.scores is None or _cache.fetched_at is None or \
               (now - _cache.fetched_at).total_seconds() > 30:
                scores, matches = await _fetch_scores()
                _cache.scores = scores
                _cache.matches = matches
                _cache.fetched_at = now
        zafronix = await _get_zafronix_data()
        initial = {
            "scores":  _cache.scores,
            "groups":  _build_groups(_cache.matches),
            "bracket": _build_bracket(zafronix["bracket"], _cache.matches or []),
        }
    except Exception as exc:
        log.warning("Initial fetch on SSE connect failed: %s", exc)
        initial = None

    q: asyncio.Queue = asyncio.Queue(maxsize=10)
    _subscribers.add(q)

    async def stream():
        try:
            if initial:
                yield f"data: {json.dumps(initial)}\n\n"
            while True:
                try:
                    payload = await asyncio.wait_for(q.get(), timeout=25)
                    yield f"data: {json.dumps(payload)}\n\n"
                except asyncio.TimeoutError:
                    yield ": keepalive\n\n"
        finally:
            _subscribers.discard(q)

    return StreamingResponse(
        stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@app.get("/api/debug")
async def debug_matches() -> dict:
    """Return a digest of raw API fields to diagnose stage/group parsing issues."""
    async with _fetch_lock:
        if _cache.matches is None:
            try:
                scores, matches = await _fetch_scores()
                _cache.scores = scores
                _cache.matches = matches
                _cache.fetched_at = datetime.now(tz=timezone.utc)
            except Exception as exc:
                raise HTTPException(status_code=503, detail=str(exc)) from exc

    stages: dict[str, int] = {}
    groups: dict[str, int] = {}
    sample: list[dict] = []
    bracket_matches: list[dict] = []
    for m in _cache.matches:  # type: ignore[union-attr]
        s = m.get("stage", "")
        g = m.get("group") or ""
        stages[s] = stages.get(s, 0) + 1
        groups[g] = groups.get(g, 0) + 1
        if len(sample) < 5:
            sample.append({
                "id": m.get("id"),
                "stage": s,
                "group": g,
                "matchday": m.get("matchday"),
                "home": m.get("homeTeam", {}).get("name"),
                "away": m.get("awayTeam", {}).get("name"),
                "utcDate": m.get("utcDate"),
                "status": m.get("status"),
            })
        if s in ("LAST_32", "LAST_16", "QUARTER_FINALS", "SEMI_FINALS", "FINAL", "THIRD_PLACE"):
            bracket_matches.append({
                "id": m.get("id"),
                "stage": s,
                "matchday": m.get("matchday"),
                "home": m.get("homeTeam", {}).get("name"),
                "away": m.get("awayTeam", {}).get("name"),
                "utcDate": m.get("utcDate"),
                "status": m.get("status"),
            })
    bracket_matches.sort(key=lambda m: (m.get("utcDate") or "", m.get("id") or 0))
    return {"stages": stages, "groups": groups, "sample": sample, "bracket": bracket_matches}


def _build_daily(now: datetime, fixtures: list) -> tuple[list, dict, str]:
    header_blocks, fallback = trivia.trivia_blocks(now)
    state = slack_poll.initial_poll_state(fixtures, now, header_blocks)
    blocks = slack_poll.build_message_blocks(state, now)
    metadata = {"event_type": "wc_prediction_poll", "event_payload": state}
    return blocks, metadata, fallback


@app.get("/api/trivia/preview")
async def trivia_preview() -> dict:
    now = datetime.now(tz=timezone.utc)
    fixtures = await _todays_poll_fixtures(now)
    blocks, _, _ = _build_daily(now, fixtures)
    return {"blocks": blocks}


@app.post("/api/trivia/post")
async def trivia_post(x_trigger_token: str = Header(default="")) -> dict:
    if not TRIVIA_TRIGGER_TOKEN or x_trigger_token != TRIVIA_TRIGGER_TOKEN:
        raise HTTPException(status_code=401, detail="invalid trigger token")
    now = datetime.now(tz=timezone.utc)
    fixtures = await _todays_poll_fixtures(now)
    blocks, metadata, fallback = _build_daily(now, fixtures)
    posted = False
    if SLACK_BOT_TOKEN and SLACK_CHANNEL_ID:
        try:
            await slack_poll.post_message(
                SLACK_BOT_TOKEN, SLACK_CHANNEL_ID, blocks, fallback, metadata
            )
            posted = True
        except Exception as exc:
            log.error("Slack post failed: %s", exc)
            raise HTTPException(status_code=502, detail="Slack post failed") from exc
    else:
        log.warning("SLACK_BOT_TOKEN/SLACK_CHANNEL_ID not set — composed but not posted")
    return {"posted": posted, "blocks": blocks}


async def _handle_vote(payload: dict) -> None:
    try:
        action = payload["actions"][0]
        value = json.loads(action["value"])
        game_id, pick = value["game_id"], value["pick"]
        user_id = payload["user"]["id"]
        channel = payload["channel"]["id"]
        ts = payload["message"]["ts"]
        meta = payload["message"].get("metadata") or {}
        state = meta.get("event_payload") or {}
        now = datetime.now(tz=timezone.utc)
        state, changed, error = slack_poll.apply_vote(state, game_id, user_id, pick, now)
        if error == "closed":
            await slack_poll.send_ephemeral(
                payload["response_url"],
                "⏱️ Voting's closed for this one — it already kicked off.",
            )
            return
        if not changed:
            return
        blocks = slack_poll.build_message_blocks(state, now)
        await slack_poll.update_message(
            SLACK_BOT_TOKEN, channel, ts, blocks, "Prediction poll updated",
            {"event_type": "wc_prediction_poll", "event_payload": state},
        )
    except Exception as exc:
        log.error("Vote handling failed: %s", exc)


@app.post("/api/slack/interactions")
async def slack_interactions(request: Request, background: BackgroundTasks) -> dict:
    body = (await request.body()).decode()
    now = datetime.now(tz=timezone.utc)
    if not slack_poll.verify_slack_signature(
        SLACK_SIGNING_SECRET,
        request.headers.get("X-Slack-Request-Timestamp", ""),
        body,
        request.headers.get("X-Slack-Signature", ""),
        now,
    ):
        raise HTTPException(status_code=401, detail="bad signature")
    parsed = urllib.parse.parse_qs(body)
    payload = json.loads(parsed.get("payload", ["{}"])[0])
    background.add_task(_handle_vote, payload)
    return {}


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=int(os.environ.get("PORT", 8080)))
