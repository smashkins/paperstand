"""``GET /api/health``.

The endpoint a container's health check calls, so it must answer under every
circumstance the deployment can produce: no library mounted, an empty library, a
database that could not be opened, a scan writing at that very moment. It reports
what it finds and never fails.
"""

from __future__ import annotations

import sqlite3

from fastapi import APIRouter

from paperstand import __version__
from paperstand.config import Settings
from paperstand.db import Database, get_meta
from paperstand.logging import get_logger
from paperstand.scanner.scheduler import ScanScheduler
from paperstand.scanner.walker import MARKER_FILE
from paperstand.schemas import HealthResponse, ScanRecord

log = get_logger(__name__)

SCAN_FIELDS = (
    "id",
    "started_at",
    "finished_at",
    "status",
    "files_seen",
    "added",
    "updated",
    "removed",
    "covers_done",
    "errors",
    "missing",
    "message",
)


def last_scan(database: Database | None) -> ScanRecord | None:
    """The last scan that finished, as stored in the database."""
    if database is None:
        return None
    try:
        row = database.connection.execute(
            "SELECT * FROM scans WHERE finished_at IS NOT NULL ORDER BY id DESC LIMIT 1"
        ).fetchone()
    except sqlite3.Error as error:
        log.warning("cannot read the last scan: %s", error)
        return None
    if row is None:
        return None
    return ScanRecord(**{field: row[field] for field in SCAN_FIELDS})


def library_marker(settings: Settings, database: Database | None) -> bool | None:
    """The root marker's state: ``None`` unset, ``True`` present, ``False`` lost.

    One read of ``meta``, alongside the count query :func:`issue_count`
    already makes: a catalogue that could not be opened has never
    remembered a marker either, so both come back the same way.
    """
    if database is None:
        return None
    try:
        remembered = get_meta(database.connection, "library_marker") == "1"
    except sqlite3.Error as error:
        log.warning("cannot read the library marker: %s", error)
        return None
    if not remembered:
        return None
    return (settings.library / MARKER_FILE).is_file()


def issue_count(database: Database | None) -> int | None:
    """How many issues the catalogue holds; ``None`` when it cannot be read."""
    if database is None:
        return None
    try:
        row = database.connection.execute("SELECT count(*) AS total FROM issues").fetchone()
    except sqlite3.Error as error:
        log.warning("cannot count the issues: %s", error)
        return None
    return int(row["total"])


def create_router(
    settings: Settings,
    database: Database | None,
    scheduler: ScanScheduler | None,
) -> APIRouter:
    """Build the health router."""
    router = APIRouter(prefix="/api", tags=["system"])

    @router.get("/health")
    def health() -> HealthResponse:
        """Report the service, the library, the database and the last scan."""
        total = issue_count(database)
        marker = library_marker(settings, database)
        return HealthResponse(
            status="ok",
            version=__version__,
            library_path=str(settings.library),
            library_ok=settings.library.is_dir() and marker is not False,
            library_marker=marker,
            db_ok=total is not None,
            last_scan=last_scan(database),
            scanning=scheduler is not None and scheduler.running,
            issue_count=total or 0,
        )

    return router
