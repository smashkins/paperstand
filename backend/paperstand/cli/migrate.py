"""``migrate``: rename and move files already in the library to the canonical layout.

Where ``organize`` imports PDFs into the library from a writable inbox, this
command works on files already there — the shape a rule change, or a newly
declared publication, means a maintainer would otherwise have to rename by
hand. See :mod:`paperstand.organizer.migration` for the pipeline it runs.
Without ``--apply`` it is a dry run, printing the same report and touching
nothing.
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import TextIO

from paperstand.cli.organize import _default_organize_config_path
from paperstand.cli.parse import load_cli_config, missing_explicit_config
from paperstand.config import MIGRATION_REPORTS, SCAN_TRIGGER
from paperstand.organizer.migration import migrate_once

__all__ = ["migrate"]


def migrate(
    library: Path,
    data: Path,
    config_path: Path | None = None,
    *,
    apply: bool = False,
    prune_empty: bool = True,
    out: TextIO | None = None,
) -> int:
    """Migrate every already-catalogued PDF under ``library`` to its canonical name and place.

    Without ``apply`` this is a dry run: the report is identical to what
    ``--apply`` would print, and nothing is written.

    Exit codes: ``2`` for a usage error (``library`` is not a directory, or
    an explicit ``--config`` does not exist); ``2`` when ``apply`` refused
    because the catalogue could not be read or a movable file's row carries
    no content hash yet; ``1`` when the library's root marker is remembered
    but not on disk, or a move failed on an unexpected error; ``0``
    otherwise, including when unsorted files or collisions were found, and
    when another run already held the lock.
    """
    stream = out or sys.stdout
    library_root = library.resolve()
    data_root = data.resolve()
    if not library_root.is_dir():
        print(f"migrate: {library} is not a directory", file=sys.stderr)
        return 2
    if missing_explicit_config("migrate", config_path):
        return 2

    resolved_config = (
        config_path if config_path is not None else _default_organize_config_path(data_root)
    )
    config = load_cli_config(resolved_config, library_root)
    report = migrate_once(
        library_root,
        config,
        config_path=resolved_config,
        db_path=data_root / "paperstand.db",
        apply=apply,
        prune_empty=prune_empty,
        out=stream,
        reports_dir=data_root / MIGRATION_REPORTS,
        trigger_path=data_root / SCAN_TRIGGER,
    )
    if report.refused == "marker":
        return 1
    if report.refused == "catalogue":
        return 2
    return 1 if report.failed else 0
