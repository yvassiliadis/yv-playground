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
from contextlib import suppress
from datetime import datetime, timedelta, timezone
from pathlib import Path

import httpx
import uvicorn
from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException
from fastapi.responses import HTMLResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles

load_dotenv(Path(__file__).parent / ".env")

FOOTBALL_DATA_API_KEY = os.environ.get("FOOTBALL_DATA_API_KEY", "")
if not FOOTBALL_DATA_API_KEY:
    logging.warning("FOOTBALL_DATA_API_KEY is not set — /api/scores will fail")
FOOTBALL_DATA_BASE = "https://api.football-data.org/v4"

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
    "United States",
    "Uruguay",
    "Ecuador",
    "Türkiye",
    "Croatia",
    "Senegal",
    "Austria",
    "Sweden",
    "Ivory Coast",
    "Scotland",
    "Canada",
    "Czechia",
    "Paraguay",
    "South Korea",
    "Australia",
    "Algeria",
    "Egypt",
    "Bosnia & Herzegovina",
    "Ghana",
    "Tunisia",
    "Iran",
    "South Africa",
    "DR Congo",
    "Qatar",
    "Saudi Arabia",
    "Cape Verde",
    "Iraq",
    "Panama",
    "Uzbekistan",
    "New Zealand",
    "Jordan",
    "Curaçao",
    "Haiti",
}

_NAME_MAP: dict[str, str] = {
    "Bosnia-Herzegovina": "Bosnia & Herzegovina",
    "Cape Verde Islands": "Cape Verde",
    "Congo DR": "DR Congo",
    "Turkey": "Türkiye",
}

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


_cache = Cache()
_fetch_lock = asyncio.Lock()
_subscribers: set[asyncio.Queue] = set()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


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
        }


def _is_group_stage(stage: str) -> bool:
    return stage.startswith("GROUP_STAGE") or stage in _GROUP_STAGES


_BRACKET_ROUNDS = [
    {"id": "r32",   "label": "Round of 32",    "stages": ["LAST_32"]},
    {"id": "r16",   "label": "Round of 16",    "stages": ["LAST_16"]},
    {"id": "qf",    "label": "Quarter-finals", "stages": ["QUARTER_FINALS"]},
    {"id": "sf",    "label": "Semi-finals",    "stages": ["SEMI_FINALS"]},
    {"id": "final", "label": "Final",          "stages": ["FINAL", "THIRD_PLACE"]},
]

_MATCH_LABEL = {
    "FINAL":       "Final",
    "THIRD_PLACE": "3rd Place",
}


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


def _build_bracket(matches: list) -> dict:
    # Collect matches per stage
    by_stage: dict[str, list] = {}
    for match in matches:
        stage = match.get("stage", "")
        if not any(stage in r["stages"] for r in _BRACKET_ROUNDS):
            continue
        by_stage.setdefault(stage, []).append(match)

    result = []
    for round_def in _BRACKET_ROUNDS:
        round_matches: list[dict] = []
        utc_dates: list[str] = []

        for stage in round_def["stages"]:
            stage_matches = by_stage.get(stage, [])
            stage_matches = sorted(stage_matches, key=lambda m: m.get("utcDate") or "")
            match_label = _MATCH_LABEL.get(stage)

            for match in stage_matches:
                home_raw = match.get("homeTeam", {}).get("name", "")
                away_raw = match.get("awayTeam", {}).get("name", "")
                home = _normalize_name(home_raw)
                away = _normalize_name(away_raw)

                score_data = match.get("score", {})
                full_time = score_data.get("fullTime", {})

                utc_date = match.get("utcDate")
                if utc_date:
                    utc_dates.append(utc_date)

                round_matches.append({
                    "home":      home if home in _KNOWN_TEAMS else None,
                    "away":      away if away in _KNOWN_TEAMS else None,
                    "homeScore": full_time.get("home"),
                    "awayScore": full_time.get("away"),
                    "status":    match.get("status", ""),
                    "utcDate":   utc_date,
                    "label":     match_label,
                    "minute":    match.get("minute"),
                    "duration":  score_data.get("duration"),
                })

        if round_matches:
            result.append({
                "id":      round_def["id"],
                "label":   round_def["label"],
                "dates":   _fmt_date_range(utc_dates),
                "matches": round_matches,
            })

    return {"rounds": result}


def _build_groups(matches: list) -> dict:
    standings: dict[str, dict[str, dict]] = {}  # group_letter -> {team_name -> stats}
    group_matches: dict[str, list] = {}  # group_letter -> [match_entry]

    for match in matches:
        stage = match.get("stage", "")
        if not _is_group_stage(stage):
            continue

        home_raw = match.get("homeTeam", {}).get("name", "")
        away_raw = match.get("awayTeam", {}).get("name", "")
        home = _normalize_name(home_raw)
        away = _normalize_name(away_raw)

        if home not in _KNOWN_TEAMS or away not in _KNOWN_TEAMS:
            continue

        group_field = match.get("group") or ""
        # Expect "GROUP_A", "GROUP_B", etc.
        if not group_field.startswith("GROUP_"):
            continue
        letter = group_field[len("GROUP_"):]
        if not letter or len(letter) != 1 or not letter.isalpha():
            continue

        # Ensure structures exist
        if letter not in standings:
            standings[letter] = {}
            group_matches[letter] = []

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

        status = match.get("status", "")
        score_data = match.get("score", {})
        full_time = score_data.get("fullTime", {})
        hg = full_time.get("home")
        ag = full_time.get("away")

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

        group_matches[letter].append({
            "home": home,
            "away": away,
            "homeScore": hg,
            "awayScore": ag,
            "status": status,
            "utcDate": match.get("utcDate"),
            "matchday": match.get("matchday"),
            "minute": match.get("minute"),
            "duration": score_data.get("duration"),
        })

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

    return result


def _build_scores(matches: list, scorers: list) -> dict:
    scores: dict = {}

    for match in matches:
        home_raw = match.get("homeTeam", {}).get("name", "")
        away_raw = match.get("awayTeam", {}).get("name", "")
        home = _normalize_name(home_raw)
        away = _normalize_name(away_raw)

        # Skip TBD / placeholder entries
        if home not in _KNOWN_TEAMS or away not in _KNOWN_TEAMS:
            continue

        _ensure_team(scores, home)
        _ensure_team(scores, away)

        stage = match.get("stage", "")
        status = match.get("status", "")
        score_data = match.get("score", {})
        full_time = score_data.get("fullTime", {})
        hg = full_time.get("home")
        ag = full_time.get("away")

        if _is_group_stage(stage):
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
            # Knockout match
            ko_level = _STAGE_TO_KO.get(stage)
            if ko_level is None:
                continue

            if stage == "THIRD_PLACE":
                # Both teams are SF losers; update third if finished
                if status == "FINISHED" and hg is not None and ag is not None:
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
                # ko stays at sf (already set by SF match)
                _set_ko(scores, home, "sf")
                _set_ko(scores, away, "sf")
            elif stage == "FINAL":
                # Both teams reached the final
                _set_ko(scores, home, "final")
                _set_ko(scores, away, "final")
                if status == "FINISHED" and hg is not None and ag is not None:
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
            else:
                # Regular knockout (LAST_32, LAST_16, QF, SF)
                _set_ko(scores, home, ko_level)
                _set_ko(scores, away, ko_level)
                if status == "FINISHED" and hg is not None and ag is not None:
                    scores[home]["gf"] += hg
                    scores[home]["ga"] += ag
                    scores[away]["gf"] += ag
                    scores[away]["ga"] += hg

    # Golden boot — dead-heat rules: 5 pts split equally among N tied leaders
    if scorers:
        top_goals = max((s.get("goals", 0) or 0 for s in scorers), default=0)
        if top_goals > 0:
            leaders = [s for s in scorers if (s.get("goals", 0) or 0) == top_goals]
            boot_pts = 5 / len(leaders)
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
            scores, matches = await _fetch_scores()
            _cache.scores = scores
            _cache.matches = matches
            _cache.fetched_at = datetime.now(tz=timezone.utc)
            payload = {
                "scores":  scores,
                "groups":  _build_groups(matches),
                "bracket": _build_bracket(matches),
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


async def _fetch_scores() -> tuple[dict, list]:
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
    scores = _build_scores(matches, scorers)
    return scores, matches


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

    return _build_bracket(_cache.matches)  # type: ignore[arg-type]


@app.get("/api/live")
async def live_updates() -> StreamingResponse:
    _ensure_poll_task()

    # Always fetch fresh data on connect so the client gets live state immediately
    try:
        async with _fetch_lock:
            scores, matches = await _fetch_scores()
            _cache.scores = scores
            _cache.matches = matches
            _cache.fetched_at = datetime.now(tz=timezone.utc)
        initial = {
            "scores":  _cache.scores,
            "groups":  _build_groups(_cache.matches),
            "bracket": _build_bracket(_cache.matches),
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


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=int(os.environ.get("PORT", 8080)))
