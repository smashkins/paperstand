"""The organizer's own run report: what it did, written for the server to read.

:func:`inventory` lists ``<inbox>/unsorted/`` and ``<inbox>/duplicates/`` — the
only way their contents reach anything outside the organizer itself, since
``duplicates/`` is never re-read by the pipeline. :func:`write_run` is the
organizer's side, called once at the end of every run; :func:`read_run` is the
server's side, called on every ``GET /api/maintenance``.
"""

from __future__ import annotations

import datetime as dt
import os
import tempfile
from pathlib import Path
from typing import Literal

from pydantic import ValidationError

from paperstand.logging import get_logger
from paperstand.organizer.mover import sidecar_path
from paperstand.schemas import OrganizerParked, OrganizerRun

log = get_logger(__name__)

__all__ = ["inventory", "read_run", "write_run"]


def _iso(timestamp: float) -> str:
    """A filesystem timestamp, as the ISO 8601 UTC string every field here uses."""
    return dt.datetime.fromtimestamp(timestamp, dt.UTC).replace(microsecond=0).isoformat()


def _reason(pdf: Path) -> str | None:
    """The first line of ``pdf``'s sidecar, stripped; ``None`` when there is none."""
    try:
        text = sidecar_path(pdf).read_text("utf-8")
    except OSError:
        return None
    first_line = text.splitlines()[0] if text.splitlines() else ""
    return first_line.strip() or None


def _park_folder(inbox: Path, folder: Literal["unsorted", "duplicates"]) -> list[OrganizerParked]:
    root = inbox / folder
    if not root.is_dir():
        return []
    entries: list[OrganizerParked] = []
    for pdf in root.rglob("*.pdf"):
        if not pdf.is_file():
            continue
        info = pdf.stat()
        entries.append(
            OrganizerParked(
                folder=folder,
                name=str(pdf.relative_to(root)),
                reason=_reason(pdf),
                size=info.st_size,
                modified=_iso(info.st_mtime),
            )
        )
    return sorted(entries, key=lambda entry: entry.name)


def inventory(inbox: Path) -> list[OrganizerParked]:
    """Every ``*.pdf`` under ``<inbox>/unsorted/`` and ``<inbox>/duplicates/``.

    A listing taken at the end of a run, not a diff of what that run parked:
    the only way ``duplicates/`` is ever seen, since the pipeline itself never
    reads it back. Sorted by folder, then by name.
    """
    return _park_folder(inbox, "unsorted") + _park_folder(inbox, "duplicates")


def write_run(path: Path, run: OrganizerRun) -> None:
    """Write ``run`` to ``path``, atomically.

    The JSON is written to a dot-prefixed sibling in the same directory and
    ``os.replace``d over ``path``, so a reader never sees a partial file. Any
    ``OSError`` — an unwritable directory, a full disk — is logged as a
    warning and swallowed: the organizer's job is moving files, and a report
    it could not write must not fail a run that did.
    """
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp_name = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.", suffix=".tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                handle.write(run.model_dump_json())
            os.replace(tmp_name, path)
        except OSError:
            Path(tmp_name).unlink(missing_ok=True)
            raise
    except OSError as error:
        log.warning("could not write the organizer report to %s: %s", path, error)


def read_run(path: Path) -> OrganizerRun | None:
    """Read ``path`` back as an :class:`OrganizerRun`, or ``None``.

    ``None`` for an absent file — the organizer has never run here — and for
    anything unreadable, invalid or from a ``version`` this build does not
    know, each logged once with the path so a stale or corrupt report is easy
    to find.
    """
    try:
        text = path.read_text("utf-8")
    except FileNotFoundError:
        return None
    except OSError as error:
        log.warning("could not read the organizer report at %s: %s", path, error)
        return None
    try:
        return OrganizerRun.model_validate_json(text)
    except (ValueError, ValidationError) as error:
        log.warning("the organizer report at %s is not valid: %s", path, error)
        return None
