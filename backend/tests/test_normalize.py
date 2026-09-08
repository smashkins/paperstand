"""Stem cleaning, the spaced and squashed forms, and the title casing."""

from __future__ import annotations

import re

import pytest

from paperstand.parsing.normalize import (
    clean_stem,
    mask,
    smart_title_case,
    squash,
    strip_accents,
    trim_separators,
)
from paperstand.parsing.profile import Profile, load_profiles


def default_profile() -> Profile:
    return load_profiles()["default"]


def clean(stem: str) -> tuple[str, bool]:
    profile = default_profile()
    result = clean_stem(
        stem,
        [re.compile(pattern) for pattern in profile.strip],
        [(re.compile(pattern), value) for pattern, value in profile.replace],
    )
    return result.spaced, result.has_dedup_suffix


@pytest.mark.parametrize(
    ("stem", "spaced"),
    [
        ("4820117639_258259_Corriere_del_Ponte", "Corriere del Ponte"),
        ("@handle_Il_Mattutino", "Il Mattutino"),
        ("@handle Il Mattutino", "Il Mattutino"),
        ("Confini__Febbraio_2026", "Confini Febbraio 2026"),
        # Dashes and apostrophes come in every shape a keyboard can produce.
        ("OPQ_N.4_–_6-19_Marzo", "OPQ N.4 - 6-19 Marzo"),  # noqa: RUF001
        ("L’Almanacco_36", "L'Almanacco 36"),  # noqa: RUF001
    ],
)
def test_stem_is_cleaned(stem: str, spaced: str) -> None:
    assert clean(stem)[0] == spaced


@pytest.mark.parametrize(
    ("stem", "expected"),
    [
        ("Corriere_del_Ponte_17_Marzo_2026-1", True),
        ("Corriere_del_Ponte_17_Marzo_2026_(1)", True),
        ("Corriere_del_Ponte_17_Marzo_2026 (2)", True),
        ("Corriere_del_Ponte_17_Marzo_2026", False),
        # A month, not a copy counter: the date has to survive intact.
        ("Circuito_2026-03", False),
        ("Corriere_del_Ponte_2026-03-17", False),
    ],
)
def test_dedup_suffix_detection(stem: str, expected: bool) -> None:
    spaced, dedup = clean(stem)
    assert dedup is expected
    if not expected:
        assert spaced.endswith(("2026", "2026-03", "2026-03-17"))


def test_squashed_form_and_offsets() -> None:
    squashed = squash("La Gazzetta del Lago 30")
    assert squashed.text == "lagazzettadellago30"
    # The map points back into the source string, which is what lets a title
    # match on the squashed form be cut out of the spaced one.
    assert squashed.source_end(len("lagazzettadellago")) == len("La Gazzetta del Lago")


def test_squash_removes_accents_and_punctuation() -> None:
    assert squash("L'Almanacco — Città").text == "lalmanaccocitta"
    assert strip_accents("città") == "citta"


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("corriere del ponte", "Corriere del Ponte"),
        ("OPQ The Other Orizzonte", "OPQ The Other Orizzonte"),
        ("TDL", "TDL"),
        ("random mag", "Random Mag"),
        ("della sera", "Della Sera"),
    ],
)
def test_smart_title_case(text: str, expected: str) -> None:
    assert smart_title_case(text, ["del", "della", "the", "of"]) == expected


def test_trim_separators() -> None:
    assert trim_separators(" - Orizzonte Kids - ") == "Orizzonte Kids"


def test_mask_preserves_indices() -> None:
    assert mask("Corriere 17 Marzo", [(9, 17)]) == "Corriere         "
