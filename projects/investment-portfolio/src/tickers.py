SHARE_CLASS_ALIASES = {
    "GOOGL": "GOOG",
    "GOOG": "GOOG",
    "BRK-B": "BRK.B",
    "BRK.B": "BRK.B",
}


def canonical_ticker(t: str) -> str:
    t = t.upper()
    return SHARE_CLASS_ALIASES.get(t, t)


def alternate_spellings(t: str) -> list[str]:
    """Returns the other known ticker spellings that are the same company as `t`.

    e.g. "BRK.B" -> ["BRK-B"], "GOOGL" -> ["GOOG"]. Only covers the known
    alias pairs in SHARE_CLASS_ALIASES — never generalizes to arbitrary tickers.
    """
    t = t.upper()
    canon = SHARE_CLASS_ALIASES.get(t)
    if canon is None:
        return []
    return [spelling for spelling in SHARE_CLASS_ALIASES if spelling != t and SHARE_CLASS_ALIASES[spelling] == canon]
