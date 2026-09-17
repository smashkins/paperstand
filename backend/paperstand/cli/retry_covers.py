"""``retry-covers``: give an issue's cover render another chance.

`render_cover` (see :mod:`paperstand.scanner.covers`) tells a retryable
failure — permissions, a vanished mount, a full cache — apart from a durable
one — the file is not a PDF — and only the second kind ever reaches
``cover_status = 'error'``; the row stays ``pending`` for the first. This
command exists for the residual case: an ``error`` row a scan will never look
at again on its own, whether it was stamped before that distinction existed
or is a misdiagnosis some other way. It resets every such row to ``pending``
and lets the next scan decide, exactly as it would for a file never rendered.
"""

from __future__ import annotations

from pathlib import Path
from typing import TextIO

from paperstand.config import Settings
from paperstand.db import open_database
from paperstand.logging import get_logger

log = get_logger(__name__)

__all__ = ["retry_covers"]


def retry_covers(data: Path, stream: TextIO) -> int:
    """Reset every durably unreadable issue under ``data`` to ``pending``.

    Resets a row even when ``missing_since`` is set: the scanner's
    ``_pending_covers`` already skips a missing row until its file comes
    back, so setting it to ``pending`` here just means it waits quietly
    instead of sitting stuck at ``error`` — it is rendered on the first scan
    after the file returns. Prints how many rows were reset and, when that
    count is more than zero, touches the scan trigger — the next scan is what
    actually renders them. Always exits ``0``: there is nothing here for a
    caller to treat as a failure.
    """
    database = open_database(data / "paperstand.db")
    try:
        connection = database.connection
        cursor = connection.execute(
            "UPDATE issues SET cover_status = 'pending', cover_error = NULL "
            "WHERE cover_status = 'error'"
        )
        reset = cursor.rowcount
        connection.commit()
    finally:
        database.close()

    print(f"{reset} issue(s) reset to pending", file=stream)

    if reset > 0:
        trigger_path = Settings(data=data).scan_trigger_path
        try:
            trigger_path.touch()
        except OSError as error:
            log.warning("could not touch the scan trigger at %s: %s", trigger_path, error)
        else:
            print(f"scan requested: {trigger_path}", file=stream)
            log.info("touched the scan trigger at %s", trigger_path)

    return 0
