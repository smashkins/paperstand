"""Reading progress: ``GET /api/progress`` and ``/api/issues/{id}/progress``.

Every position belongs to the account that set it: each route reads and writes
the caller's rows only, so two people reading the same issue never move each
other's bookmark, and deleting one leaves the other's alone.

The page number is clamped to the issue on the way in: a reader on a phone that
fell behind a re-scan, or a client with an off-by-one, should not be able to
store a position the document does not have.

The progress rows outlive the issues they point at — that is deliberate, and is
why ``reading_progress`` has no foreign key — but writing one still needs the
issue to exist, because the clamp needs its page count.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, Response

from paperstand import queries
from paperstand.api.auth import Authenticator, Caller
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


def create_router(database: Database | None, authenticator: Authenticator) -> APIRouter:
    """Build the progress router.

    Every handler takes the caller from a default rather than an `Annotated`
    hint, for the reason given in `Authenticator.__init__`; it is the same
    dependency the router is included with, so FastAPI resolves it once.
    """
    router = APIRouter(prefix="/api", tags=["progress"])

    @router.get("/progress")
    def list_progress(
        limit: Annotated[int, Query(ge=1, le=MAX_LIMIT)] = 50,
        caller: Caller = Depends(authenticator.user),
    ) -> list[Issue]:
        """Every issue with a stored position, most recently read first."""
        return queries.issues_with_progress(catalogue(database), limit, user_id=caller.account.id)

    @router.get("/issues/{issue_id}/progress")
    def get_progress(issue_id: str, caller: Caller = Depends(authenticator.user)) -> ProgressRecord:
        """Where the reader got to in one issue."""
        record = queries.get_progress(catalogue(database), issue_id, user_id=caller.account.id)
        if record is None:
            raise HTTPException(status_code=404, detail="no progress for this issue")
        return record

    @router.put("/issues/{issue_id}/progress")
    def put_progress(
        issue_id: str, update: ProgressUpdate, caller: Caller = Depends(authenticator.user)
    ) -> ProgressRecord:
        """Store where the reader got to, clamped to the issue."""
        connection = catalogue(database)
        row = queries.issue_row(connection, issue_id)
        if row is None:
            raise HTTPException(status_code=404, detail=UNKNOWN_ISSUE)
        page_count = int(row["page_count"]) if row["page_count"] is not None else None
        return queries.set_progress(
            connection,
            issue_id,
            clamp(update.page, page_count),
            page_count,
            user_id=caller.account.id,
        )

    @router.delete("/issues/{issue_id}/progress", status_code=204)
    def remove_progress(issue_id: str, caller: Caller = Depends(authenticator.user)) -> Response:
        """Forget where the reader got to."""
        queries.delete_progress(catalogue(database), issue_id, user_id=caller.account.id)
        return Response(status_code=204)

    return router
