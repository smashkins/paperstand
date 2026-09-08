"""The issue number rules N1 and N2."""

from __future__ import annotations

import pytest

from paperstand.parsing.numbers import compile_number_rules, find_number

RULES = compile_number_rules(["numero", "num", "issue", "nr", "no", "n", "#"])


def number(text: str, *, bare: bool = True, bare_from: int = 0) -> tuple[str, int] | None:
    hit = find_number(text, RULES, allow_bare=bare, bare_from=bare_from)
    return (hit.rule, hit.value) if hit else None


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("Orizzonte N.1655", ("N1", 1655)),
        ("Confini n. 8", ("N1", 8)),
        ("Confini N 1", ("N1", 1)),
        ("Circuito #12", ("N1", 12)),
        ("Rivista numero 3", ("N1", 3)),
        ("Rivista issue 45", ("N1", 45)),
        ("Orizzonte 1630 -", ("N2", 1630)),
        ("L'Almanacco 36", ("N2", 36)),
        ("Orizzonte -", None),
    ],
)
def test_number_rules(text: str, expected: tuple[str, int] | None) -> None:
    assert number(text) == expected


def test_a_year_is_never_an_issue_number() -> None:
    assert number("Something 2026") is None


def test_bare_numbers_can_be_switched_off() -> None:
    """This is what protects a daily whose title carries a number."""
    assert number("Cronaca 24 Pagine", bare=False) is None
    assert number("Cronaca 24 Pagine", bare=True) == ("N2", 24)


def test_bare_search_starts_after_the_title() -> None:
    text = "Cronaca 24 Pagine 36"
    assert number(text, bare_from=len("Cronaca 24 Pagine")) == ("N2", 36)


def test_an_introducing_token_inside_a_word_is_not_one() -> None:
    assert number("Orizzonte Kids", bare=False) is None


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        # N2 is a standalone integer: digits glued to letters are part of a word.
        ("Formula1", None),
        ("Formula1 -", None),
        ("1Formula", None),
        ("Formula 1", ("N2", 1)),
    ],
)
def test_bare_numbers_are_standalone(text: str, expected: tuple[str, int] | None) -> None:
    assert number(text) == expected
