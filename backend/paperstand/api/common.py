"""Pieces every catalogue router needs.

Three of them, and each exists because getting it wrong once would be a bug in
every endpoint at the same time:

* :func:`catalogue` — the API keeps answering when the database could not be
  opened (``/api/health`` says so), so every router that needs one asks for it
  here and gets a ``503`` rather than an ``AttributeError``.
* :func:`resolve_in_library` — the only way a path from the database is turned
  into a path on disk. A ``rel_path`` is data, and data is not trusted to stay
  inside the library root just because a scan put it there.
* :func:`etag_matches` — conditional requests, for the PDF and for the images.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

from fastapi import HTTPException

from paperstand.db import Database
from paperstand.logging import get_logger

log = get_logger(__name__)

CATALOGUE_UNAVAILABLE = "the catalogue is unavailable: the database could not be opened"
UNKNOWN_ISSUE = "unknown issue"
UNKNOWN_TITLE = "unknown title"
MISSING_FILE = "the file is no longer in the library"

#: How long the browser may keep something whose URL cannot change meaning.
IMMUTABLE = "public, max-age=31536000, immutable"

#: What a versionless image URL gets: cache it, but check every time.
REVALIDATE = "no-cache"

#: The PDF is private to the deployment and must be revalidated.
PRIVATE = "private, max-age=0, must-revalidate"


def catalogue(database: Database | None) -> sqlite3.Connection:
    """This request's database connection, or a ``503``."""
    if database is None:
        raise HTTPException(status_code=503, detail=CATALOGUE_UNAVAILABLE)
    try:
        return database.connection
    except sqlite3.Error as error:  # pragma: no cover - the file went away
        log.warning("cannot reach the catalogue: %s", error)
        raise HTTPException(status_code=503, detail=CATALOGUE_UNAVAILABLE) from error


def resolve_in_library(root: Path, rel_path: str) -> Path | None:
    """The absolute path of ``rel_path`` inside ``root``, or ``None``.

    ``None`` means the path does not resolve to somewhere under the library
    root — an absolute path, one climbing out with ``..``, or a symlink pointing
    off the mount. The caller turns that into a plain ``404``: from the outside,
    a row Paperstand refuses to serve and a file that is not there look the same,
    and should.
    """
    candidate = Path(rel_path)
    if candidate.is_absolute() or not rel_path:
        return None
    base = root.resolve()
    resolved = (base / candidate).resolve()
    if resolved != base and not resolved.is_relative_to(base):
        return None
    return resolved


def library_file(root: Path, rel_path: str) -> Path:
    """The readable file ``rel_path`` names, or a ``404``."""
    resolved = resolve_in_library(root, rel_path)
    if resolved is None:
        log.warning("refusing to serve %r: it does not resolve inside %s", rel_path, root)
        raise HTTPException(status_code=404, detail=MISSING_FILE)
    if not resolved.is_file():
        raise HTTPException(status_code=404, detail=MISSING_FILE)
    return resolved


def etag_matches(header: str | None, etag: str) -> bool:
    """Whether an ``If-None-Match`` header covers ``etag``."""
    if not header:
        return False
    for raw in header.split(","):
        token = raw.strip()
        if token == "*" or token == etag:
            return True
        if token.startswith("W/") and token[2:] == etag:
            return True
    return False
