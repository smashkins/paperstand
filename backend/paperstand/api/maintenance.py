"""``GET /api/maintenance`` and ``GET /api/maintenance/gaps``.

The maintenance view's two calls. ``/maintenance`` is cheap: a summary of
everything a person can act on right now — issues gone missing, files the
renderer could not open, what the organizer parked under ``unsorted/`` and
``duplicates/``, the parser's own *Unsorted* buckets — which is also where
the top bar's badge number comes from. ``/maintenance/gaps`` is the more
expensive report, computed on request rather than folded into the summary:
holes in every title's numbering and, for a declared cadence, in its
calendar. Gaps are information, not a backlog, so they never count towards
the badge.
"""

from __future__ import annotations

import datetime as dt
from typing import Annotated

from fastapi import APIRouter, Query

from paperstand import gaps, queries
from paperstand.api.common import catalogue
from paperstand.api.today import local_today
from paperstand.config import Settings
from paperstand.db import Database
from paperstand.organizer.report import read_run
from paperstand.schemas import MaintenanceSummary, TitleGaps


def create_router(settings: Settings, database: Database | None) -> APIRouter:
    """Build the maintenance router."""
    router = APIRouter(prefix="/api", tags=["maintenance"])

    @router.get("/maintenance")
    def maintenance() -> MaintenanceSummary:
        """What needs attention right now: the badge, and the page's own header."""
        connection = catalogue(database)
        _titles, _issues, _duplicates, missing = queries.catalogue_totals(connection)
        unreadable = queries.count_unreadable(connection)
        unsorted = queries.unsorted_buckets(connection)
        organizer = read_run(settings.organizer_report_path)
        parked = len(organizer.parked) if organizer is not None else 0
        return MaintenanceSummary(
            missing_count=missing,
            missing_grace_days=settings.missing_grace_days,
            unreadable_count=unreadable,
            unsorted=unsorted,
            unsorted_count=sum(bucket.count for bucket in unsorted),
            organizer=organizer,
            attention=missing + unreadable + parked,
        )

    @router.get("/maintenance/gaps")
    def maintenance_gaps(
        today: Annotated[dt.date | None, Query(description="Defaults to today in TZ")] = None,
    ) -> list[TitleGaps]:
        """Every title with a hole in its numbering, or in its declared cadence."""
        connection = catalogue(database)
        as_of = today or local_today(settings)
        results: list[TitleGaps] = []
        for title in queries.list_titles(connection):
            rows = queries.series_rows(connection, title.id)
            number = gaps.number_gaps(rows)
            dates = gaps.date_gaps(rows, title.frequency)
            qualifying = gaps.qualifying_dates(rows, title.frequency)
            overdue_days = gaps.overdue(
                max(qualifying) if qualifying else None, as_of, title.frequency
            )
            if not number and not dates and overdue_days is None:
                continue
            results.append(
                TitleGaps(
                    title_id=title.id,
                    title_name=title.name,
                    kind=title.kind,
                    frequency=title.frequency,
                    issue_key=title.issue_key,
                    first_date=title.first_date,
                    last_date=title.last_date,
                    issue_count=title.issue_count,
                    overdue_days=overdue_days,
                    date_gaps=dates[: gaps.GAP_LIST_LIMIT],
                    date_gap_count=len(dates),
                    number_gaps=number[: gaps.GAP_LIST_LIMIT],
                    number_gap_count=sum(gap.last - gap.first + 1 for gap in number),
                )
            )
        return results

    return router
