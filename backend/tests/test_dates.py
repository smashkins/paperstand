"""The date rules D1-D7, the month tables and the folder fallback."""

from __future__ import annotations

import pytest

from paperstand.parsing.dates import (
    DATE_RULE_IDS,
    build_month_table,
    compile_date_rules,
    find_date,
    folder_date,
    is_date_component,
    to_date,
)

MONTHS = build_month_table(["it", "en"])
RULES = compile_date_rules(MONTHS, DATE_RULE_IDS)
YEARS = (1900, 2099)


def parse(text: str) -> tuple[str, str | None]:
    """``(rule, ISO date or None)`` for a piece of text."""
    hit = find_date(text, RULES, MONTHS, YEARS)
    if hit is None:
        return "none", None
    date = to_date(hit)
    return hit.rule, date.isoformat() if date else None


@pytest.mark.parametrize(
    ("text", "rule", "date"),
    [
        ("Corriere del Ponte 2026-03-17", "D1", "2026-03-17"),
        ("Corriere del Ponte 17-03-2026", "D2", "2026-03-17"),
        ("Corriere del Ponte 17.03.2026", "D2", "2026-03-17"),
        ("Corriere del Ponte 17 Marzo 2026", "D3", "2026-03-17"),
        ("laGazzettadelLago30Agosto2026", "D3", "2026-08-30"),
        ("Cronaca 24 Pagine 23Agosto 2026", "D3", "2026-08-23"),
        ("The Daily Ledger March 17 2026", "D4", "2026-03-17"),
        ("Circuito 2026-03", "D5", "2026-03-01"),
        ("Orizzonte Kids - Marzo 2026", "D6", "2026-03-01"),
        ("Confini n. 8 2026", "D7", "2026-01-01"),
        ("Something", "none", None),
    ],
)
def test_first_matching_rule_wins(text: str, rule: str, date: str | None) -> None:
    assert parse(text) == (rule, date)


def test_a_missing_year_is_left_open() -> None:
    hit = find_date("Corriere del Ponte 17 Marzo", RULES, MONTHS, YEARS)
    assert hit is not None
    assert (hit.rule, hit.day, hit.month, hit.year) == ("D3", 17, 3, None)
    assert to_date(hit) is None


def test_a_day_range_keeps_its_start() -> None:
    hit = find_date("OPQ N.4 - 6-19 Marzo 2026", RULES, MONTHS, YEARS)
    assert hit is not None
    assert (hit.day, hit.day_end, hit.month, hit.year) == (6, 19, 3, 2026)


def test_an_impossible_date_is_not_a_date() -> None:
    assert parse("Something 2026-02-30")[0] != "D1"


def test_a_month_abbreviation_alone_is_not_a_date() -> None:
    """``Mag`` is an Italian abbreviation and an English word: too ambiguous."""
    assert parse("Random Mag March 2026") == ("D6", "2026-03-01")


def test_month_names_are_case_and_accent_insensitive() -> None:
    assert parse("Orizzonte 1630 - 5 settembre 2026") == ("D3", "2026-09-05")
    table = build_month_table(["fr", "pt"])
    assert table.numbers["fevrier"] == 2
    assert table.numbers["março".replace("ç", "c")] == 3


def test_custom_month_table() -> None:
    table = build_month_table(["xx"], {"xx": {"primo": 1, "secondo": 2}})
    rules = compile_date_rules(table, ["D6"])
    hit = find_date("Rivista Secondo 2026", rules, table, YEARS)
    assert hit is not None
    assert (hit.month, hit.year) == (2, 2026)


@pytest.mark.parametrize(
    ("components", "date", "precision"),
    [
        (("2026", "03", "17"), "2026-03-17", "day"),
        (("2026", "Orizzonte", "03"), "2026-03-01", "month"),
        (("2026",), "2026-01-01", "year"),
        (("Orizzonte",), None, None),
    ],
)
def test_folder_fallback(
    components: tuple[str, ...], date: str | None, precision: str | None
) -> None:
    hit = folder_date(components)
    if date is None:
        assert hit is None
        return
    assert hit is not None
    assert hit.precision == precision
    found = to_date(hit)
    assert found is not None
    assert found.isoformat() == date


@pytest.mark.parametrize(
    ("component", "expected"),
    [("2026", True), ("03", True), ("17", True), ("Marzo", True), ("Orizzonte", False)],
)
def test_date_components(component: str, expected: bool) -> None:
    assert is_date_component(component, MONTHS) is expected


@pytest.mark.parametrize(
    ("text", "rule", "date"),
    [
        # The profile turns "Title_17_03_2026" into spaces before the rules run.
        ("Corriere del Ponte 17 03 2026", "D2", "2026-03-17"),
        ("Corriere del Ponte 2026 03 17", "D1", "2026-03-17"),
        ("Corriere del Ponte 17/03/2026", "D2", "2026-03-17"),
    ],
)
def test_numeric_dates_accept_the_normalised_separator(text: str, rule: str, date: str) -> None:
    assert parse(text) == (rule, date)


def test_the_separator_must_be_the_same_on_both_sides() -> None:
    assert parse("Corriere del Ponte 17-03 2026") == ("D7", "2026-01-01")


def test_a_year_outside_the_range_is_refused() -> None:
    hit = find_date("Title 17 March 1750", RULES, MONTHS, (1900, 2099))
    assert hit is not None
    # D3 keeps the day and the month and drops the year it cannot accept.
    assert (hit.day, hit.month, hit.year) == (17, 3, None)
    inside = find_date("Title 17 March 1750", RULES, MONTHS, (1700, 1799))
    assert inside is not None
    assert (inside.day, inside.month, inside.year) == (17, 3, 1750)


def test_an_impossible_range_end_rejects_the_match() -> None:
    hit = find_date("Weekly 6-40 March 2026", RULES, MONTHS, YEARS)
    assert hit is not None
    assert hit.rule == "D6"  # not a range: only the month is left
    assert hit.day is None


def test_a_range_ending_before_it_starts_rejects_the_match() -> None:
    hit = find_date("Weekly 19-6 March 2026", RULES, MONTHS, YEARS)
    assert hit is not None
    assert hit.rule == "D6"


def test_the_folder_fallback_honours_the_year_range() -> None:
    assert folder_date(("1990", "03"), (2000, 2099)) is None
    hit = folder_date(("1990", "03"), (1900, 2099))
    assert hit is not None
    assert hit.year == 1990
