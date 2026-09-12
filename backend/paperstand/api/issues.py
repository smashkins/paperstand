"""``GET /api/issues`` and ``GET /api/issues/{id}``.

The list is the one endpoint every browsing screen goes through, so it carries
every filter at once — title, kind, library, a date range, a year — and answers
with a page and the total that page came out of, which is what a "showing 50 of
1 274" line needs.

Duplicates are hidden unless asked for. A duplicate row is a second copy of an
issue Paperstand already has; it stays in the database so that a scan does not
have to decide again every time, and stays out of the answers so that a shelf
does not show the same day twice.
"""

from __future__ import annotations

import datetime as dt
from typing import Annotated

from fastapi import APIRouter, HTTPException, Query

from paperstand import queries
from paperstand.api.common import UNKNOWN_ISSUE, catalogue
from paperstand.db import Database
from paperstand.queries import issue_from_row, pages_url_template
from paperstand.schemas import IssueDetail, IssuePage, IssueSort, Kind

#: The most issues one request may ask for.
MAX_LIMIT = 200


def create_router(database: Database | None) -> APIRouter:
    """Build the issues router."""
    router = APIRouter(prefix="/api", tags=["catalogue"])

    @router.get("/issues")
    def list_issues(
        title: str | None = None,
        kind: Kind | None = None,
        library: str | None = None,
        date_from: Annotated[dt.date | None, Query(alias="from")] = None,
        date_to: Annotated[dt.date | None, Query(alias="to")] = None,
        year: Annotated[int | None, Query(ge=1, le=9999)] = None,
        sort: IssueSort = "date_desc",
        limit: Annotated[int, Query(ge=1, le=MAX_LIMIT)] = 50,
        offset: Annotated[int, Query(ge=0)] = 0,
        include_duplicates: bool = False,
        missing: bool = False,
    ) -> IssuePage:
        """A filtered, sorted page of issues, with the total behind it.

        ``missing`` shows only what every other filter hides for having gone
        missing — a maintenance view will use it; it is ``false``
        everywhere else, including the default list.
        """
        items, total = queries.list_issues(
            catalogue(database),
            title=title,
            kind=kind,
            library=library,
            date_from=date_from.isoformat() if date_from is not None else None,
            date_to=date_to.isoformat() if date_to is not None else None,
            year=year,
            sort=sort,
            limit=limit,
            offset=offset,
            include_duplicates=include_duplicates,
            missing=missing,
        )
        return IssuePage(items=items, total=total)

    @router.get("/issues/{issue_id}")
    def get_issue(issue_id: str) -> IssueDetail:
        """One issue, with what the parser made of it and its neighbours."""
        connection = catalogue(database)
        row = queries.get_issue(connection, issue_id)
        if row is None:
            raise HTTPException(status_code=404, detail=UNKNOWN_ISSUE)
        previous, following = queries.neighbours(connection, row)
        return IssueDetail(
            **issue_from_row(row).model_dump(),
            derived_title=str(row["derived_title"]),
            matched_rule=str(row["matched_rule"]),
            prev_issue_id=previous,
            next_issue_id=following,
            pages_url_template=pages_url_template(issue_id),
        )

    return router
