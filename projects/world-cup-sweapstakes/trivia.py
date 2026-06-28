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
_DAY_MONTH = re.compile(r"(\d{1,2})\s+(" + "|".join(_MONTHS) + r")")
_MONTH_DAY = re.compile(r"(" + "|".join(_MONTHS) + r")\s+(\d{1,2})")


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
