"""Holes in a title's series: in its numbering, and in its declared cadence.

Pure and read-only — no database, no filesystem — so every rule here is a
plain function over :class:`SeriesRow`, the shape :func:`~paperstand.queries.series_rows`
hands back. The caller (``api/maintenance.py``) decides which title to run
this over and builds the response; this module only computes.

Two disjoint ideas share the word "missing" elsewhere in the codebase, and
this module is careful to only ever mean the second one:

* An issue whose file has vanished from the library is *missing* — the
  catalogue still has the row, with its own date and number, and it plays no
  part in a gap: it already occupies its place in the series.
* A *gap* is a date or a number the series never had a row for at all,
  missing or not.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass
from itertools import pairwise

from paperstand.schemas import Frequency, NumberGap

#: A hole wider than this between two consecutive catalogued numbers is read
#: as a change of numbering — a relaunch, a new volume that restarts — not a
#: gap, so it is ignored rather than reported as thousands of missing issues.
NUMBER_GAP_LIMIT = 20

#: How many periods or number ranges a response lists per title. The count
#: behind the list is always complete; only the list shown is capped.
GAP_LIST_LIMIT = 50

#: The declared cadences a date gap can be computed for. ``irregular`` and an
#: undeclared title (``None``) never produce a date gap.
_DATED_FREQUENCIES = ("daily", "weekly", "monthly")


@dataclass(frozen=True, slots=True)
class SeriesRow:
    """One catalogued issue of a title, as far as gap analysis needs it.

    Deliberately not the API's own :class:`~paperstand.schemas.Issue`: this
    is the minimum a gap needs, and it is exactly what
    :func:`~paperstand.queries.series_rows` selects, missing issues included
    — a gap and a missing issue are different things, and telling them apart
    is the caller's query, not this module's business.
    """

    issue_date: str | None
    date_precision: str
    issue_number: int | None
    volume: int | None


def number_gaps(rows: list[SeriesRow]) -> list[NumberGap]:
    """Holes between consecutive catalogued numbers, grouped by volume.

    Applies to every title, declared or not — this is what a numbered weekly
    with no ``publication.yml`` gets covered by. Rows are grouped by
    ``volume`` (``None`` is a group of its own, sorted last); within a group,
    distinct numbers are sorted and every consecutive pair ``(a, b)`` with
    ``1 < b - a <= NUMBER_GAP_LIMIT + 1`` becomes one range,
    ``first=a + 1, last=b - 1``. A wider hole is read as a change of
    numbering and ignored.
    """
    numbers_by_volume: dict[int | None, set[int]] = {}
    for row in rows:
        if row.issue_number is None:
            continue
        numbers_by_volume.setdefault(row.volume, set()).add(row.issue_number)

    gaps: list[NumberGap] = []
    for volume in sorted(numbers_by_volume, key=lambda value: (value is None, value)):
        numbers = sorted(numbers_by_volume[volume])
        for earlier, later in pairwise(numbers):
            width = later - earlier
            if 1 < width <= NUMBER_GAP_LIMIT + 1:
                gaps.append(NumberGap(volume=volume, first=earlier + 1, last=later - 1))
    return gaps


def qualifying_dates(rows: list[SeriesRow], frequency: Frequency | None) -> list[dt.date]:
    """Dates precise enough for ``frequency``'s gap and overdue rules.

    ``daily`` and ``weekly`` need day precision; ``monthly`` accepts day or
    month precision (the database already normalises a month-precision row's
    ``issue_date`` to the first of the month). A coarser row — or any row at
    all, for ``irregular`` and an undeclared title — is ignored.
    """
    if frequency == "monthly":
        precisions = {"day", "month"}
    elif frequency in ("daily", "weekly"):
        precisions = {"day"}
    else:
        return []
    return [
        dt.date.fromisoformat(row.issue_date)
        for row in rows
        if row.issue_date is not None and row.date_precision in precisions
    ]


def _period_label(day: dt.date, frequency: Frequency) -> str:
    if frequency == "daily":
        return day.isoformat()
    if frequency == "weekly":
        iso_year, iso_week, _ = day.isocalendar()
        return f"{iso_year:04d}-W{iso_week:02d}"
    return f"{day.year:04d}-{day.month:02d}"


def _next_period(day: dt.date, frequency: Frequency) -> dt.date:
    """One period after ``day``: a step that always lands cleanly on the next label."""
    if frequency == "daily":
        return day + dt.timedelta(days=1)
    if frequency == "weekly":
        return day + dt.timedelta(days=7)
    if day.month == 12:
        return day.replace(year=day.year + 1, month=1, day=1)
    return day.replace(month=day.month + 1, day=1)


def date_gaps(rows: list[SeriesRow], frequency: Frequency | None) -> list[str]:
    """Periods with no issue at all, between the earliest and the latest, newest first.

    Only for ``daily``, ``weekly`` and ``monthly`` — ``irregular`` and an
    undeclared title always answer ``[]`` — and only once there are at least
    two qualifying rows to bracket a span with. Periods are ISO labels:
    ``YYYY-MM-DD`` for a daily, ``YYYY-Www`` (``date.isocalendar()``) for a
    weekly, ``YYYY-MM`` for a monthly. A variant or a supplement sharing its
    date with the plain issue never creates an expectation of its own — it
    is just one more row covering the same period.
    """
    if frequency not in _DATED_FREQUENCIES:
        return []
    dates = qualifying_dates(rows, frequency)
    if len(dates) < 2:
        return []
    covered = {_period_label(day, frequency) for day in dates}
    earliest, latest = min(dates), max(dates)
    latest_label = _period_label(latest, frequency)

    gaps: list[str] = []
    cursor = earliest
    while True:
        cursor = _next_period(cursor, frequency)
        label = _period_label(cursor, frequency)
        if label == latest_label:
            break
        if label not in covered:
            gaps.append(label)
    gaps.reverse()
    return gaps


def overdue(last: dt.date | None, today: dt.date, frequency: Frequency | None) -> int | None:
    """Days since ``last``, when it is more than one period behind ``today``.

    ``daily``: overdue past one day; ``weekly``: past seven days; ``monthly``:
    once a full calendar month separates them, counted in months, not days,
    so that a short month never reads as overdue a day early. ``None`` for
    ``irregular``, an undeclared title, a title with no qualifying issue yet,
    or one that is not overdue.
    """
    if last is None or frequency not in _DATED_FREQUENCIES:
        return None
    days = (today - last).days
    if frequency == "daily":
        return days if days > 1 else None
    if frequency == "weekly":
        return days if days > 7 else None
    months = (today.year * 12 + today.month) - (last.year * 12 + last.month)
    return days if months > 1 else None
