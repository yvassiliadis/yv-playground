from datetime import datetime
from zoneinfo import ZoneInfo

ET = ZoneInfo("America/New_York")

# Canonical + football-data.org name variants → ISO 3166-1 alpha-2.
_TEAM_ISO2: dict[str, str] = {
    "Spain": "ES", "France": "FR", "Portugal": "PT", "Brazil": "BR",
    "Argentina": "AR", "Germany": "DE", "Netherlands": "NL", "Norway": "NO",
    "Belgium": "BE", "Colombia": "CO", "Morocco": "MA", "Mexico": "MX",
    "Japan": "JP", "Switzerland": "CH", "USA": "US", "United States": "US",
    "Uruguay": "UY", "Ecuador": "EC", "Türkiye": "TR", "Turkey": "TR",
    "Croatia": "HR", "Senegal": "SN", "Austria": "AT", "Sweden": "SE",
    "Côte d'Ivoire": "CI", "Ivory Coast": "CI", "Canada": "CA", "Czechia": "CZ",
    "Paraguay": "PY", "Korea Republic": "KR", "South Korea": "KR",
    "Australia": "AU", "Algeria": "DZ", "Egypt": "EG",
    "Bosnia and Herzegovina": "BA", "Bosnia & Herzegovina": "BA",
    "Ghana": "GH", "Tunisia": "TN", "Iran": "IR", "South Africa": "ZA",
    "Congo DR": "CD", "DR Congo": "CD", "Qatar": "QA", "Saudi Arabia": "SA",
    "Cabo Verde": "CV", "Cape Verde": "CV", "Iraq": "IQ", "Panama": "PA",
    "Uzbekistan": "UZ", "New Zealand": "NZ", "Jordan": "JO", "Curaçao": "CW",
    "Haiti": "HT",
}

# Non-ISO subdivisions that still have flag emoji.
_SPECIAL_FLAGS: dict[str, str] = {
    "England": "🏴\U000e0067\U000e0062\U000e0065\U000e006e\U000e0067\U000e007f",
    "Scotland": "🏴\U000e0067\U000e0062\U000e0073\U000e0063\U000e0074\U000e007f",
    "Wales": "🏴\U000e0067\U000e0062\U000e0077\U000e006c\U000e0073\U000e007f",
}


def flag(name: str) -> str:
    if name in _SPECIAL_FLAGS:
        return _SPECIAL_FLAGS[name]
    iso2 = _TEAM_ISO2.get(name)
    if not iso2:
        return ""
    return "".join(chr(0x1F1E6 + ord(c) - ord("A")) for c in iso2)


def todays_fixtures(matches: list[dict], now: datetime) -> list[dict]:
    today = now.astimezone(ET).date()
    out: list[dict] = []
    for m in matches:
        utc = m.get("utcDate")
        if not utc:
            continue
        try:
            kickoff = datetime.fromisoformat(utc.replace("Z", "+00:00"))
        except ValueError:
            continue
        if kickoff.astimezone(ET).date() != today:
            continue
        out.append({
            "game_id": str(m.get("id")),
            "home": (m.get("homeTeam") or {}).get("name") or "?",
            "away": (m.get("awayTeam") or {}).get("name") or "?",
            "kickoff_utc": utc,
            "stage": m.get("stage") or "",
        })
    out.sort(key=lambda g: g["kickoff_utc"])
    return out
