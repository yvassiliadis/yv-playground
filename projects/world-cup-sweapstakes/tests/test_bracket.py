import server


def _round(bracket: dict, round_id: str) -> dict:
    return next(r for r in bracket["rounds"] if r["id"] == round_id)


def test_build_bracket_threads_rounds_by_ref():
    # QF5 is fed by R16 matches 1 & 3, QF6 by 2 & 4. Sorting R16 by matchNo
    # (1,2,3,4) mis-threads the tree; the feeder-threaded order is 1,3,2,4.
    stages = {
        "round_of_16": [
            {"matchNo": 1, "matchId": "m1", "homeRef": "1A", "awayRef": "2B", "home": "Brazil", "away": "Japan"},
            {"matchNo": 2, "matchId": "m2", "homeRef": "1C", "awayRef": "2D", "home": "France", "away": "Sweden"},
            {"matchNo": 3, "matchId": "m3", "homeRef": "1E", "awayRef": "2F", "home": "Spain", "away": "Austria"},
            {"matchNo": 4, "matchId": "m4", "homeRef": "1G", "awayRef": "2H", "home": "Germany", "away": "Paraguay"},
        ],
        "quarter_final": [
            {"matchNo": 5, "matchId": "m5", "homeRef": "W1", "awayRef": "W3", "home": None, "away": None},
            {"matchNo": 6, "matchId": "m6", "homeRef": "W2", "awayRef": "W4", "home": None, "away": None},
        ],
        "final": [
            {"matchNo": 7, "matchId": "m7", "homeRef": "W5", "awayRef": "W6", "home": None, "away": None},
        ],
    }
    bracket = server._build_bracket({"stages": stages}, [])
    r16 = _round(bracket, "r16")
    assert [m["home"] for m in r16["matches"]] == ["Brazil", "Spain", "France", "Germany"]


def test_build_bracket_falls_back_to_matchno_without_final():
    # No final stage -> tree can't be rooted -> stable matchNo order.
    stages = {
        "round_of_16": [
            {"matchNo": 2, "matchId": "m2", "homeRef": "1C", "awayRef": "2D", "home": "France", "away": "Sweden"},
            {"matchNo": 1, "matchId": "m1", "homeRef": "1A", "awayRef": "2B", "home": "Brazil", "away": "Japan"},
        ],
    }
    bracket = server._build_bracket({"stages": stages}, [])
    r16 = _round(bracket, "r16")
    assert [m["home"] for m in r16["matches"]] == ["Brazil", "France"]


def test_build_bracket_applies_belgium_senegal_correction():
    # Zafronix swaps Algeria/Senegal between matches 82 and 85.
    stages = {
        "round_of_32": [
            {"matchNo": 82, "matchId": "2026-082", "homeRef": "1G", "awayRef": "3AEHIJ", "home": "Belgium", "away": "Algeria"},
            {"matchNo": 85, "matchId": "2026-085", "homeRef": "1B", "awayRef": "3EFGIJ", "home": "Switzerland", "away": "Senegal"},
        ],
    }
    bracket = server._build_bracket({"stages": stages}, [])
    r32 = _round(bracket, "r32")
    pairings = {m["home"]: m["away"] for m in r32["matches"]}
    assert pairings["Belgium"] == "Senegal"
    assert pairings["Switzerland"] == "Algeria"
