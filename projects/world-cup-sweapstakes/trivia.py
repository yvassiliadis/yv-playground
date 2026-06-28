import hashlib
import json
import logging
import re
from datetime import date, datetime, timezone
from pathlib import Path

import httpx

log = logging.getLogger(__name__)

FACTS_PATH = Path(__file__).parent / "trivia_output.json"
ANCHOR = date(2026, 6, 1)
FINAL_UTC = datetime(2026, 7, 19, 19, 0, tzinfo=timezone.utc)
ZAFRONIX_BASE = "https://api.zafronix.com/fifa/worldcup/v1"

_MONTHS = [
    "January", "February", "March", "April", "May", "June",
    "July", "August", "September", "October", "November", "December",
]
_MIDX = {name: i + 1 for i, name in enumerate(_MONTHS)}
_DAY_MONTH = re.compile(r"(?<!\d)(\d{1,2})\s+(" + "|".join(_MONTHS) + r")")
_MONTH_DAY = re.compile(r"(" + "|".join(_MONTHS) + r")\s+(\d{1,2})(?!\d)")


def _month_day(text: str) -> str | None:
    m = _DAY_MONTH.search(text)
    if m:
        return f"{_MIDX[m.group(2)]:02d}-{int(m.group(1)):02d}"
    m = _MONTH_DAY.search(text)
    if m:
        return f"{_MIDX[m.group(1)]:02d}-{int(m.group(2)):02d}"
    return None


def load_facts(path: Path = FACTS_PATH) -> list[dict]:
    facts = json.loads(path.read_text(encoding="utf-8"))
    for fact in facts:
        fact["monthDay"] = _month_day(fact["fact"])
    return facts


def partition_facts(facts: list[dict]) -> tuple[list[dict], list[dict]]:
    dated = [f for f in facts if f["monthDay"]]
    undated = [f for f in facts if not f["monthDay"]]
    return dated, undated


def did_you_know(undated: list[dict], today: date, count: int = 1) -> list[dict]:
    ordered = sorted(undated, key=lambda f: hashlib.md5(f["id"].encode()).hexdigest())
    start = (today - ANCHOR).days % len(ordered)
    return [ordered[(start + i) % len(ordered)] for i in range(count)]


def on_this_day_from_file(dated: list[dict], today: date) -> list[dict]:
    key = today.strftime("%m-%d")
    hits = sorted(
        (f for f in dated if f["monthDay"] == key),
        key=lambda f: f["year"],
    )
    return [{"year": f["year"], "fact": f["fact"]} for f in hits[:3]]


def phrase_match(m: dict) -> str:
    home = m.get("homeTeam", "?")
    away = m.get("awayTeam", "?")
    score = (m.get("score") or "").replace("-", "–")
    stage = (m.get("stage") or "").replace("_", " ")
    city = m.get("city")
    bits = ", ".join(b for b in (stage, city) if b)
    detail = f" ({bits})" if bits else ""
    return f"{home} {score} {away}{detail}".strip()


async def on_this_day_from_api(today: date, api_key: str) -> list[dict]:
    url = f"{ZAFRONIX_BASE}/on-this-day"
    headers = {"X-API-Key": api_key}
    params = {"date": today.strftime("%m-%d")}
    async with httpx.AsyncClient(timeout=10.0) as client:
        resp = await client.get(url, headers=headers, params=params)
    resp.raise_for_status()
    data = resp.json()
    facts = data.get("facts") or []
    if facts:
        return [{"year": f.get("year"), "fact": f.get("fact", "")} for f in facts[:3]]
    matches = sorted(
        data.get("matches") or [],
        key=lambda m: m.get("year", 0),
        reverse=True,
    )[:3]
    return [{"year": m.get("year"), "fact": phrase_match(m)} for m in matches]


async def select_on_this_day(dated: list[dict], today: date, api_key: str) -> list[dict]:
    file_hits = on_this_day_from_file(dated, today)
    if file_hits:
        return file_hits
    if not api_key:
        return []
    try:
        return await on_this_day_from_api(today, api_key)
    except Exception as exc:
        log.warning("on-this-day API failed: %s", exc)
        return []


def minutes_to_final(now: datetime) -> int:
    return max(0, int((FINAL_UTC - now).total_seconds() // 60))


def compose_message(dyk: list[dict], otd: list[dict], now: datetime) -> str:
    lines = ["*Did You Know? ⚽*", ""]
    for fact in dyk:
        lines.append(f"💡 {fact['fact']}")
        lines.append("")
    for i, item in enumerate(otd):
        if i == 0:
            lines.append(f"🗓️ *On this exact day, {item['year']}:* {item['fact']}")
        else:
            lines.append(f"🗓️ *Also in {item['year']}:* {item['fact']}")
        lines.append("")
    mins = minutes_to_final(now)
    lines.append(
        f"⏱️ *{mins:,} minutes until the 2026 World Cup Final.* "
        "Don't forget to check how you're stacking up in "
        "<http://tinyurl.com/scopee-wc2026|Scopee's Challenge>"
    )
    return "\n".join(lines)


async def build_daily_post(now: datetime, api_key: str) -> str:
    dated, undated = partition_facts(load_facts())
    today = now.date()
    otd = await select_on_this_day(dated, today, api_key)
    dyk = did_you_know(undated, today, count=1 if otd else 2)
    return compose_message(dyk, otd, now)


async def post_to_slack(message: str, webhook_url: str) -> bool:
    async with httpx.AsyncClient(timeout=10.0) as client:
        resp = await client.post(webhook_url, json={"text": message})
    resp.raise_for_status()
    return True
