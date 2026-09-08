"""``GET /api/libraries`` and ``GET /api/stats``.

The two endpoints that describe the catalogue rather than its contents: what
libraries exist and how much they hold, and what the whole thing is costing on
disk.
"""

from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter

from paperstand import queries
from paperstand.api.common import catalogue
from paperstand.config import Settings
from paperstand.db import Database
from paperstand.scanner.covers import covers_root, pages_root
from paperstand.schemas import Library, Stats


def directory_size(path: Path) -> int:
    """How many bytes a directory tree holds, ignoring what cannot be read."""
    if not path.is_dir():
        return 0
    total = 0
    for child in path.rglob("*"):
        try:
            if child.is_file():
                total += child.stat().st_size
        except OSError:  # pragma: no cover - swept while we were counting
            continue
    return total


def database_size(db_path: Path) -> int:
    """The database and its write-ahead log, which is part of its size."""
    total = 0
    for suffix in ("", "-wal", "-shm"):
        candidate = db_path.with_name(db_path.name + suffix)
        try:
            total += candidate.stat().st_size
        except OSError:
            continue
    return total


def create_router(settings: Settings, database: Database | None) -> APIRouter:
    """Build the libraries router."""
    router = APIRouter(prefix="/api", tags=["catalogue"])

    @router.get("/libraries")
    def list_libraries() -> list[Library]:
        """Every configured library, with its title and issue counts."""
        return queries.list_libraries(catalogue(database))

    @router.get("/stats")
    def stats() -> Stats:
        """What the catalogue holds, and what the caches cost on disk."""
        connection = catalogue(database)
        titles, issues, duplicates = queries.catalogue_totals(connection)
        cache = settings.cache_path
        return Stats(
            libraries=queries.list_libraries(connection),
            title_count=titles,
            issue_count=issues,
            duplicate_count=duplicates,
            covers_bytes=directory_size(covers_root(cache)),
            pages_bytes=directory_size(pages_root(cache)),
            db_bytes=database_size(settings.db_path),
        )

    return router
