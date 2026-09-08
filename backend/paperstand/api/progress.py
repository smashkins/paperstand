"""Reading progress: ``GET /api/progress`` and ``/api/issues/{id}/progress``.

Paperstand is single user, so there is one position per issue and no
authentication around it. The page number is clamped to the issue on the way in:
a reader on a phone that fell behind a re-scan, or a client with an off-by-one,
should not be able to store a position the document does not have.

The progress rows outlive the issues they point at — that is deliberate, and is
why ``reading_progress`` has no foreign key — but writing one still needs the
issue to exist, because the clamp needs its page count.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, HTTPException, Query, Response

from paperstand import queries
from paperstand.api.common import UNKNOWN_ISSUE, catalogue
from paperstand.db import Database
from paperstand.schemas import Issue, ProgressRecord, ProgressUpdate

#: The most progress rows one request may ask for.
MAX_LIMIT = 200


def clamp(page: int, page_count: int | None) -> int:
    """A page number the issue actually has.

    With no page count — the scanner has not opened the file yet — any positive
    page is taken at face value rather than refused: the reader is looking at
    the document and knows better than the catalogue does.
    """
    if page < 1:
        return 1
    if page_count is not None and page_count >= 1:
        return min(page, page_count)
    return page


def create_router(database: Database | None) -> APIRouter:
    """Build the progress router."""
    router = APIRouter(prefix="/api", tags=["progress"])

    @router.get("/progress")
    def list_progress(
        limit: Annotated[int, Query(ge=1, le=MAX_LIMIT)] = 50,
    ) -> list[Issue]:
        """Every issue with a stored position, most recently read first."""
        return queries.issues_with_progress(catalogue(database), limit)

    @router.get("/issues/{issue_id}/progress")
    def get_progress(issue_id: str) -> ProgressRecord:
        """Where the reader got to in one issue."""
        record = queries.get_progress(catalogue(database), issue_id)
        if record is None:
            raise HTTPException(status_code=404, detail="no progress for this issue")
        return record

    @router.put("/issues/{issue_id}/progress")
    def put_progress(issue_id: str, update: ProgressUpdate) -> ProgressRecord:
        """Store where the reader got to, clamped to the issue."""
        connection = catalogue(database)
        row = queries.issue_row(connection, issue_id)
        if row is None:
            raise HTTPException(status_code=404, detail=UNKNOWN_ISSUE)
        page_count = int(row["page_count"]) if row["page_count"] is not None else None
        return queries.set_progress(
            connection, issue_id, clamp(update.page, page_count), page_count
        )

    @router.delete("/issues/{issue_id}/progress", status_code=204)
    def remove_progress(issue_id: str) -> Response:
        """Forget where the reader got to."""
        queries.delete_progress(catalogue(database), issue_id)
        return Response(status_code=204)

    return router
