"""The title rules T1 (configured), T2 (folder) and T3 (file name)."""

from __future__ import annotations

import pytest

from paperstand.parsing.dates import build_month_table, is_date_component
from paperstand.parsing.normalize import squash
from paperstand.parsing.titles import (
    build_title_index,
    match_title,
    squash_articles,
    title_from_filename,
    title_from_folder,
)

ARTICLES = squash_articles(["il", "lo", "la", "le", "gli", "l", "the"])
MONTHS = build_month_table(["it", "en"])
INDEX = build_title_index(
    [
        ("Il Mattutino", []),
        ("Cronaca 24 Pagine", ["Cronaca 24 Pagine", "Cronaca24Pagine"]),
        ("Corriere del Ponte", []),
        ("La Gazzetta del Lago", []),
        ("La Gazzetta del Lago Valdora", []),
        ("W La Gazzetta del Lago", []),
        ("The Daily Ledger", []),
        ("L'Almanacco", []),
    ],
    ["il", "lo", "la", "le", "gli", "l", "the"],
)


def match(name: str) -> str | None:
    hit = match_title(squash(name), INDEX, ARTICLES, MONTHS.squashed_names)
    return hit.canonical if hit else None


@pytest.mark.parametrize(
    ("name", "expected"),
    [
        ("Corriere del Ponte", "Corriere del Ponte"),
        ("corriere_del_ponte", "Corriere del Ponte"),
        ("laGazzettadelLago", "La Gazzetta del Lago"),
        ("La Gazzetta del Lago Valdora", "La Gazzetta del Lago Valdora"),
        ("W La Gazzetta del Lago", "W La Gazzetta del Lago"),
        # An article on one side only, in both directions.
        ("Mattutino", "Il Mattutino"),
        ("Daily Ledger", "The Daily Ledger"),
        # An alias, and the same alias squashed differently.
        ("Cronaca 24 Pagine", "Cronaca 24 Pagine"),
        ("Cronaca24Pagine", "Cronaca 24 Pagine"),
        ("LAlmanacco", "L'Almanacco"),
        # A supplement, an insert and a regional edition are not the base title.
        ("La Gazzetta del Lago Sud", None),
        ("Corriere del Ponte Weekend", None),
        ("Something Else", None),
    ],
)
def test_configured_titles(name: str, expected: str | None) -> None:
    assert match(name) == expected


def test_the_longest_configured_name_wins() -> None:
    """``La Gazzetta del Lago Valdora`` must not be filed under ``La Gazzetta del Lago``."""
    keys = [entry.key for entry in INDEX]
    assert keys == sorted(keys, key=lambda key: (-len(key), key))
    assert match("La Gazzetta del Lago Valdora 18 Marzo") == "La Gazzetta del Lago Valdora"


@pytest.mark.parametrize(
    "boundary",
    ["Corriere del Ponte17", "Corriere del Ponte Marzo", "Corriere del Ponte n1"],
)
def test_accepted_boundaries(boundary: str) -> None:
    assert match(boundary) == "Corriere del Ponte"


def test_the_match_reports_where_the_name_ends() -> None:
    hit = match_title(squash("Cronaca 24 Pagine 36"), INDEX, ARTICLES, MONTHS.squashed_names)
    assert hit is not None
    assert hit.end == len("Cronaca 24 Pagine")


@pytest.mark.parametrize(
    ("components", "expected"),
    [
        (("Circuito",), "Circuito"),
        (("2026", "Orizzonte", "03"), "Orizzonte"),
        (("2026", "03", "17"), None),
        ((), None),
        (("Random_Mag",), "Random Mag"),
    ],
)
def test_folder_titles(components: tuple[str, ...], expected: str | None) -> None:
    assert title_from_folder(components, lambda name: is_date_component(name, MONTHS)) == expected


@pytest.mark.parametrize(
    ("spaced", "cut", "expected"),
    [
        ("Corriere del Ponte 17 Marzo 2026", 19, "Corriere del Ponte"),
        ("Orizzonte Kids - Marzo 2026", 17, "Orizzonte Kids"),
        ("random mag March 2026", 11, "Random Mag"),
        ("Something", None, "Something"),
    ],
)
def test_filename_titles(spaced: str, cut: int | None, expected: str) -> None:
    assert title_from_filename(spaced, cut, ["del", "della", "the"]) == expected
