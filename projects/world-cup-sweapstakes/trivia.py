import hashlib
import json
import logging
import re
from datetime import date, datetime, timezone
from pathlib import Path

log = logging.getLogger(__name__)

FACTS_PATH = Path(__file__).parent / "trivia_output.json"
ANCHOR = date(2026, 6, 1)
FINAL_UTC = datetime(2026, 7, 19, 19, 0, tzinfo=timezone.utc)
SCOPEE_LINK = "<http://tinyurl.com/scopee-wc2026|Scopee's Challenge>"

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


def days_to_final(now: datetime) -> int:
    return max(0, (FINAL_UTC - now).days)


def trivia_blocks(now: datetime) -> tuple[list[dict], str]:
    _, undated = partition_facts(load_facts())
    fact = did_you_know(undated, now.date(), count=1)[0]
    days = days_to_final(now)
    blocks = [
        {"type": "section", "text": {"type": "mrkdwn", "text": "*Did You Know? ⚽*"}},
        {"type": "section", "text": {"type": "mrkdwn", "text": f"💡 {fact['fact']}"}},
        {"type": "section", "text": {"type": "mrkdwn", "text": (
            f"📅 *{days:,} days until the 2026 World Cup Final.* "
            f"See how you're stacking up in {SCOPEE_LINK} ›"
        )}},
    ]
    fallback = f"Did You Know? ⚽ {fact['fact']}"
    return blocks, fallback
