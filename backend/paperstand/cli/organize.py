"""``organize-plan``, a read-only preview, and ``organize``, which writes.

``organize-plan`` mirrors ``parse-report``: same walk, same configuration
discovery, same parser. The only difference is what gets printed for each
file — the canonical path it would get under `<Title>/<YYYY>/<Title> - <ISO
date>[ - n<number>].pdf`, or inside its own declared publication folder, or
why it would stay put. Nothing is ever opened for writing: this command
builds names, it does not create, move, rename or delete a single file.

``organize`` is the command that actually imports PDFs from a writable inbox
into the library — see :mod:`paperstand.organizer.inbox` for the pipeline it
runs. Without ``--apply`` it is a dry run, printing the same report and
touching nothing; ``--every`` repeats it on an interval until stopped.
"""

from __future__ import annotations

import sys
import threading
from collections import defaultdict
from collections.abc import Callable
from pathlib import Path
from signal import SIGINT, SIGTERM, signal
from typing import TextIO

from paperstand.cli.parse import (
    default_config_path,
    load_cli_config,
    missing_explicit_config,
    parse_file,
)
from paperstand.config import ORGANIZER_REPORT, SCAN_TRIGGER, Settings, get_settings
from paperstand.logging import get_logger
from paperstand.organizer import (
    OrganizeReport,
    Unsorted,
    organize_forever,
    organize_once,
    plan_issue,
)
from paperstand.organizer.resolve import declared_title_folders
from paperstand.publication import PublicationIndex
from paperstand.scanner.walker import Walk

log = get_logger(__name__)

__all__ = ["organize", "organize_plan"]


def organize_plan(
    directory: Path,
    config_path: Path | None = None,
    out: TextIO | None = None,
) -> int:
    """Print where every PDF under ``directory`` would live under the canonical layout."""
    stream = out or sys.stdout
    root = directory.resolve()
    if not root.is_dir():
        print(f"organize-plan: {directory} is not a directory", file=sys.stderr)
        return 2
    if missing_explicit_config("organize-plan", config_path):
        return 2
    resolved_config = config_path if config_path is not None else default_config_path()
    config = load_cli_config(resolved_config, root)

    print(f"library root:  {root}", file=stream)
    print(f"configuration: {resolved_config or 'auto-discovered'}", file=stream)
    print(file=stream)

    walk = Walk(root, config)
    buffered = list(walk)
    index = PublicationIndex(root)
    index.load_all(walk.publications)
    title_folders = declared_title_folders(index, walk.publications, config)

    # Source paths grouped by the canonical path they resolve to, so that two
    # (or more) files landing on the same name can be reported together instead
    # of one silently winning.
    sources_by_canonical: dict[str, list[str]] = defaultdict(list)
    planned = 0
    in_place = 0
    unsorted = 0
    for found in buffered:
        rel_path = found.rel_path
        library = config.library_for(rel_path)
        if library is None:
            # Belongs to no configured library: parse-report skips it the same
            # way, since no profile ran on it at all.
            continue
        publication = index.resolve(found.publication_dir, library, config)
        issue = parse_file(rel_path, root, config, publication)
        if issue is None:
            continue
        plan = plan_issue(
            issue,
            publication_folder=title_folders.get((library.name, issue.title_name)),
            library_path=library.path,
        )
        if isinstance(plan, Unsorted):
            unsorted += 1
            print(f"{rel_path} -> unsorted: {plan.reason}", file=stream)
            continue
        sources_by_canonical[plan.rel_path].append(rel_path)
        if plan.rel_path == rel_path:
            in_place += 1
            print(f"{rel_path} -> in place", file=stream)
        else:
            planned += 1
            print(f"{rel_path} -> {plan.rel_path}", file=stream)

    collisions = {
        canonical: sources
        for canonical, sources in sources_by_canonical.items()
        if len(sources) > 1
    }
    if collisions:
        print(file=stream)
        for canonical, sources in sorted(collisions.items()):
            print(f"COLLISION {canonical}", file=stream)
            for source in sorted(sources):
                print(f"  {source}", file=stream)

    print(file=stream)
    print(
        f"{planned} planned, {in_place} in place, {unsorted} unsorted, "
        f"{len(collisions)} collision(s)",
        file=stream,
    )
    return 0


def _default_organize_config_path(data: Path) -> Path | None:
    """The configuration file ``organize`` uses by default.

    The same idea as :func:`paperstand.cli.parse.default_config_path`, but
    against the ``--data`` this run was actually given rather than against
    the environment: a caller that passed ``--data`` explicitly must see
    *that* directory's configuration, not whatever ``PAPERSTAND_DATA`` is set
    to. Honours ``PAPERSTAND_CONFIG`` exactly as ``Settings.config_path``
    does for the server and for ``scan``: an explicit configuration file
    still wins over ``<data>/paperstand.yml`` even when ``--data`` points
    somewhere else.
    """
    settings = get_settings()
    if settings.data != data:
        settings = Settings(**{**settings.model_dump(), "data": data})
    candidate = settings.config_path
    return candidate if candidate.is_file() else None


def _install_stop_handlers(stop: threading.Event) -> Callable[[], None]:
    """Make SIGTERM and SIGINT set ``stop``; return a callable that restores them.

    The organizer is PID 1 in its own container, so without this a plain
    ``docker stop`` would wait out the whole grace period for SIGKILL instead
    of finishing the file in progress and exiting.
    """

    def handler(signal_number: int, frame: object) -> None:
        del signal_number, frame
        stop.set()

    previous_sigterm = signal(SIGTERM, handler)
    previous_sigint = signal(SIGINT, handler)

    def restore() -> None:
        signal(SIGTERM, previous_sigterm)
        signal(SIGINT, previous_sigint)

    return restore


def organize(
    inbox: Path,
    library: Path,
    data: Path,
    config_path: Path | None = None,
    *,
    apply: bool = False,
    settle: float = 60.0,
    every: float | None = None,
    out: TextIO | None = None,
) -> int:
    """Import PDFs from ``inbox`` into ``library``, or preview doing so.

    Without ``apply`` this is a dry run: the report is identical to what
    ``--apply`` would print, and nothing is written. ``every``, when given,
    repeats the run on that interval — re-reading the configuration each
    time — until SIGTERM or SIGINT; without it, the run happens once.

    Exit codes: ``2`` for a usage error (``inbox`` or ``library`` is not a
    directory, one is inside the other, or an explicit ``--config`` does not
    exist); ``1`` when a move failed on an unexpected error, or when the
    library's root marker is remembered but not on disk — a share that may
    not be mounted where ``--library`` expects it; ``0`` otherwise, including
    when unsorted or duplicate files were found, and when another run
    already held the lock. Under ``--every`` a missing marker skips just
    that iteration, with one warning, rather than ending the loop.
    """
    stream = out or sys.stdout
    inbox_root = inbox.resolve()
    library_root = library.resolve()
    data_root = data.resolve()
    if (
        inbox_root == library_root
        or inbox_root.is_relative_to(library_root)
        or library_root.is_relative_to(inbox_root)
    ):
        print(
            "organize: the inbox must not be inside the library nor contain it",
            file=sys.stderr,
        )
        return 2
    if not inbox_root.is_dir():
        print(f"organize: {inbox} is not a directory", file=sys.stderr)
        return 2
    if not library_root.is_dir():
        print(f"organize: {library} is not a directory", file=sys.stderr)
        return 2
    if missing_explicit_config("organize", config_path):
        return 2

    def run() -> OrganizeReport:
        resolved_config = (
            config_path if config_path is not None else _default_organize_config_path(data_root)
        )
        config = load_cli_config(resolved_config, library_root)
        return organize_once(
            inbox_root,
            library_root,
            config,
            config_path=resolved_config,
            db_path=data_root / "paperstand.db",
            apply=apply,
            settle=settle,
            out=stream,
            report_path=data_root / ORGANIZER_REPORT,
            trigger_path=data_root / SCAN_TRIGGER,
        )

    if every is None:
        result = run()
        return 1 if (result.failed or result.refused) else 0

    stop = threading.Event()
    restore = _install_stop_handlers(stop)
    try:
        organize_forever(run, every, stop)
    finally:
        restore()
    return 0
