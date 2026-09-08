"""``GET /api/today`` — the front page.

Four shelves, and the only endpoint with an opinion about what a reader wants to
see first:

* **newspapers** — one issue per daily title for the day asked for. A day with
  nothing on it is not an empty page: the most recent day at or before it that
  *does* have newspapers is shown instead, and ``newspapers_date`` says which,
  so the interface can label the shelf honestly. That is what makes the app
  usable before the morning's files have arrived, and on a Sunday.
* **magazines** — the latest issue of every magazine title, newest first.
* **continue_reading** — issues started and not finished, most recent first.
* **recently_added** — what the last scans brought in.

"Today" is the day in ``TZ``, not the day in UTC: at 01:00 in Rome the answer is
the new day, which is when the night's files show up.
"""

from __future__ import annotations

import datetime as dt
from typing import Annotated
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from fastapi import APIRouter, Query

from paperstand import queries
from paperstand.api.common import catalogue
from paperstand.config import Settings
from paperstand.db import Database
from paperstand.logging import get_logger
from paperstand.schemas import Today

log = get_logger(__name__)

#: How many issues each of the two "what next" shelves carries.
SHELF_LIMIT = 12


def local_today(settings: Settings) -> dt.date:
    """The current date in the configured zone, or in the system's."""
    name = settings.tz
    if name:
        try:
            return dt.datetime.now(ZoneInfo(name)).date()
        except (ZoneInfoNotFoundError, ValueError) as error:
            log.warning("TZ=%r is not a known time zone (%s); using the system zone", name, error)
    return dt.datetime.now().astimezone().date()


def create_router(settings: Settings, database: Database | None) -> APIRouter:
    """Build the today router."""
    router = APIRouter(prefix="/api", tags=["catalogue"])

    @router.get("/today")
    def today(
        date: Annotated[dt.date | None, Query(description="Defaults to today in TZ")] = None,
    ) -> Today:
        """The day's newspapers, the latest magazines and what to read next."""
        connection = catalogue(database)
        now = local_today(settings)
        day = date or now
        wanted = day.isoformat()

        newspapers = queries.issues_on_date(connection, "newspaper", wanted)
        newspapers_date: str | None = wanted if newspapers else None
        if not newspapers:
            fallback = queries.latest_date_on_or_before(connection, "newspaper", wanted)
            if fallback is not None:
                newspapers = queries.issues_on_date(connection, "newspaper", fallback)
                newspapers_date = fallback

        return Today(
            date=wanted,
            is_today=day == now,
            newspapers_date=newspapers_date,
            newspapers=newspapers,
            magazines=queries.latest_per_kind(connection, "magazine"),
            continue_reading=queries.continue_reading(connection, SHELF_LIMIT),
            recently_added=queries.recently_added(connection, SHELF_LIMIT),
        )

    return router
