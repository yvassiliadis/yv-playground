import asyncio
from datetime import date

import trivia


def test_month_day_parses_day_then_month():
    assert trivia._month_day("on 27 June 1954 something") == "06-27"


def test_month_day_parses_month_then_day():
    assert trivia._month_day("scored on July 13, 1930") == "07-13"


def test_month_day_returns_none_without_date():
    assert trivia._month_day("no calendar date in here") is None


def test_load_facts_adds_month_day_to_every_fact():
    facts = trivia.load_facts()
    assert len(facts) == 98
    assert all("monthDay" in f for f in facts)


def test_partition_splits_dated_and_undated():
    facts = trivia.load_facts()
    dated, undated = trivia.partition_facts(facts)
    assert len(dated) == 19
    assert len(undated) == 79
    assert all(f["monthDay"] for f in dated)
    assert all(f["monthDay"] is None for f in undated)


def test_did_you_know_is_deterministic_for_a_date():
    _, undated = trivia.partition_facts(trivia.load_facts())
    a = trivia.did_you_know(undated, date(2026, 6, 27))
    b = trivia.did_you_know(undated, date(2026, 6, 27))
    assert a[0]["id"] == b[0]["id"]


def test_did_you_know_known_pick_for_june_27():
    _, undated = trivia.partition_facts(trivia.load_facts())
    pick = trivia.did_you_know(undated, date(2026, 6, 27))
    assert pick[0]["id"] == "1954-most-goals-per-match"


def test_did_you_know_count_returns_distinct_facts():
    _, undated = trivia.partition_facts(trivia.load_facts())
    picks = trivia.did_you_know(undated, date(2026, 6, 27), count=2)
    assert len(picks) == 2
    assert picks[0]["id"] != picks[1]["id"]


def test_on_this_day_from_file_june_27_is_battle_of_bern():
    dated, _ = trivia.partition_facts(trivia.load_facts())
    hits = trivia.on_this_day_from_file(dated, date(2026, 6, 27))
    assert len(hits) == 1
    assert hits[0]["year"] == 1954
    assert "Battle of Bern" in hits[0]["fact"]


def test_on_this_day_from_file_empty_when_no_match():
    dated, _ = trivia.partition_facts(trivia.load_facts())
    # Jan 1 has no dated fact in the curated set
    assert trivia.on_this_day_from_file(dated, date(2026, 1, 1)) == []


def test_phrase_match_formats_result_with_endash():
    m = {
        "homeTeam": "Sweden", "awayTeam": "Italy", "score": "3-2",
        "stage": "group_3", "city": "São Paulo",
    }
    assert trivia.phrase_match(m) == "Sweden 3–2 Italy (group 3, São Paulo)"


def test_select_on_this_day_prefers_file_and_skips_api():
    dated, _ = trivia.partition_facts(trivia.load_facts())
    # api_key intentionally bogus; file match for 06-27 means API is never called
    hits = asyncio.run(trivia.select_on_this_day(dated, date(2026, 6, 27), "bogus"))
    assert len(hits) == 1
    assert "Battle of Bern" in hits[0]["fact"]


def test_select_on_this_day_no_key_no_file_returns_empty():
    dated, _ = trivia.partition_facts(trivia.load_facts())
    hits = asyncio.run(trivia.select_on_this_day(dated, date(2026, 1, 1), ""))
    assert hits == []
