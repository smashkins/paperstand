"""``gaps.py``: pure functions over a title's rows, no database, no I/O."""

from __future__ import annotations

import datetime as dt

from paperstand.gaps import NUMBER_GAP_LIMIT, SeriesRow, date_gaps, number_gaps, overdue
from paperstand.schemas import NumberGap


def _row(
    *,
    issue_date: str | None = None,
    date_precision: str = "day",
    issue_number: int | None = None,
    volume: int | None = None,
) -> SeriesRow:
    return SeriesRow(
        issue_date=issue_date,
        date_precision=date_precision,
        issue_number=issue_number,
        volume=volume,
    )


# ------------------------------------------------------------- number gaps


def test_consecutive_numbers_have_no_gap() -> None:
    rows = [_row(issue_number=n) for n in (1, 2, 3)]
    assert number_gaps(rows) == []


def test_a_single_hole_is_one_range_of_one() -> None:
    rows = [_row(issue_number=n) for n in (1650, 1651, 1652, 1653, 1655, 1656)]
    assert number_gaps(rows) == [NumberGap(volume=None, first=1654, last=1654)]


def test_a_wider_hole_is_one_range() -> None:
    rows = [_row(issue_number=n) for n in (1, 2, 3, 8)]
    assert number_gaps(rows) == [NumberGap(volume=None, first=4, last=7)]


def test_gaps_are_grouped_per_volume() -> None:
    rows = [
        _row(issue_number=2, volume=2024),
        _row(issue_number=15, volume=2023),
    ]
    # Different volumes: 2 and 15 never bracket a gap with each other.
    assert number_gaps(rows) == []


def test_a_gap_exactly_at_the_limit_is_reported() -> None:
    rows = [_row(issue_number=1), _row(issue_number=1 + NUMBER_GAP_LIMIT + 1)]
    assert number_gaps(rows) == [NumberGap(volume=None, first=2, last=NUMBER_GAP_LIMIT + 1)]


def test_a_hole_wider_than_the_limit_is_a_numbering_change_not_a_gap() -> None:
    rows = [_row(issue_number=1), _row(issue_number=1 + NUMBER_GAP_LIMIT + 2)]
    assert number_gaps(rows) == []


def test_duplicate_numbers_collapse_to_one() -> None:
    """A variant sharing its parent's number is not a gap on either side."""
    rows = [_row(issue_number=1), _row(issue_number=1), _row(issue_number=2)]
    assert number_gaps(rows) == []


def test_fewer_than_two_numbers_is_no_gap() -> None:
    assert number_gaps([_row(issue_number=1)]) == []
    assert number_gaps([_row(issue_number=None)]) == []
    assert number_gaps([]) == []


# --------------------------------------------------------------- date gaps


def test_daily_with_a_hole() -> None:
    rows = [
        _row(issue_date="2026-03-01"),
        _row(issue_date="2026-03-02"),
        # 03-03 missing
        _row(issue_date="2026-03-04"),
    ]
    assert date_gaps(rows, "daily") == ["2026-03-03"]


def test_daily_a_weekend_only_edition_still_covers_its_day() -> None:
    """A variant sharing the plain issue's date covers the day just as well."""
    rows = [
        _row(issue_date="2026-03-01"),
        _row(issue_date="2026-03-02"),  # the Weekend edition, same date
        _row(issue_date="2026-03-03"),
    ]
    assert date_gaps(rows, "daily") == []


def test_daily_fewer_than_two_qualifying_rows_is_no_gap() -> None:
    assert date_gaps([_row(issue_date="2026-03-01")], "daily") == []
    assert date_gaps([], "daily") == []


def test_weekly_across_a_year_boundary() -> None:
    # ISO week 52 of 2025 starts Monday 2025-12-22; week 1 of 2026 starts
    # 2025-12-29. A row in each, with one week skipped in between.
    rows = [
        _row(issue_date="2025-12-22"),  # 2025-W52
        _row(issue_date="2026-01-12"),  # 2026-W03
    ]
    assert date_gaps(rows, "weekly") == ["2026-W02", "2026-W01"]


def test_monthly_with_month_precision_rows() -> None:
    rows = [
        _row(issue_date="2026-01-01", date_precision="month"),
        _row(issue_date="2026-04-01", date_precision="month"),
    ]
    assert date_gaps(rows, "monthly") == ["2026-03", "2026-02"]


def test_monthly_accepts_day_precision_too() -> None:
    rows = [
        _row(issue_date="2026-01-15", date_precision="day"),
        _row(issue_date="2026-03-03", date_precision="day"),
    ]
    assert date_gaps(rows, "monthly") == ["2026-02"]


def test_coarser_rows_are_ignored() -> None:
    """A year-precision row never qualifies, whatever the cadence."""
    rows = [
        _row(issue_date="2026-01-01", date_precision="year"),
        _row(issue_date="2026-01-15", date_precision="day"),
        _row(issue_date="2026-03-03", date_precision="day"),
    ]
    assert date_gaps(rows, "monthly") == ["2026-02"]


def test_irregular_and_undeclared_never_produce_date_gaps() -> None:
    rows = [_row(issue_date="2026-01-01"), _row(issue_date="2026-06-01")]
    assert date_gaps(rows, "irregular") == []
    assert date_gaps(rows, None) == []


# ----------------------------------------------------------------- overdue


def test_daily_overdue_thresholds() -> None:
    today = dt.date(2026, 3, 17)
    assert overdue(dt.date(2026, 3, 16), today, "daily") is None  # one day: on time
    assert overdue(dt.date(2026, 3, 15), today, "daily") == 2  # two days: overdue


def test_weekly_overdue_thresholds() -> None:
    today = dt.date(2026, 3, 17)
    assert overdue(today - dt.timedelta(days=7), today, "weekly") is None
    assert overdue(today - dt.timedelta(days=8), today, "weekly") == 8


def test_monthly_overdue_thresholds() -> None:
    today = dt.date(2026, 3, 17)
    assert overdue(dt.date(2026, 2, 1), today, "monthly") is None  # one month: on time
    assert overdue(dt.date(2026, 1, 31), today, "monthly") is not None  # two months


def test_overdue_is_none_without_a_last_date_or_a_dated_cadence() -> None:
    today = dt.date(2026, 3, 17)
    assert overdue(None, today, "daily") is None
    assert overdue(dt.date(2026, 1, 1), today, "irregular") is None
    assert overdue(dt.date(2026, 1, 1), today, None) is None
