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
    assert len(dated) == 21
    assert len(undated) == 77
    assert all(f["monthDay"] for f in dated)
    assert all(f["monthDay"] is None for f in undated)
