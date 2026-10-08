SHARE_CLASS_ALIASES = {
    "GOOGL": "GOOG",
    "GOOG": "GOOG",
    "BRK-B": "BRK.B",
    "BRK.B": "BRK.B",
}


def canonical_ticker(t: str) -> str:
    t = t.upper()
    return SHARE_CLASS_ALIASES.get(t, t)
