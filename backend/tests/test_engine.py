"""The fixture table, the custom profile, ``extends: default`` and the budget."""

from __future__ import annotations

import time
from typing import Any

import pytest
import yaml

from paperstand.config import PaperstandConfig
from paperstand.parsing import ParsedIssue, parse_path
from tests.fixtures.filenames import (
    CONFIGURED,
    DISCOVERED,
    MTIME,
    Expected,
    discovered_config,
    example_config,
    example_config_path,
)


def parse(expected: Expected, config: PaperstandConfig) -> ParsedIssue:
    """Parse one fixture row with the library it belongs to."""
    library = config.library_for(expected.rel_path)
    assert library is not None, f"no library for {expected.rel_path}"
    return parse_path(expected.rel_path, library, config.profile_for(library), MTIME)


def assert_matches(issue: ParsedIssue, expected: Expected) -> None:
    assert issue.title_name == expected.title
    assert issue.title_source == expected.title_source
    assert issue.derived_title == expected.derived
    assert issue.issue_date == expected.issue_date
    assert issue.date_precision == expected.precision
    assert issue.date_source == expected.source
    assert issue.issue_number == expected.number
    assert issue.has_dedup_suffix is expected.dedup
    assert issue.matched_rule == expected.rule


@pytest.fixture(scope="module")
def configured() -> PaperstandConfig:
    return example_config()


@pytest.fixture(scope="module")
def discovered() -> PaperstandConfig:
    return discovered_config()


@pytest.mark.parametrize("expected", CONFIGURED, ids=lambda row: row.id)
def test_configured_names(expected: Expected, configured: PaperstandConfig) -> None:
    assert_matches(parse(expected, configured), expected)


@pytest.mark.parametrize("expected", DISCOVERED, ids=lambda row: row.id)
def test_discovered_names(expected: Expected, discovered: PaperstandConfig) -> None:
    assert_matches(parse(expected, discovered), expected)


def test_labels_read_as_english(configured: PaperstandConfig) -> None:
    labels = {
        "Newspapers/2026/03/17/Corriere_del_Ponte_17_Marzo_2026.pdf": "17 March 2026",
        "Magazines/2026/Orizzonte/03/Orizzonte_N.1655_-_6_Marzo_2026.pdf": (
            "No. 1655 · 6 March 2026"
        ),
        "Magazines/2026/Confini/03/Confini__Marzo_2026.pdf": "March 2026",
        "Magazines/Confini/Confini_n._8_2026.pdf": "No. 8 · 2026",
    }
    for rel_path, label in labels.items():
        library = configured.library_for(rel_path)
        assert library is not None
        issue = parse_path(rel_path, library, configured.profile_for(library), MTIME)
        assert issue.label == label


# --------------------------------------------------------------- custom profile

CUSTOM_PATTERN = r"^(?P<title>[A-Z]{3})[_-](?P<date>\d{8}|\d{4}-\d{2}-\d{2})$"


def custom_config() -> PaperstandConfig:
    """A profile written from scratch: one pattern, two aliased short codes."""
    return PaperstandConfig.model_validate(
        {
            "parsers": {"codes": {"patterns": [CUSTOM_PATTERN]}},
            "libraries": [
                {
                    "name": "Codes",
                    "path": "Codes",
                    "kind": "newspaper",
                    "parser": "codes",
                    "titles": [
                        {"name": "Alpha Daily", "aliases": ["ABC"]},
                        {"name": "Zeta Times", "aliases": ["XYZ"]},
                    ],
                }
            ],
        }
    )


@pytest.mark.parametrize(
    ("name", "title"),
    [("ABC_20260317.pdf", "Alpha Daily"), ("XYZ-2026-03-17.pdf", "Zeta Times")],
)
def test_custom_profile_short_codes(name: str, title: str) -> None:
    config = custom_config()
    rel_path = f"Codes/{name}"
    library = config.library_for(rel_path)
    assert library is not None
    issue = parse_path(rel_path, library, config.profile_for(library), MTIME)
    assert issue.title_name == title
    assert issue.issue_date is not None
    assert issue.issue_date.isoformat() == "2026-03-17"
    assert issue.date_precision == "day"
    assert issue.date_source == "filename"
    assert issue.matched_rule == "pattern[0] title:config"


def test_custom_profile_is_empty_without_extends() -> None:
    """A profile with no ``extends`` inherits nothing from the bundled one."""
    profile = custom_config().profiles()["codes"]
    assert profile.strip == []
    assert profile.replace == []
    assert profile.languages == []
    assert profile.patterns == [CUSTOM_PATTERN]


# -------------------------------------------------------------- extends: default


def extended_config() -> PaperstandConfig:
    """The example configuration, with a profile extending ``default``."""
    raw: dict[str, Any] = yaml.safe_load(example_config_path().read_text("utf-8"))
    raw["parsers"] = {"extended": {"extends": "default", "strip": ["+", r"^copy[_ ]"]}}
    for library in raw["libraries"]:
        library["parser"] = "extended"
    return PaperstandConfig.model_validate(raw)


EXTENDED = extended_config()


@pytest.mark.parametrize("expected", CONFIGURED, ids=lambda row: row.id)
def test_extends_default_passes_the_whole_table(expected: Expected) -> None:
    assert_matches(parse(expected, EXTENDED), expected)


def test_extends_appends_rather_than_replaces() -> None:
    profile = EXTENDED.profiles()["extended"]
    default = EXTENDED.profiles()["default"]
    assert profile.strip == [*default.strip, r"^copy[_ ]"]
    assert profile.languages == default.languages


def test_extra_strip_rule_applies() -> None:
    rel_path = "Newspapers/2026/03/17/copy_Corriere_del_Ponte_17_Marzo_2026.pdf"
    library = EXTENDED.library_for(rel_path)
    assert library is not None
    issue = parse_path(rel_path, library, EXTENDED.profile_for(library), MTIME)
    assert issue.title_name == "Corriere del Ponte"


def test_child_list_replaces_by_default() -> None:
    config = PaperstandConfig.model_validate(
        {"parsers": {"narrow": {"extends": "default", "languages": ["en"]}}}
    )
    assert config.profiles()["narrow"].languages == ["en"]


# ------------------------------------------------------------------- performance


def test_perf_ten_thousand_names_under_a_second() -> None:
    config = example_config()
    library = config.library_for("Newspapers/x.pdf")
    assert library is not None
    profile = config.profile_for(library)
    names = [
        f"Newspapers/2026/{month:02d}/{day:02d}/{title}_{day}_{month_name}_2026{suffix}.pdf"
        for month, month_name in enumerate(("Gennaio", "Febbraio", "Marzo", "Aprile"), start=1)
        for day in range(1, 26)
        for title in (
            "Corriere_del_Ponte",
            "La_Gazzetta_del_Lago",
            "Cronaca_24_Pagine",
            "The_Daily_Ledger",
        )
        for suffix in ("", "-1", "_(2)", "_extra", "_x", "_y", "_z", "_w", "_v", "_u")
    ]
    assert len(names) == 4000
    names = names * 3  # 12,000 names, comfortably past the 10,000 budget
    # Warm the compiled parser up: the budget is about parsing, not compiling.
    parse_path(names[0], library, profile, MTIME)

    started = time.perf_counter()
    for name in names[:10_000]:
        parse_path(name, library, profile, MTIME)
    elapsed = time.perf_counter() - started

    assert elapsed < 1.0, f"10,000 names took {elapsed:.2f}s"


def test_parse_path_touches_no_files(monkeypatch: pytest.MonkeyPatch) -> None:
    """The pipeline is pure: the mtime is injected, the path is just a string."""
    import builtins
    import os

    def forbidden(*args: object, **kwargs: object) -> object:
        raise AssertionError("parse_path must not touch the filesystem")

    config = example_config()
    rel_path = "Newspapers/2026/03/17/Corriere_del_Ponte_17_Marzo_2026.pdf"
    library = config.library_for(rel_path)
    assert library is not None
    profile = config.profile_for(library)

    monkeypatch.setattr(builtins, "open", forbidden)
    monkeypatch.setattr(os, "stat", forbidden)
    monkeypatch.setattr(os, "listdir", forbidden)
    issue = parse_path(rel_path, library, profile, MTIME)
    assert issue.title_name == "Corriere del Ponte"


# --------------------------------------------------------------- title patterns


def test_a_per_title_pattern_is_tried_before_the_generic_rules() -> None:
    config = PaperstandConfig.model_validate(
        {
            "libraries": [
                {
                    "name": "Magazines",
                    "path": "Magazines",
                    "titles": [
                        {"name": "Confini", "pattern": r"^Confini\s*N?\s*(?P<number>\d+)"},
                    ],
                }
            ]
        }
    )
    rel_path = "Magazines/Confini/Confini_N_3_Il_mare_che_ci_separa_Marzo_2026.pdf"
    library = config.library_for(rel_path)
    assert library is not None
    issue = parse_path(rel_path, library, config.profile_for(library), MTIME)
    assert issue.title_name == "Confini"
    assert issue.issue_number == 3
    # The pattern captured only the number: the date still comes from D6.
    assert issue.issue_date is not None
    assert issue.issue_date.isoformat() == "2026-03-01"
    assert issue.matched_rule == "pattern[Confini] D6 title:config"


# ------------------------------------------------- findings from the code review


def parse_with(config: PaperstandConfig, rel_path: str) -> ParsedIssue:
    library = config.library_for(rel_path)
    assert library is not None
    return parse_path(rel_path, library, config.profile_for(library), MTIME)


def config_with(profile: dict[str, Any], **library: Any) -> PaperstandConfig:
    """A one-library configuration using a profile written inline."""
    return PaperstandConfig.model_validate(
        {
            "parsers": {"custom": profile},
            "libraries": [{"name": "M", "path": "M", "parser": "custom", **library}],
        }
    )


def test_a_pattern_that_captures_only_a_year_is_completed_by_the_generic_rules() -> None:
    """A pattern captures what it can; the rest falls through to D1-D7."""
    config = config_with(
        {
            "extends": "default",
            "patterns": [r"^(?P<title>.+?)\s.*(?P<year>\d{4})$"],
        }
    )
    issue = parse_with(config, "M/The_Daily_Ledger_17_March_2026.pdf")
    assert issue.issue_date is not None
    assert issue.issue_date.isoformat() == "2026-03-17"
    assert issue.date_precision == "day"
    assert issue.date_source == "filename"
    # The day the generic rule contributed is part of the date, not a number.
    assert issue.issue_number is None
    assert issue.matched_rule == "pattern[0] D3 title:pattern"


def test_a_pattern_capturing_a_number_does_not_leak_it_into_the_date() -> None:
    """A volume-and-issue pattern of the user's own, not the bundled `default`
    patterns for the same shape: the engine fix stands on its own. Without it,
    the `02` of `c02` is masked only as a date group — it is not one here, the
    pattern captures it as `number` — so D3 reads it as a day, `2 February`
    rather than `February`."""
    config = config_with(
        {
            "extends": "default",
            "patterns": [r"^(?P<title>.+?)\s+v(?P<year>\d{4})(?!\d)\s+c(?P<number>\d{1,5})(?!\d)"],
        }
    )
    issue = parse_with(config, "M/Bright_Meadows_v2024_c02_Febbraio_2024.pdf")
    assert issue.issue_date is not None
    assert issue.issue_date.isoformat() == "2024-02-01"
    assert issue.date_precision == "month"
    assert issue.date_source == "filename"
    assert issue.issue_number == 2
    assert issue.matched_rule == "pattern[0] D6 title:pattern"


def test_number_false_suppresses_a_pattern_captured_number_too() -> None:
    """`number: false` is not just for the generic N1/N2 rules: a number the
    pattern captured on its own is dropped as well, but the date completion
    from the same pattern's spans is unaffected."""
    config = config_with({"extends": "default", "number": False})
    issue = parse_with(config, "M/Bright_Meadows_v2024_c02_Febbraio_2024.pdf")
    assert issue.issue_number is None
    assert issue.issue_date is not None
    assert issue.issue_date.isoformat() == "2024-02-01"
    assert issue.date_precision == "month"


def test_a_year_outside_the_range_is_not_a_year() -> None:
    config = config_with({"extends": "default", "year_range": [1700, 1799]})
    issue = parse_with(config, "M/Title_17_March_1750.pdf")
    assert issue.issue_date is not None
    assert issue.issue_date.isoformat() == "1750-03-17"
    assert issue.date_precision == "day"
    assert issue.date_source == "filename"


def test_a_folder_year_outside_the_range_is_not_a_date_folder() -> None:
    config = config_with({"extends": "default", "year_range": [2000, 2099]})
    issue = parse_with(config, "M/1990/03/Title.pdf")
    assert issue.date_source == "mtime"
    assert issue.issue_date == MTIME
    in_range = parse_with(config, "M/2020/03/Title.pdf")
    assert in_range.date_source == "folder"
    assert in_range.issue_date is not None
    assert in_range.issue_date.isoformat() == "2020-03-01"


RANGE_PATTERN = (
    r"^(?P<title>.+?)\s+(?P<day>\d{1,2})-(?P<date_end>\d{1,2})\s+"
    r"(?P<month>[a-z]+)\s+(?P<year>\d{4})$"
)


def test_a_pattern_with_an_impossible_range_end_does_not_match() -> None:
    config = config_with({"extends": "default", "patterns": [RANGE_PATTERN]})
    issue = parse_with(config, "M/Weekly_6-40_March_2026.pdf")
    # The pattern is out; the generic rules see no valid range either, so only
    # the month survives.
    assert issue.issue_date is not None
    assert issue.issue_date.isoformat() == "2026-03-01"
    assert issue.date_precision == "month"
    assert "pattern" not in issue.matched_rule
    assert issue.matched_rule.startswith("D6")


def test_a_pattern_with_a_valid_range_end_matches() -> None:
    config = config_with({"extends": "default", "patterns": [RANGE_PATTERN]})
    issue = parse_with(config, "M/Weekly_6-19_March_2026.pdf")
    assert issue.issue_date is not None
    assert issue.issue_date.isoformat() == "2026-03-06"
    assert issue.date_precision == "day"
    assert issue.matched_rule == "pattern[0] title:pattern"
    assert issue.title_name == "Weekly"


def test_a_range_that_ends_before_it_starts_does_not_match() -> None:
    config = config_with({"extends": "default", "patterns": [RANGE_PATTERN]})
    issue = parse_with(config, "M/Weekly_19-6_March_2026.pdf")
    assert issue.matched_rule.startswith("D")
