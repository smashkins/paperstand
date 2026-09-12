"""Planning and performing an in-library migration to the canonical layout.

Where :mod:`paperstand.organizer.inbox` imports PDFs *into* the library from a
writable inbox, this module renames and moves files already *inside* it, in
place, to the canonical `<Title>/<YYYY>/<Title> - <ISO date>[ - n<number>].pdf`
layout that a fresh naming rule, or a newly declared publication, may have
made possible. :func:`plan_library` is the pure planner — the computation
``organize-plan`` prints, factored out so both it and :func:`migrate_once`
share exactly one walk of the library and one set of naming decisions.
:func:`migrate_once` is the writer: it turns a :class:`LibraryPlan` into
moves, refusing before touching a single file when the catalogue could not
follow along, and reports a per-run summary the way :func:`organize_once`
does for the inbox.

A migration never overwrites, never deletes, never touches a file's bytes.
Two files that would land on the same canonical name are a
:class:`Collision`: neither moves, not even the one already there, and the
maintainer resolves it by hand.
"""

from __future__ import annotations

import datetime as dt
import errno
import fcntl
import os
import sys
import time
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import TextIO

from paperstand.cli.parse import parse_file
from paperstand.config import PaperstandConfig
from paperstand.db import read_rel_path_hashes
from paperstand.logging import get_logger
from paperstand.organizer.inbox import marker_missing, occupant_matches
from paperstand.organizer.mover import DestinationOccupied, move_file
from paperstand.organizer.naming import Unsorted, plan_issue
from paperstand.organizer.report import write_run
from paperstand.organizer.resolve import declared_title_folders
from paperstand.publication import PublicationIndex
from paperstand.scanner.hashing import content_hash
from paperstand.scanner.walker import MARKER_FILE, Walk
from paperstand.schemas import (
    MigrationCollision,
    MigrationLeftInPlace,
    MigrationRun,
    OrganizerMove,
)

log = get_logger(__name__)

__all__ = [
    "Collided",
    "Collision",
    "InPlace",
    "LibraryPlan",
    "MigrationReport",
    "Move",
    "Unplaced",
    "migrate_once",
    "plan_library",
]

#: The whole-run lock, under ``<data>/organizer``. Separate from the inbox's
#: own lock (``<inbox>/.organizer.lock``): a migration and an ``organize``
#: run may proceed at the same time, since the mover's occupant check is
#: what keeps either from overwriting the other.
LOCK_NAME = ".migrate.lock"


# --------------------------------------------------------------- the planner


@dataclass(frozen=True, slots=True)
class InPlace:
    """``rel_path`` is already named and placed the way the canonical layout wants it."""

    rel_path: str


@dataclass(frozen=True, slots=True)
class Move:
    """``rel_path`` would move to ``destination``, both library-relative."""

    rel_path: str
    destination: str


@dataclass(frozen=True, slots=True)
class Unplaced:
    """``rel_path`` would stay exactly where it is, for ``reason``."""

    rel_path: str
    reason: str


@dataclass(frozen=True, slots=True)
class Collision:
    """Two or more sources whose plan resolves to the same ``destination``.

    ``sources`` is sorted, has at least two entries, and includes a source
    already in place at ``destination`` when one is a member of the group —
    the group is what makes that in-place file unsafe to treat as settled.
    """

    destination: str
    sources: tuple[str, ...]


@dataclass(slots=True)
class LibraryPlan:
    """One walk of a library, planned against the canonical layout.

    ``entries`` is exactly one per file the walk found in a configured
    library, in walk order; a file outside every configured library is
    skipped silently, the same as ``parse-report``. ``walk_complete`` is
    the buffered walk's own :attr:`~paperstand.scanner.walker.Walk.complete`
    — ``False`` when a directory could not be listed or the depth limit was
    reached, meaning a declared ``publication.yml`` may sit unseen behind
    it, or a collision source may be hidden.
    """

    entries: list[InPlace | Move | Unplaced]
    collisions: list[Collision]
    walk_complete: bool

    def movable(self) -> list[Move]:
        """This plan's ``Move`` entries whose destination is in no collision."""
        contested = {group.destination for group in self.collisions}
        return [
            entry
            for entry in self.entries
            if isinstance(entry, Move) and entry.destination not in contested
        ]


def plan_library(root: Path, config: PaperstandConfig) -> LibraryPlan:
    """Where every PDF under ``root`` would live under the canonical layout.

    Exactly the computation ``organize-plan`` used to do inline: one buffered
    walk, one :class:`~paperstand.publication.PublicationIndex`, one call to
    :func:`~paperstand.organizer.resolve.declared_title_folders`, so that a
    misconfiguration warning (two folders declaring the same title) is
    logged once per call, not once per consumer.
    """
    walk = Walk(root, config)
    buffered = list(walk)
    index = PublicationIndex(root)
    index.load_all(walk.publications)
    title_folders = declared_title_folders(index, walk.publications, config)

    entries: list[InPlace | Move | Unplaced] = []
    sources_by_canonical: dict[str, list[str]] = defaultdict(list)

    for found in buffered:
        rel_path = found.rel_path
        library = config.library_for(rel_path)
        if library is None:
            continue
        publication = index.resolve(found.publication_dir, library, config)
        issue = parse_file(rel_path, root, config, publication)
        if issue is None:
            continue
        planned = plan_issue(
            issue,
            publication_folder=title_folders.get((library.name, issue.title_name)),
            library_path=library.path,
        )
        if isinstance(planned, Unsorted):
            entries.append(Unplaced(rel_path, planned.reason))
            continue
        sources_by_canonical[planned.rel_path].append(rel_path)
        if planned.rel_path == rel_path:
            entries.append(InPlace(rel_path))
        else:
            entries.append(Move(rel_path, planned.rel_path))

    collisions = [
        Collision(destination, tuple(sorted(sources)))
        for destination, sources in sorted(sources_by_canonical.items())
        if len(sources) > 1
    ]

    return LibraryPlan(entries=entries, collisions=collisions, walk_complete=walk.complete)


# --------------------------------------------------------------- the outcomes


@dataclass(frozen=True, slots=True)
class Moved:
    """``rel_path`` moved, or would move, to ``destination``."""

    rel_path: str
    destination: str


@dataclass(frozen=True, slots=True)
class Collided:
    """``rel_path`` was left in place because of ``reason``.

    Covers every way a move conflicts with something else: a member of a
    collision group, a cycle in the deferred moves, the destination being
    the same file under another spelling of its case, or a destination that
    already exists with different content.
    """

    rel_path: str
    reason: str


@dataclass(frozen=True, slots=True)
class Duplicate:
    """``rel_path`` is byte-identical to ``of``, already at its destination.

    Left exactly where it is — the organizer never deletes; a maintainer
    removes one copy by hand.
    """

    rel_path: str
    of: str


@dataclass(frozen=True, slots=True)
class Failed:
    """An unexpected :class:`OSError` moving ``rel_path``; it was left in place."""

    rel_path: str
    error: str


#: One outcome per file this run looked at, in walk order.
Outcome = Moved | InPlace | Unplaced | Collided | Duplicate | Failed


@dataclass(slots=True)
class MigrationReport:
    """What one ``migrate`` run did, or would do, to every file it planned."""

    outcomes: list[Outcome] = field(default_factory=list)
    refused: str | None = None
    """``"marker"``, ``"walk"`` or ``"catalogue"`` when the run refused before
    touching anything; ``None`` otherwise, including when another run held
    the lock."""
    removed_folders: list[str] = field(default_factory=list)

    @property
    def moved(self) -> int:
        return sum(1 for outcome in self.outcomes if isinstance(outcome, Moved))

    @property
    def in_place(self) -> int:
        return sum(1 for outcome in self.outcomes if isinstance(outcome, InPlace))

    @property
    def unsorted(self) -> int:
        return sum(1 for outcome in self.outcomes if isinstance(outcome, Unplaced))

    @property
    def collision(self) -> int:
        return sum(1 for outcome in self.outcomes if isinstance(outcome, Collided))

    @property
    def duplicate(self) -> int:
        return sum(1 for outcome in self.outcomes if isinstance(outcome, Duplicate))

    @property
    def failed(self) -> int:
        return sum(1 for outcome in self.outcomes if isinstance(outcome, Failed))


def _collision_reason(group: Collision) -> str:
    other = len(group.sources) - 1
    noun = "file" if other == 1 else "files"
    return f"{group.destination} is also the plan of {other} other {noun}"


def _resolve_move(library: Path, move: Move, *, apply: bool) -> Outcome:
    """Perform, or preview, one ``move`` already known to be free of every collision.

    The samefile, occupant and content checks run identically in both modes,
    so the printed line never differs between a dry run and ``--apply``.
    """
    source = library / move.rel_path
    destination = library / move.destination
    same_case_reason = "the destination is this same file under another spelling of its case"
    occupied_reason = f"destination {move.destination} exists with different content"
    if destination.exists() and destination.samefile(source):
        return Collided(move.rel_path, same_case_reason)
    if not apply:
        if destination.exists():
            if occupant_matches(destination, content_hash(source)):
                return Duplicate(move.rel_path, move.destination)
            return Collided(move.rel_path, occupied_reason)
        return Moved(move.rel_path, move.destination)
    try:
        move_file(source, destination)
    except DestinationOccupied:
        if occupant_matches(destination, content_hash(source)):
            return Duplicate(move.rel_path, move.destination)
        return Collided(move.rel_path, occupied_reason)
    except OSError as error:
        log.warning("cannot move %s to %s: %s", source, destination, error)
        return Failed(move.rel_path, str(error))
    log.info("moved %s -> %s", move.rel_path, move.destination)
    return Moved(move.rel_path, move.destination)


def _resolve_outcomes(library: Path, plan: LibraryPlan, *, apply: bool) -> dict[str, Outcome]:
    """Every entry's final outcome, keyed by its own ``rel_path``.

    ``Unplaced`` entries carry their own outcome unchanged. A ``Move`` whose
    destination is contested by a :class:`Collision` becomes :class:`Collided`
    immediately, never attempted; an ``InPlace`` entry is exactly as unsafe to
    treat as settled when its own ``rel_path`` is that same contested
    destination, so it becomes :class:`Collided` too, with the same reason.
    Every other ``Move`` is deferred until its destination is not another
    pending move's current path: passes repeat while something resolves, and
    whatever is left once a whole pass makes no progress is a cycle, reported
    the same way.
    """
    contested = {group.destination: group for group in plan.collisions}
    outcomes: dict[str, Outcome] = {}
    pending: dict[str, Move] = {}

    for entry in plan.entries:
        if isinstance(entry, Move):
            group = contested.get(entry.destination)
            if group is not None:
                outcomes[entry.rel_path] = Collided(entry.rel_path, _collision_reason(group))
            else:
                pending[entry.rel_path] = entry
        elif isinstance(entry, InPlace):
            group = contested.get(entry.rel_path)
            if group is not None:
                outcomes[entry.rel_path] = Collided(entry.rel_path, _collision_reason(group))
            else:
                outcomes[entry.rel_path] = entry
        else:
            outcomes[entry.rel_path] = entry

    current_paths = set(pending.keys())
    progress = True
    while pending and progress:
        progress = False
        for rel_path in list(pending.keys()):
            move = pending[rel_path]
            if move.destination in current_paths:
                continue
            outcome = _resolve_move(library, move, apply=apply)
            outcomes[rel_path] = outcome
            del pending[rel_path]
            if isinstance(outcome, Moved):
                current_paths.discard(rel_path)
            progress = True

    for rel_path, move in pending.items():
        reason = f"destination is the current path of {move.destination}"
        outcomes[rel_path] = Collided(rel_path, reason)

    return outcomes


def _report_line(outcome: Outcome) -> str:
    if isinstance(outcome, Moved):
        return f"{outcome.rel_path} -> {outcome.destination}"
    if isinstance(outcome, InPlace):
        return f"{outcome.rel_path} -> in place"
    if isinstance(outcome, Unplaced):
        return f"{outcome.rel_path} -> unsorted: {outcome.reason}"
    if isinstance(outcome, Collided):
        return f"{outcome.rel_path} -> collision: {outcome.reason}"
    if isinstance(outcome, Duplicate):
        return f"{outcome.rel_path} -> duplicate of {outcome.of}"
    return f"{outcome.rel_path} -> failed: {outcome.error}"


def _left_in_place(outcomes: list[Outcome]) -> list[MigrationLeftInPlace]:
    entries: list[MigrationLeftInPlace] = []
    for outcome in outcomes:
        if isinstance(outcome, Unplaced):
            entries.append(MigrationLeftInPlace(rel_path=outcome.rel_path, reason=outcome.reason))
        elif isinstance(outcome, Duplicate):
            entries.append(
                MigrationLeftInPlace(rel_path=outcome.rel_path, reason=f"duplicate of {outcome.of}")
            )
        elif isinstance(outcome, Collided):
            entries.append(MigrationLeftInPlace(rel_path=outcome.rel_path, reason=outcome.reason))
        elif isinstance(outcome, Failed):
            entries.append(MigrationLeftInPlace(rel_path=outcome.rel_path, reason=outcome.error))
    return entries


# ---------------------------------------------------------- empty folders


#: Errno values a folder removal fails on silently: not empty (a declared
#: publication folder still holding its ``publication.yml``, or a sibling
#: file), or not even a directory (a symlink — ``rmdir`` always refuses one).
_PRUNE_SILENT_ERRNOS: tuple[int, ...] = (errno.ENOTEMPTY, errno.EEXIST, errno.ENOTDIR)


def _prune_empty_folders(
    library: Path, moved_rel_paths: list[str], config: PaperstandConfig
) -> list[str]:
    """Remove every folder a move in this run left empty, deepest first.

    Starts from the immediate parent of every moved source and climbs one
    ancestor at a time, stopping at the library root or a configured
    library's own path — never removed — and at the first failure in each
    chain, which ends that chain silently rather than raising.
    """
    stop_at = {library.resolve()}
    for configured in config.libraries:
        stop_at.add((library / configured.path).resolve())

    seen: set[Path] = set()
    candidates: list[Path] = []
    for rel_path in moved_rel_paths:
        parent = (library / rel_path).parent
        if parent not in seen:
            seen.add(parent)
            candidates.append(parent)
    candidates.sort(key=lambda path: len(path.parts), reverse=True)

    removed: list[str] = []
    removed_set: set[Path] = set()
    for start in candidates:
        current = start
        while current not in removed_set and current.resolve() not in stop_at:
            try:
                os.rmdir(current)
            except FileNotFoundError:
                break
            except OSError as error:
                if error.errno not in _PRUNE_SILENT_ERRNOS:
                    log.warning("could not remove empty folder %s: %s", current, error)
                break
            removed_set.add(current)
            removed.append(current.relative_to(library).as_posix())
            current = current.parent
    return removed


# ------------------------------------------------------------ the catalogue


def _catalogue_line(db_path: Path, hashes: dict[str, str | None] | None) -> str:
    if hashes is None:
        return f"{db_path} not readable"
    if not hashes:
        return "none — nothing to preserve"
    hashed = sum(1 for value in hashes.values() if value is not None)
    return f"{db_path} ({len(hashes)} rows, {hashed} hashed)"


def _catalogue_guard(
    stream: TextIO, hashes: dict[str, str | None] | None, movable: list[Move], *, apply: bool
) -> bool:
    """Print the catalogue's warning, if any, and say whether ``apply`` must refuse.

    A dry run never refuses: the same message is printed as a warning and
    the plan is still shown in full. ``apply`` refuses before a single file
    moves, since a rename the catalogue cannot follow loses that file's
    cover and reading progress until the next scan re-derives them from
    scratch.
    """
    if hashes is None:
        print(
            "migrate: the catalogue cannot be read; scan the library once with this version first",
            file=stream,
        )
        return apply

    unhashed = [
        move.rel_path
        for move in movable
        if move.rel_path in hashes and hashes[move.rel_path] is None
    ]
    if unhashed:
        print(
            f"migrate: {len(unhashed)} catalogued files have no content hash yet; "
            "scan the library first, or their covers and reading progress would be lost",
            file=stream,
        )
        for rel_path in unhashed[:10]:
            print(f"  {rel_path}", file=stream)
        return apply
    return False


# ------------------------------------------------------------ the report file


def _iso_utc(timestamp: float) -> str:
    return dt.datetime.fromtimestamp(timestamp, dt.UTC).replace(microsecond=0).isoformat()


def _compact_timestamp(started_at: str) -> str:
    """``started_at`` (an ISO 8601 UTC string) as ``YYYYMMDDTHHMMSSZ``."""
    moment = dt.datetime.fromisoformat(started_at)
    return moment.strftime("%Y%m%dT%H%M%S") + "Z"


def _report_path_for(reports_dir: Path, started_at: str) -> Path:
    """The path this run's report is written to; ``-2``, ``-3``… when the second is taken."""
    base = _compact_timestamp(started_at)
    candidate = reports_dir / f"{base}.json"
    suffix = 2
    while candidate.exists():
        candidate = reports_dir / f"{base}-{suffix}.json"
        suffix += 1
    return candidate


def _write_migration_report(
    reports_dir: Path,
    library: Path,
    report: MigrationReport,
    plan: LibraryPlan,
    started_at: float,
    scan_requested: bool,
) -> None:
    run = MigrationRun(
        started_at=_iso_utc(started_at),
        finished_at=_iso_utc(time.time()),
        library=str(library),
        moved=report.moved,
        in_place=report.in_place,
        unsorted=report.unsorted,
        collision=report.collision,
        duplicate=report.duplicate,
        failed=report.failed,
        moves=[
            OrganizerMove(source=outcome.rel_path, destination=outcome.destination)
            for outcome in report.outcomes
            if isinstance(outcome, Moved)
        ],
        collisions=[
            MigrationCollision(destination=group.destination, sources=list(group.sources))
            for group in plan.collisions
        ],
        left_in_place=_left_in_place(report.outcomes),
        removed_folders=report.removed_folders,
        scan_requested=scan_requested,
    )
    write_run(_report_path_for(reports_dir, run.started_at), run)


# ------------------------------------------------------------------- the run


def _summary_line(report: MigrationReport, *, apply: bool, removed: int) -> str:
    if not apply:
        return (
            f"{report.moved} to move, {report.in_place} in place, {report.unsorted} unsorted, "
            f"{report.collision} collision(s), {report.duplicate} duplicate"
        )
    return (
        f"{report.moved} moved, {report.in_place} in place, {report.unsorted} unsorted, "
        f"{report.collision} collision(s), {report.duplicate} duplicate, {report.failed} failed, "
        f"{removed} empty folder(s) removed"
    )


def migrate_once(
    library: Path,
    config: PaperstandConfig,
    *,
    config_path: Path | None = None,
    db_path: Path,
    apply: bool,
    prune_empty: bool = True,
    out: TextIO | None = None,
    reports_dir: Path | None = None,
    trigger_path: Path | None = None,
) -> MigrationReport:
    """Migrate every already-catalogued PDF under ``library`` to its canonical name and place.

    Refuses before touching anything when the library's root marker is
    remembered but not on disk (``refused == "marker"``), or, in ``apply``
    mode only, when the library could not be walked completely
    (``refused == "walk"``) or when the catalogue cannot be read or a
    movable file's row carries no content hash yet (``refused ==
    "catalogue"``): a partial walk may hide a declared ``publication.yml``
    or a collision source, and the other two would lose track of a file's
    cover and reading progress across the rename. A dry run never refuses
    on an incomplete walk — it prints the same sentence as a warning after
    the summary and carries on. Takes an exclusive, non-blocking lock on
    ``<data>/organizer/.migrate.lock`` for the whole run; another run
    already holding it prints one line and this call returns immediately,
    having moved nothing.

    A report is written under ``reports_dir``, and the scan trigger touched
    at ``trigger_path``, only for an ``apply`` run that actually moved at
    least one file — an idempotent, already-migrated library produces
    neither on a second run.
    """
    stream = out or sys.stdout
    report = MigrationReport()
    mode = "apply" if apply else "dry-run"

    hashes = read_rel_path_hashes(db_path)

    print(f"{'library root:':<14} {library}", file=stream)
    print(
        f"{'configuration:':<14} {config_path if config_path is not None else 'auto-discovered'}",
        file=stream,
    )
    print(f"{'catalogue:':<14} {_catalogue_line(db_path, hashes)}", file=stream)
    print(f"{'mode:':<14} {mode}", file=stream)

    if marker_missing(library, db_path):
        print(
            f"{'library:':<14} marker {MARKER_FILE} missing — is the share mounted? nothing moved",
            file=stream,
        )
        log.warning("the library root %s has no %s marker; nothing moved", library, MARKER_FILE)
        report.refused = "marker"
        return report

    lock_dir = db_path.parent / "organizer"
    lock_dir.mkdir(parents=True, exist_ok=True)
    lock_path = lock_dir / LOCK_NAME
    with lock_path.open("a+") as lock_file:
        try:
            fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            print(f"another migrate run holds {library}; nothing done", file=stream)
            return report

        started_at = time.time()
        print(file=stream)

        plan = plan_library(library, config)
        movable = plan.movable()

        if apply and not plan.walk_complete:
            print(
                "migrate: the library could not be walked completely; nothing moved",
                file=stream,
            )
            report.refused = "walk"
            return report

        if _catalogue_guard(stream, hashes, movable, apply=apply):
            report.refused = "catalogue"
            return report

        outcomes = _resolve_outcomes(library, plan, apply=apply)
        for entry in plan.entries:
            outcome = outcomes[entry.rel_path]
            report.outcomes.append(outcome)
            print(_report_line(outcome), file=stream)

        if plan.collisions:
            print(file=stream)
            for group in plan.collisions:
                print(f"COLLISION {group.destination}", file=stream)
                for source in group.sources:
                    print(f"  {source}", file=stream)

        removed_folders: list[str] = []
        if apply and prune_empty:
            moved_rel_paths = [
                outcome.rel_path for outcome in report.outcomes if isinstance(outcome, Moved)
            ]
            removed_folders = _prune_empty_folders(library, moved_rel_paths, config)
        report.removed_folders = removed_folders
        if removed_folders:
            print(file=stream)
            for rel_path in removed_folders:
                print(f"removed empty folder: {rel_path}", file=stream)

        print(file=stream)
        print(_summary_line(report, apply=apply, removed=len(removed_folders)), file=stream)

        if not plan.walk_complete:
            print(
                "migrate: the library could not be walked completely; nothing moved",
                file=stream,
            )

        if apply and report.moved >= 1:
            scan_requested = False
            if trigger_path is not None:
                try:
                    trigger_path.touch()
                except OSError as error:
                    log.warning("could not touch the scan trigger at %s: %s", trigger_path, error)
                else:
                    scan_requested = True
                    print(f"scan requested: {trigger_path}", file=stream)
                    log.info("touched the scan trigger at %s", trigger_path)
            if reports_dir is not None:
                _write_migration_report(
                    reports_dir, library, report, plan, started_at, scan_requested
                )

    return report
