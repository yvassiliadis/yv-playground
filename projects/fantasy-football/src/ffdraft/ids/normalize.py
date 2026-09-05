"""Name and team normalization for player-ID matching.

`normalize_name` gives sources without native player IDs a stable key to
match individual players by name. `normalize_dst` does the same for
defense/special-teams entries, which every source spells differently
("49ers D/ST", "SF Defense", "San Francisco 49ers") but which all really
mean one of the 32 NFL teams.
"""

from __future__ import annotations

import re

_SUFFIX_TOKENS = {"jr", "sr", "ii", "iii", "iv"}

# strip periods/apostrophes (and the unicode right-quote) outright, then turn
# any other non-alphanumeric character into a space so hyphens, slashes, etc.
# become word breaks instead of being glued together.
_DROP_CHARS_RE = re.compile(r"[.'’]")
_NON_ALNUM_RE = re.compile(r"[^a-z0-9\s]")

# the "D/ST" marker, in its "d/st", "d st", and solid "dst" spellings, as a
# single unit -- matched before generic non-alnum splitting so it doesn't get
# confused with a real "St" as in "St Louis".
_DST_MARKER_RE = re.compile(r"\bd\s*/\s*st\b|\bdst\b")

# other DST-only noise words that show up alongside a team name/nickname and
# carry no team-identifying information themselves.
_DST_NOISE_TOKENS = {"defense", "def"}


def _tokenize(text: str) -> list[str]:
    cleaned = _DROP_CHARS_RE.sub("", text.lower())
    cleaned = _NON_ALNUM_RE.sub(" ", cleaned)
    return [tok for tok in cleaned.split() if tok]


def normalize_name(name: str) -> str:
    """Normalize a player name into a stable matching key.

    Lowercases, strips periods/apostrophes, drops a trailing generational
    suffix (Jr, Sr, II, III, IV), and collapses whitespace. Two spellings of
    the same person -- "Odell Beckham Jr." and "Odell Beckham" -- normalize
    to the same key.
    """
    tokens = _tokenize(name)
    while tokens and tokens[-1] in _SUFFIX_TOKENS:
        tokens.pop()
    return " ".join(tokens)


def _clean_key(text: str) -> str:
    return " ".join(_tokenize(text))


# (city, nickname) for each of the 32 current franchises, keyed by the team
# abbreviation used elsewhere in this pipeline (matches the `team` column
# nflreadpy returns from load_player_stats/load_team_stats -- notably "LA"
# for the Rams, not "LAR").
_TEAM_DATA: dict[str, tuple[str, str]] = {
    "ARI": ("Arizona", "Cardinals"),
    "ATL": ("Atlanta", "Falcons"),
    "BAL": ("Baltimore", "Ravens"),
    "BUF": ("Buffalo", "Bills"),
    "CAR": ("Carolina", "Panthers"),
    "CHI": ("Chicago", "Bears"),
    "CIN": ("Cincinnati", "Bengals"),
    "CLE": ("Cleveland", "Browns"),
    "DAL": ("Dallas", "Cowboys"),
    "DEN": ("Denver", "Broncos"),
    "DET": ("Detroit", "Lions"),
    "GB": ("Green Bay", "Packers"),
    "HOU": ("Houston", "Texans"),
    "IND": ("Indianapolis", "Colts"),
    "JAX": ("Jacksonville", "Jaguars"),
    "KC": ("Kansas City", "Chiefs"),
    "LA": ("Los Angeles", "Rams"),
    "LAC": ("Los Angeles", "Chargers"),
    "LV": ("Las Vegas", "Raiders"),
    "MIA": ("Miami", "Dolphins"),
    "MIN": ("Minnesota", "Vikings"),
    "NE": ("New England", "Patriots"),
    "NO": ("New Orleans", "Saints"),
    "NYG": ("New York", "Giants"),
    "NYJ": ("New York", "Jets"),
    "PHI": ("Philadelphia", "Eagles"),
    "PIT": ("Pittsburgh", "Steelers"),
    "SEA": ("Seattle", "Seahawks"),
    "SF": ("San Francisco", "49ers"),
    "TB": ("Tampa Bay", "Buccaneers"),
    "TEN": ("Tennessee", "Titans"),
    "WAS": ("Washington", "Commanders"),
}

# historical/alternate franchise names and city-only abbreviations that
# should still resolve to the current team key above.
_HISTORICAL_ALIASES: dict[str, str] = {
    "LAR": "LA",
    "STL": "LA",
    "St Louis": "LA",
    "St Louis Rams": "LA",
    "San Diego": "LAC",
    "San Diego Chargers": "LAC",
    "SD": "LAC",
    "Oakland": "LV",
    "Oakland Raiders": "LV",
    "OAK": "LV",
    "Washington Redskins": "WAS",
    "Washington Football Team": "WAS",
    "Redskins": "WAS",
}


def _build_team_aliases() -> dict[str, str]:
    aliases: dict[str, str] = {}
    for abbr, (city, nick) in _TEAM_DATA.items():
        aliases[_clean_key(abbr)] = abbr
        aliases[_clean_key(city)] = abbr
        aliases[_clean_key(nick)] = abbr
        aliases[_clean_key(f"{city} {nick}")] = abbr
    for alias, abbr in _HISTORICAL_ALIASES.items():
        aliases[_clean_key(alias)] = abbr
    return aliases


TEAM_ALIASES: dict[str, str] = _build_team_aliases()


def normalize_dst(name_or_team: str) -> str:
    """Normalize a D/ST name or team string to a canonical team abbreviation.

    Handles the common spelling variants -- "49ers D/ST", "SF Defense",
    "San Francisco 49ers", the bare abbreviation "SF" -- by stripping DST
    noise words and looking the remainder up in `TEAM_ALIASES`.

    Raises `ValueError` if the input doesn't resolve to a known team; unlike
    `resolve_by_name`, a DST input is expected to always name a real team, so
    a failure here signals a data problem worth surfacing immediately rather
    than a genuine "no match" case to route to an unmatched report.
    """
    without_marker = _DST_MARKER_RE.sub(" ", name_or_team.lower())
    tokens = [tok for tok in _tokenize(without_marker) if tok not in _DST_NOISE_TOKENS]
    key = " ".join(tokens)
    if key not in TEAM_ALIASES:
        raise ValueError(f"unrecognized team/DST name: {name_or_team!r}")
    return TEAM_ALIASES[key]
