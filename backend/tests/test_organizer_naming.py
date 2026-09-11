"""Canonical naming: unit tests, plus the whole fixture table pinned to the grammar."""

from __future__ import annotations

import datetime as dt

import pytest

from paperstand.config import PaperstandConfig
from paperstand.organizer import CanonicalPath, Unsorted, plan_issue
from paperstand.parsing import ParsedIssue, parse_path
from tests.fixtures.filenames import (
    CONFIGURED,
    DISCOVERED,
    MTIME,
    Expected,
    discovered_config,
    example_config,
)


def issue(
    *,
    title: str = "Corriere del Ponte",
    title_source: str = "config",
    derived: str = "Corriere del Ponte",
    date: dt.date | None = dt.date(2026, 3, 17),
    precision: str = "day",
    source: str = "filename",
    number: int | None = None,
    volume: int | None = None,
    variant: str | None = None,
) -> ParsedIssue:
    """A :class:`ParsedIssue`, with every field the naming code ignores stubbed out."""
    return ParsedIssue(
        title_name=title,
        title_source=title_source,
        derived_title=derived,
        issue_date=date,
        date_precision=precision,
        date_source=source,
        issue_number=number,
        has_dedup_suffix=False,
        label="",
        matched_rule="",
        volume=volume,
        variant=variant,
    )


def test_day_precision() -> None:
    plan = plan_issue(issue(date=dt.date(2026, 3, 17), precision="day"))
    assert plan == CanonicalPath(
        folder="Corriere del Ponte/2026",
        filename="Corriere del Ponte - 2026-03-17.pdf",
    )
    assert plan.rel_path == "Corriere del Ponte/2026/Corriere del Ponte - 2026-03-17.pdf"


def test_month_precision() -> None:
    plan = plan_issue(
        issue(title="Confini", derived="Confini", date=dt.date(2026, 2, 1), precision="month")
    )
    assert plan == CanonicalPath(folder="Confini/2026", filename="Confini - 2026-02.pdf")


def test_year_precision() -> None:
    plan = plan_issue(
        issue(title="Confini", derived="Confini", date=dt.date(2026, 1, 1), precision="year")
    )
    assert plan == CanonicalPath(folder="Confini/2026", filename="Confini - 2026.pdf")


def test_a_numbered_magazine_gets_no_zero_padding() -> None:
    plan = plan_issue(
        issue(
            title="Orizzonte",
            derived="Orizzonte",
            date=dt.date(2026, 3, 6),
            precision="day",
            number=1655,
        )
    )
    assert plan == CanonicalPath(
        folder="Orizzonte/2026",
        filename="Orizzonte - 2026-03-06 - n1655.pdf",
    )


def test_volume_and_issue_the_number_is_the_issue_not_the_volume() -> None:
    """Amendment 2 (P1.1): the *old* volume-and-issue patterns capture `v2024`
    as the year, not as `ParsedIssue.volume` — only the canonical grammar's own
    `volume` group does that (see the tests below)."""
    plan = plan_issue(
        issue(
            title="Bright Meadows",
            title_source="pattern",
            derived="Bright Meadows",
            date=dt.date(2024, 2, 1),
            precision="month",
            number=2,
        )
    )
    assert plan == CanonicalPath(
        folder="Bright Meadows/2024",
        filename="Bright Meadows - 2024-02 - n2.pdf",
    )


def test_a_volume_is_written_alongside_its_number() -> None:
    plan = plan_issue(
        issue(
            title="Bright Meadows",
            title_source="pattern",
            derived="Bright Meadows",
            date=dt.date(2024, 3, 1),
            precision="month",
            number=3,
            volume=2024,
        )
    )
    assert plan == CanonicalPath(
        folder="Bright Meadows/2024",
        filename="Bright Meadows - 2024-03 - v2024 n3.pdf",
    )


def test_a_volume_with_no_number_is_dropped() -> None:
    """`volume` alone cannot be read back: the canonical grammar only ever
    captures it together with `number`, so writing one on its own would
    produce a name the parser could not parse again."""
    plan = plan_issue(issue(volume=2024))
    assert plan == CanonicalPath(
        folder="Corriere del Ponte/2026",
        filename="Corriere del Ponte - 2026-03-17.pdf",
    )


def test_a_variant_is_appended_after_the_number() -> None:
    plan = plan_issue(
        issue(number=8, precision="year", date=dt.date(2026, 1, 1), variant="Weekend")
    )
    assert plan == CanonicalPath(
        folder="Corriere del Ponte/2026",
        filename="Corriere del Ponte - 2026 - n8 - Weekend.pdf",
    )


def test_a_variant_with_no_number_is_still_appended() -> None:
    plan = plan_issue(issue(variant="Weekend"))
    assert plan == CanonicalPath(
        folder="Corriere del Ponte/2026",
        filename="Corriere del Ponte - 2026-03-17 - Weekend.pdf",
    )


def test_an_apostrophe_title_is_used_verbatim() -> None:
    plan = plan_issue(
        issue(
            title="L'Almanacco",
            derived="L'Almanacco",
            date=dt.date(2026, 1, 1),
            precision="year",
            number=36,
        )
    )
    assert plan == CanonicalPath(folder="L'Almanacco/2026", filename="L'Almanacco - 2026 - n36.pdf")


def test_unsorted_no_configured_title_matches() -> None:
    plan = plan_issue(
        issue(
            title="Unsorted",
            title_source="unsorted",
            derived="Corriere del Ponte Weekend",
            date=dt.date(2026, 3, 17),
            precision="day",
        )
    )
    assert plan == Unsorted(reason='no configured title matches "Corriere del Ponte Weekend"')


def test_unsorted_no_date_at_all() -> None:
    plan = plan_issue(issue(date=None, precision="none", source="none"))
    assert plan == Unsorted(reason="no date in the name or the folder")


def test_unsorted_a_date_from_the_modification_time() -> None:
    plan = plan_issue(issue(date=dt.date(2026, 4, 1), precision="day", source="mtime"))
    assert plan == Unsorted(reason="date would come from the file's modification time")


def test_no_configured_title_wins_over_a_missing_date() -> None:
    """The title check is the first to run, even when the date is unusable too."""
    plan = plan_issue(
        issue(
            title="Unsorted",
            title_source="unsorted",
            derived="Some Supplement",
            date=None,
            precision="none",
            source="none",
        )
    )
    assert plan == Unsorted(reason='no configured title matches "Some Supplement"')


def test_a_missing_date_wins_over_an_mtime_date() -> None:
    """`date_precision` is checked before `date_source`: "none" never reads as "mtime"."""
    plan = plan_issue(issue(date=None, precision="none", source="mtime"))
    assert plan == Unsorted(reason="no date in the name or the folder")


def test_unsorted_a_title_with_a_path_separator() -> None:
    """A configured title is a free string: `/` would smuggle in an extra folder."""
    plan = plan_issue(issue(title="Ponte / Lago", derived="Ponte / Lago"))
    assert plan == Unsorted(reason='title "Ponte / Lago" is not a valid folder name')


def test_unsorted_an_empty_title() -> None:
    plan = plan_issue(issue(title="", derived=""))
    assert plan == Unsorted(reason='title "" is not a valid folder name')


def test_unsorted_a_title_that_is_dot_dot() -> None:
    plan = plan_issue(issue(title="..", derived=".."))
    assert plan == Unsorted(reason='title ".." is not a valid folder name')


def test_an_mtime_date_wins_over_a_bad_title() -> None:
    """`date_source` is checked before the title's shape: the mtime reason stands."""
    plan = plan_issue(issue(title="Ponte / Lago", derived="Ponte / Lago", source="mtime"))
    assert plan == Unsorted(reason="date would come from the file's modification time")


def test_unsorted_a_variant_that_cannot_round_trip() -> None:
    """A variant containing ` - ` would read back as extra fields, not as itself."""
    plan = plan_issue(issue(variant="Weekend - Extra"))
    assert plan == Unsorted(
        reason='variant "Weekend - Extra" contains " - ", which a canonical name '
        "could not read back"
    )


def test_a_bad_title_wins_over_an_unreadable_variant() -> None:
    """The title's shape is checked before the variant's: unchanged order."""
    plan = plan_issue(
        issue(title="Ponte / Lago", derived="Ponte / Lago", variant="Weekend - Extra")
    )
    assert plan == Unsorted(reason='title "Ponte / Lago" is not a valid folder name')


# --------------------------------------------------------------- the fixture table


def _expected_rel_path(expected: Expected) -> str:
    """The canonical path the grammar itself predicts for one fixture row."""
    if expected.precision == "day":
        date = expected.date
    elif expected.precision == "month":
        date = expected.date[:7]  # type: ignore[index]
    elif expected.precision == "year":
        date = expected.date[:4]  # type: ignore[index]
    else:  # pragma: no cover - no fixture row currently reaches "none"
        raise AssertionError(f"a row of precision {expected.precision!r} has no canonical path")
    name = f"{expected.title} - {date}"
    if expected.number is not None:
        if expected.volume is not None:
            name += f" - v{expected.volume} n{expected.number}"
        else:
            name += f" - n{expected.number}"
    if expected.variant is not None:
        name += f" - {expected.variant}"
    year = expected.date[:4]  # type: ignore[index]
    return f"{expected.title}/{year}/{name}.pdf"


def _parse(expected: Expected, config: PaperstandConfig) -> ParsedIssue:
    library = config.library_for(expected.rel_path)
    assert library is not None, f"no library for {expected.rel_path}"
    return parse_path(expected.rel_path, library, config.profile_for(library), MTIME)


def _assert_plan_matches(expected: Expected, plan: object) -> None:
    if expected.title != "Unsorted" and expected.precision != "none" and expected.source != "mtime":
        assert isinstance(plan, CanonicalPath), (
            f"{expected.id}: expected a canonical path, got {plan}"
        )
        assert plan.rel_path == _expected_rel_path(expected)
        return
    assert isinstance(plan, Unsorted), f"{expected.id}: expected unsorted, got {plan}"
    if expected.title_source == "unsorted":
        assert plan.reason == f'no configured title matches "{expected.derived}"'
    elif expected.precision == "none":
        assert plan.reason == "no date in the name or the folder"
    else:
        assert expected.source == "mtime"
        assert plan.reason == "date would come from the file's modification time"


@pytest.fixture(scope="module")
def configured() -> PaperstandConfig:
    return example_config()


@pytest.fixture(scope="module")
def discovered() -> PaperstandConfig:
    return discovered_config()


@pytest.mark.parametrize("expected", CONFIGURED, ids=lambda row: row.id)
def test_configured_names_match_the_canonical_grammar(
    expected: Expected, configured: PaperstandConfig
) -> None:
    _assert_plan_matches(expected, plan_issue(_parse(expected, configured)))


@pytest.mark.parametrize("expected", DISCOVERED, ids=lambda row: row.id)
def test_discovered_names_match_the_canonical_grammar(
    expected: Expected, discovered: PaperstandConfig
) -> None:
    _assert_plan_matches(expected, plan_issue(_parse(expected, discovered)))
