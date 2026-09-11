"""Importing PDFs from the inbox into the library: the full pipeline.

The first place in :mod:`paperstand.organizer` that reads a whole folder and
decides, file by file, what happens to each one: settle, verify, hash,
resolve, claim, then move — never overwrite, never touch a file already in
the library. :func:`organize_once` runs the pipeline exactly once and prints
the same report whether it writes anything or not; :func:`organize_forever`
repeats it on an interval until told to stop.

The whole run is serialised by ``fcntl.flock`` on ``<inbox>/.organizer.lock``,
held for as long as the run takes: two organizer processes racing over one
inbox would otherwise both try to claim the same destination.
"""

from __future__ import annotations

import datetime as dt
import fcntl
import sqlite3
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import TextIO

import pymupdf

from paperstand.config import PaperstandConfig
from paperstand.db import read_content_hashes, user_version
from paperstand.logging import get_logger
from paperstand.organizer.mover import DestinationOccupied, move_file, park, remove_sidecar
from paperstand.organizer.naming import Unsorted as PlanUnsorted
from paperstand.organizer.resolve import Resolver
from paperstand.scanner.hashing import content_hash
from paperstand.scanner.walker import LibraryFile, Walk

log = get_logger(__name__)

__all__ = [
    "Duplicate",
    "Failed",
    "Moved",
    "OrganizeReport",
    "Outcome",
    "Parked",
    "Skipped",
    "organize_forever",
    "organize_once",
]

#: The whole-run lock, at the inbox's own root. Dot-prefixed, so a walk of the
#: inbox never sees it as a source file.
LOCK_NAME = ".organizer.lock"

#: The two folders a file is parked into; never a source for `duplicates/`.
UNSORTED_FOLDER = "unsorted"
DUPLICATES_FOLDER = "duplicates"


@dataclass(frozen=True, slots=True)
class Moved:
    """``source`` (an inbox rel path) moved, or would move, to ``destination``.

    ``destination`` is relative to the *library* root, exactly the
    :class:`~paperstand.organizer.naming.CanonicalPath` the resolver planned —
    printed verbatim after ``->``. In a dry run nothing actually moved; the
    outcome is identical either way, which is the point of the report.
    """

    source: str
    destination: str


@dataclass(frozen=True, slots=True)
class Duplicate:
    """``source`` is byte-identical to ``of``, already in the library.

    ``of`` is a library rel path: either a row the catalogue already knew
    about, the destination another file of this same run already claimed, or
    the destination a move found already occupied, byte for byte, at move
    time.
    """

    source: str
    of: str


@dataclass(frozen=True, slots=True)
class Parked:
    """``source`` cannot be placed, for ``reason`` — parked under ``unsorted/``.

    Re-evaluated on every run: declaring the title, or creating its
    publication folder, is enough to move it out on the next one.
    """

    source: str
    reason: str


@dataclass(frozen=True, slots=True)
class Skipped:
    """``source`` has not settled yet; left exactly where it is."""

    source: str
    reason: str


@dataclass(frozen=True, slots=True)
class Failed:
    """An unexpected :class:`OSError` moving ``source``; it was left in place."""

    source: str
    error: str


#: One outcome per file, in the order the walk produced them.
Outcome = Moved | Duplicate | Parked | Skipped | Failed


@dataclass(slots=True)
class OrganizeReport:
    """What one run did, or would do, to every file it looked at."""

    outcomes: list[Outcome] = field(default_factory=list)

    @property
    def failed(self) -> int:
        """How many files a move failed on with an unexpected error."""
        return sum(1 for outcome in self.outcomes if isinstance(outcome, Failed))


def organize_once(
    inbox: Path,
    library: Path,
    config: PaperstandConfig,
    *,
    config_path: Path | None = None,
    db_path: Path,
    apply: bool,
    settle: float,
    now: float | None = None,
    out: TextIO,
) -> OrganizeReport:
    """Run one pass over ``inbox``, printing the report to ``out``.

    Takes an exclusive, non-blocking lock on ``<inbox>/.organizer.lock`` for
    the whole run. When another process already holds it, prints one line and
    returns an empty report — nothing is read, nothing is written, and the
    caller still exits ``0``.

    ``apply`` decides whether anything is actually moved; the report is the
    same either way. ``now``, when given, stands in for the current time, so
    that the settle check is deterministic in a test.
    """
    report = OrganizeReport()
    lock_path = inbox / LOCK_NAME
    with lock_path.open("a+") as lock_file:
        try:
            fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            print(f"another organizer run holds {inbox}; nothing done", file=out)
            return report
        _run(
            inbox,
            library,
            config,
            config_path,
            db_path,
            apply=apply,
            settle=settle,
            now=now if now is not None else time.time(),
            out=out,
            report=report,
        )
    return report


def organize_forever(
    run: Callable[[], OrganizeReport], every: float, stop: threading.Event
) -> None:
    """Call ``run``, wait up to ``every`` seconds, and repeat until ``stop`` is set.

    ``run`` is called at least once, even when ``stop`` is already set on
    entry — a caller that wants zero runs simply never calls this at all. The
    wait is a single ``threading.Event.wait``, so setting ``stop`` from a
    signal handler ends the loop within one file's processing, not after a
    full sleep.
    """
    while True:
        run()
        if stop.wait(every):
            return


# ------------------------------------------------------------------- the run


def _run(
    inbox: Path,
    library: Path,
    config: PaperstandConfig,
    config_path: Path | None,
    db_path: Path,
    *,
    apply: bool,
    settle: float,
    now: float,
    out: TextIO,
    report: OrganizeReport,
) -> None:
    catalogue_line, known_hashes = _catalogue(db_path)
    mode = "apply" if apply else "dry-run"

    print(f"{'inbox root:':<14} {inbox}", file=out)
    print(f"{'library root:':<14} {library}", file=out)
    print(
        f"{'configuration:':<14} {config_path if config_path is not None else 'auto-discovered'}",
        file=out,
    )
    print(f"{'catalogue:':<14} {catalogue_line}", file=out)
    print(f"{'mode:':<14} {mode}", file=out)
    print(file=out)

    resolver = Resolver(library, config)
    candidates = [
        found for found in Walk(inbox, config) if not found.rel_path.startswith("duplicates/")
    ]

    moved_hashes: dict[str, str] = {}
    claims: dict[str, str] = {}
    for found in candidates:
        outcome = _process_one(
            found,
            inbox=inbox,
            library=library,
            resolver=resolver,
            known_hashes=known_hashes,
            moved_hashes=moved_hashes,
            claims=claims,
            apply=apply,
            settle=settle,
            now=now,
        )
        report.outcomes.append(outcome)
        print(_report_line(outcome), file=out)

    print(file=out)
    print(_summary_line(report.outcomes, apply=apply), file=out)


def _catalogue(db_path: Path) -> tuple[str, dict[str, str]]:
    """The header's ``catalogue:`` value, and the hashes it carries.

    ``read_content_hashes`` already returns ``{}`` both for "no hashes yet"
    and for "cannot be read at all", logging its own warning either way; this
    tells the two apart for the header, without opening the database twice
    for anything but a read-only ``PRAGMA``.
    """
    hashes = read_content_hashes(db_path)
    if hashes or _catalogue_readable(db_path):
        return f"{db_path} ({len(hashes)} hashes)", hashes
    return "not readable — duplicates are checked against the destination only", hashes


def _catalogue_readable(db_path: Path) -> bool:
    try:
        connection = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    except sqlite3.Error:
        return False
    try:
        return user_version(connection) >= 3
    except sqlite3.Error:
        return False
    finally:
        connection.close()


# ------------------------------------------------------------- per-file steps


def _process_one(
    found: LibraryFile,
    *,
    inbox: Path,
    library: Path,
    resolver: Resolver,
    known_hashes: dict[str, str],
    moved_hashes: dict[str, str],
    claims: dict[str, str],
    apply: bool,
    settle: float,
    now: float,
) -> Outcome:
    """The pipeline for one inbox file: settle, verify, hash, resolve, claim, move."""
    source = inbox / found.rel_path
    mtime_seconds = found.mtime_ns / 1_000_000_000
    elapsed = now - mtime_seconds
    if elapsed < settle:
        return Skipped(found.rel_path, f"modified {int(elapsed)} s ago")

    unreadable = _unreadable_reason(source)
    if unreadable is not None:
        reason = f"not a readable PDF ({unreadable})"
        return _park_unsorted(found, inbox, reason, apply=apply)

    digest = content_hash(source)
    known = known_hashes.get(digest) or moved_hashes.get(digest)
    if known is not None:
        return _park_duplicate(found, inbox, known, apply=apply)

    mtime = dt.datetime.fromtimestamp(mtime_seconds)
    resolved = resolver.resolve(found.filename, mtime)
    if isinstance(resolved, PlanUnsorted):
        return _park_unsorted(found, inbox, resolved.reason, apply=apply)

    destination_rel = resolved.destination.rel_path
    claimant = claims.get(destination_rel)
    if claimant is not None:
        reason = f"destination {destination_rel} is claimed by {claimant}"
        return _park_unsorted(found, inbox, reason, apply=apply)
    claims[destination_rel] = found.rel_path

    destination = library / destination_rel
    if not destination.resolve().is_relative_to(library.resolve()):
        # `LibraryConfig.path` is a free string and may contain `..`: without
        # this check a configured path like `../outside` would plan a
        # destination that escapes the library root entirely.
        reason = f"destination {destination_rel} is outside the library"
        return _park_unsorted(found, inbox, reason, apply=apply)
    outcome = (
        _apply_move(found, inbox, destination, destination_rel, digest)
        if apply
        else _dry_run_move(found, destination, destination_rel, digest)
    )
    if isinstance(outcome, Moved):
        moved_hashes[digest] = destination_rel
    return outcome


def _unreadable_reason(path: Path) -> str | None:
    """Why ``path`` is not a readable PDF, or ``None`` when it is.

    A password-protected file and a broken one are told apart the same way
    the cover renderer already does: opening it never raises just for being
    encrypted, only ``needs_pass`` says so afterwards.
    """
    try:
        with pymupdf.open(path) as document:
            if document.needs_pass:
                return "the document is encrypted"
            if document.page_count == 0:
                return "the document has no pages"
    except Exception as error:  # a broken PDF must not stop the run
        return f"{type(error).__name__}: {error}"
    return None


def _park_unsorted(found: LibraryFile, inbox: Path, reason: str, *, apply: bool) -> Parked:
    """Report ``found`` as unsorted, parking it in apply mode.

    A file already sitting in ``unsorted/`` that stays unsorted is re-parked
    onto itself: :func:`park` only rewrites its sidecar, and only when the
    reason actually changed.
    """
    if apply:
        park(inbox / found.rel_path, inbox / UNSORTED_FOLDER, reason)
    return Parked(found.rel_path, reason)


def _park_duplicate(found: LibraryFile, inbox: Path, of: str, *, apply: bool) -> Duplicate:
    """Report ``found`` as a duplicate of ``of``, parking it in apply mode.

    A copy that was itself sitting in ``unsorted/`` loses its sidecar first,
    so nothing dangles once it moves on to ``duplicates/``.
    """
    if apply:
        source = inbox / found.rel_path
        if found.rel_path.startswith(f"{UNSORTED_FOLDER}/"):
            remove_sidecar(source)
        park(source, inbox / DUPLICATES_FOLDER, f"duplicate of {of}")
    return Duplicate(found.rel_path, of)


def _dry_run_move(
    found: LibraryFile, destination: Path, destination_rel: str, digest: str
) -> Outcome:
    """What a move would do, without writing anything.

    Checked the same way the real move is: a destination already on disk is
    hashed, so the dry run reports exactly what ``--apply`` would.
    """
    if destination.exists():
        if content_hash(destination) == digest:
            return Duplicate(found.rel_path, destination_rel)
        reason = f"destination {destination_rel} exists with different content"
        return Parked(found.rel_path, reason)
    return Moved(found.rel_path, destination_rel)


def _apply_move(
    found: LibraryFile, inbox: Path, destination: Path, destination_rel: str, digest: str
) -> Outcome:
    """Actually move ``found`` to ``destination``, handling every way it can fail.

    A file arriving from ``unsorted/`` loses its sidecar before the attempt,
    whatever the attempt turns into: moved, a duplicate found at move time,
    or still unsorted for a new reason — :func:`park` writes a fresh sidecar
    in the last two cases, so nothing is ever left without one.
    """
    source = inbox / found.rel_path
    if found.rel_path.startswith(f"{UNSORTED_FOLDER}/"):
        remove_sidecar(source)
    try:
        move_file(source, destination)
    except DestinationOccupied:
        if content_hash(destination) == digest:
            park(source, inbox / DUPLICATES_FOLDER, f"duplicate of {destination_rel}")
            return Duplicate(found.rel_path, destination_rel)
        reason = f"destination {destination_rel} exists with different content"
        park(source, inbox / UNSORTED_FOLDER, reason)
        return Parked(found.rel_path, reason)
    except OSError as error:
        log.warning("cannot move %s to %s: %s", source, destination, error)
        return Failed(found.rel_path, str(error))
    log.info("moved %s -> %s", found.rel_path, destination_rel)
    return Moved(found.rel_path, destination_rel)


# --------------------------------------------------------------- the report


def _report_line(outcome: Outcome) -> str:
    if isinstance(outcome, Moved):
        return f"{outcome.source} -> {outcome.destination}"
    if isinstance(outcome, Duplicate):
        return f"{outcome.source} -> duplicate of {outcome.of}"
    if isinstance(outcome, Parked):
        return f"{outcome.source} -> unsorted: {outcome.reason}"
    if isinstance(outcome, Skipped):
        return f"{outcome.source} -> skipped: {outcome.reason}"
    return f"{outcome.source} -> failed: {outcome.error}"


def _summary_line(outcomes: list[Outcome], *, apply: bool) -> str:
    moved = sum(1 for outcome in outcomes if isinstance(outcome, Moved))
    duplicate = sum(1 for outcome in outcomes if isinstance(outcome, Duplicate))
    unsorted = sum(1 for outcome in outcomes if isinstance(outcome, Parked))
    skipped = sum(1 for outcome in outcomes if isinstance(outcome, Skipped))
    if not apply:
        return f"{moved} to move, {duplicate} duplicate, {unsorted} unsorted, {skipped} skipped"
    failed = sum(1 for outcome in outcomes if isinstance(outcome, Failed))
    return (
        f"{moved} moved, {duplicate} duplicate, {unsorted} unsorted, "
        f"{skipped} skipped, {failed} failed"
    )
