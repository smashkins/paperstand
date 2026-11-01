"""``GET /api/titles``, ``/api/titles/{id}`` and ``/api/titles/{id}/calendar``.

A title is a periodical: everything the catalogue holds under one name in one
library. The list is what the *Newspapers* and *Magazines* screens are built
from, the calendar is what the title screen draws a month grid with.

``Unsorted`` — the bucket the parser puts a file in when it cannot tell what it
is — is a title like any other in the database, and is left out of the list
unless it is asked for. It is a diagnostic, not a periodical anybody subscribes
to.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query

from paperstand import queries
from paperstand.api.auth import Authenticator, Caller
from paperstand.api.common import UNKNOWN_TITLE, catalogue
from paperstand.db import Database
from paperstand.schemas import Calendar, Kind, Title, TitleDetail, TitleSort


def create_router(database: Database | None, authenticator: Authenticator) -> APIRouter:
    """Build the titles router.

    A title embeds its latest issue, and that issue carries the caller's
    position in it, so the two routes that return one take the caller — from a
    default rather than an `Annotated` hint, for the reason given in
    `Authenticator.__init__`.
    """
    router = APIRouter(prefix="/api", tags=["catalogue"])

    @router.get("/titles")
    def list_titles(
        kind: Kind | None = None,
        library: str | None = None,
        sort: TitleSort = "name",
        include_unsorted: bool = False,
        caller: Caller = Depends(authenticator.user),
    ) -> list[Title]:
        """Every title, with its latest issue and the span its issues cover."""
        return queries.list_titles(
            catalogue(database),
            user_id=caller.account.id,
            kind=kind,
            library=library,
            sort=sort,
            include_unsorted=include_unsorted,
        )

    @router.get("/titles/{title_id}")
    def get_title(title_id: str, caller: Caller = Depends(authenticator.user)) -> TitleDetail:
        """One title, with a per-year count of its issues."""
        title = queries.get_title(catalogue(database), title_id, user_id=caller.account.id)
        if title is None:
            raise HTTPException(status_code=404, detail=UNKNOWN_TITLE)
        return title

    @router.get("/titles/{title_id}/calendar")
    def title_calendar(
        title_id: str,
        year: Annotated[int | None, Query(ge=1, le=9999)] = None,
    ) -> Calendar:
        """One year of a title as a day → issue map, latest year by default."""
        connection = catalogue(database)
        # Only whether the title exists: a calendar carries no issue, and so
        # nobody's position.
        if queries.get_title(connection, title_id, user_id=None) is None:
            raise HTTPException(status_code=404, detail=UNKNOWN_TITLE)
        return queries.title_calendar(connection, title_id, year)

    return router
