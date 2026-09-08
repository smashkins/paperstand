"""The scan itself.

Two phases, deliberately unequal.

The **fast phase** never opens a file. It walks the library, compares each PDF's
``(size, mtime_ns)`` with the row already in the database, and only parses the
names that are new or that changed. Files that disappeared take their rows and
their cached images with them, titles left without issues are dropped, and every
surviving group of same-day, same-number files is resolved into one winner and
its duplicates. When the configuration changed since the last scan — a title
added, a library renamed — every row is re-parsed from its path alone, so the
catalogue is reorganised without a single PDF being opened.

The **slow phase** opens the PDFs whose cover is still missing, on a small worker
pool, and records what it finds. A file that cannot be read costs that row an
error message; the scan still ends ``ok``.
"""

from __future__ import annotations

import datetime as dt
import sqlite3
import time
from collections import defaultdict
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, replace
from typing import Literal

from paperstand.config import LibraryConfig, PaperstandConfig, Settings, load_config
from paperstand.db import (
    Database,
    get_meta,
    issue_id,
    library_id,
    open_database,
    set_meta,
    title_id,
    utc_now,
)
from paperstand.logging import get_logger
from paperstand.parsing import ParsedIssue, parse_path
from paperstand.parsing.normalize import strip_accents
from paperstand.scanner.covers import (
    CoverError,
    CoverResult,
    clear_cache,
    has_cover,
    render_cover,
)
from paperstand.scanner.walker import LibraryFile, Walk, top_level_folders

log = get_logger(__name__)

#: How many cover results are written before the transaction is committed.
COVER_COMMIT_BATCH = 20

ISSUE_COLUMNS = (
    "id",
    "library_id",
    "title_id",
    "rel_path",
    "filename",
    "size",
    "mtime_ns",
    "issue_date",
    "date_precision",
    "date_source",
    "issue_number",
    "derived_title",
    "label",
    "matched_rule",
    "has_dedup_suffix",
    "added_at",
    "updated_at",
    "last_seen_scan",
)

#: The columns an existing row has rewritten when its file changed.
UPDATABLE_COLUMNS = ISSUE_COLUMNS[1:-3]

#: The two phases of a scan, in the order they run.
ScanPhase = Literal["catalogue", "covers"]


@dataclass(frozen=True, slots=True)
class ScanProgress:
    """An immutable snapshot of a scan already in progress.

    Published through ``on_progress`` at every phase change, on every file of
    the fast phase and on every rendered cover of the slow one. Frozen, so
    handing one to a reader on another thread is a single attribute
    assignment and needs no lock: the reader either sees the old snapshot or
    the new one, never a mix of the two. ``started_at`` is carried rather
    than an elapsed duration, because a duration frozen at publish time would
    be stale by the time a poller reads it two seconds later; the caller
    computes ``elapsed`` from ``started_at`` when it is read.
    """

    scan_id: int
    phase: ScanPhase
    started_at: str
    files_seen: int = 0
    added: int = 0
    updated: int = 0
    removed: int = 0
    errors: int = 0
    covers_done: int = 0
    covers_total: int | None = None


@dataclass(frozen=True, slots=True)
class ScanResult:
    """The outcome of one scan, mirroring a row of the ``scans`` table."""

    scan_id: int
    status: str
    files_seen: int = 0
    added: int = 0
    updated: int = 0
    removed: int = 0
    covers_done: int = 0
    errors: int = 0
    message: str | None = None
    duration: float = 0.0

    def summary(self) -> str:
        """One line, the way the logs and the command line report a scan."""
        return (
            f"files_seen={self.files_seen} added={self.added} updated={self.updated} "
            f"removed={self.removed} covers_done={self.covers_done} errors={self.errors}"
        )


@dataclass(frozen=True, slots=True)
class _Existing:
    """The little of a stored row the fast phase needs to make its decision."""

    id: str
    size: int
    mtime_ns: int


class Scanner:
    """Catalogues a library root into a database.

    One instance may be used again and again — the scheduler keeps a single one
    — but a scan is never run twice at the same time; that is the scheduler's
    job to guarantee.
    """

    def __init__(
        self,
        settings: Settings,
        database: Database,
        on_progress: Callable[[ScanProgress], None] | None = None,
    ) -> None:
        self.settings = settings
        self.database = database
        self.on_progress = on_progress

    # ------------------------------------------------------------------ public

    def begin(self) -> int:
        """Open a ``scans`` row and return its id.

        Separate from :meth:`run` so that a caller can be handed the id of the
        scan it just asked for before that scan has done anything.
        """
        with self.database.transaction() as connection:
            cursor = connection.execute(
                "INSERT INTO scans (started_at, status) VALUES (?, 'running')",
                (utc_now(),),
            )
        return int(cursor.lastrowid or 0)

    def scan(self) -> ScanResult:
        """Run a complete scan, from the ``scans`` row to the last cover."""
        return self.run(self.begin())

    def run(self, scan_id: int) -> ScanResult:
        """Run both phases of scan ``scan_id`` and close its row."""
        started = time.perf_counter()
        started_at = utc_now()
        try:
            result = self._run(scan_id, started, started_at)
        except Exception as error:  # a failed scan is reported, never raised
            log.exception("scan %d failed", scan_id)
            result = ScanResult(
                scan_id=scan_id,
                status="error",
                message=f"{type(error).__name__}: {error}",
                duration=time.perf_counter() - started,
            )
        self._finish(result)
        return result

    # ----------------------------------------------------------------- phases

    def _run(self, scan_id: int, started: float, started_at: str) -> ScanResult:
        config = load_config(
            self.settings.config_path,
            folder_names=top_level_folders(self.settings.library),
        )
        result = self._fast_phase(scan_id, config, started_at)
        result = replace(result, duration=time.perf_counter() - started)
        log.info(
            "scan %d: fast phase in %.2fs — %s",
            scan_id,
            result.duration,
            result.summary(),
        )
        result = self._slow_phase(result, started_at)
        return replace(result, duration=time.perf_counter() - started)

    def _publish(
        self,
        scan_id: int,
        phase: ScanPhase,
        started_at: str,
        *,
        files_seen: int = 0,
        added: int = 0,
        updated: int = 0,
        removed: int = 0,
        errors: int = 0,
        covers_done: int = 0,
        covers_total: int | None = None,
    ) -> None:
        """Hand a snapshot to ``on_progress``, when there is one to hand it to."""
        if self.on_progress is None:
            return
        self.on_progress(
            ScanProgress(
                scan_id=scan_id,
                phase=phase,
                started_at=started_at,
                files_seen=files_seen,
                added=added,
                updated=updated,
                removed=removed,
                errors=errors,
                covers_done=covers_done,
                covers_total=covers_total,
            )
        )

    def _fast_phase(self, scan_id: int, config: PaperstandConfig, started_at: str) -> ScanResult:
        """Walk, parse what changed, remove what is gone, resolve duplicates."""
        root = self.settings.library
        connection = self.database.connection
        stored_hash = get_meta(connection, "config_hash")
        reparse_all = stored_hash != config.config_hash
        if reparse_all and stored_hash is not None:
            log.info("scan %d: the configuration changed; every row is re-parsed", scan_id)

        with self.database.transaction():
            self._sync_libraries(connection, config)
        titles = _TitleCache(connection)

        existing = {
            str(row["rel_path"]): _Existing(
                id=str(row["id"]), size=int(row["size"]), mtime_ns=int(row["mtime_ns"])
            )
            for row in connection.execute("SELECT id, rel_path, size, mtime_ns FROM issues")
        }

        files_seen = added = updated = errors = 0
        unchanged: list[str] = []
        walk = Walk(root, config)

        def snapshot(*, removed: int = 0) -> None:
            self._publish(
                scan_id,
                "catalogue",
                started_at,
                files_seen=files_seen,
                added=added,
                updated=updated,
                removed=removed,
                errors=errors,
            )

        with self.database.transaction():
            snapshot()
            for found in walk:
                files_seen += 1
                library = config.library_for(found.rel_path)
                if library is None:
                    snapshot()
                    continue
                stored = existing.pop(found.rel_path, None)
                changed = stored is not None and (
                    stored.size != found.size or stored.mtime_ns != found.mtime_ns
                )
                if stored is not None and not changed and not reparse_all:
                    unchanged.append(stored.id)
                    snapshot()
                    continue
                try:
                    self._upsert(
                        connection, scan_id, found, library, config, titles, stored, changed
                    )
                except (sqlite3.Error, ValueError) as error:
                    errors += 1
                    log.warning("scan %d: cannot catalogue %s: %s", scan_id, found.rel_path, error)
                    snapshot()
                    continue
                if stored is None:
                    added += 1
                else:
                    updated += 1
                snapshot()

            connection.executemany(
                "UPDATE issues SET last_seen_scan = ? WHERE id = ?",
                ((scan_id, identifier) for identifier in unchanged),
            )
            # Only files the walk actually looked for may be removed. A root that
            # would not list itself, or a folder that refused to be read, means
            # "no idea", and "no idea" must never be written down as "deleted".
            gone = {
                rel_path: stored for rel_path, stored in existing.items() if walk.covers(rel_path)
            }
            kept = len(existing) - len(gone)
            removed = self._remove(connection, gone)
            titles.drop_empty(connection)
            if walk.complete:
                # A library row is only forgotten when the scan saw the whole
                # root: with no `paperstand.yml` the configuration is discovered
                # from the very folders the walk could not list, and dropping
                # libraries on that basis cascades the catalogue away. The
                # configuration hash is held back for the same reason — an
                # incomplete scan is no evidence that the catalogue is current.
                self._drop_empty_libraries(connection, config)
                set_meta(connection, "config_hash", config.config_hash)
            self._mark_duplicates(connection)
            snapshot(removed=removed)

        message = self._incomplete(scan_id, walk, kept)
        return ScanResult(
            scan_id=scan_id,
            status="ok",
            files_seen=files_seen,
            added=added,
            updated=updated,
            removed=removed,
            errors=errors + (0 if walk.complete else max(1, len(walk.unreadable))),
            message=message,
        )

    def _incomplete(self, scan_id: int, walk: Walk, kept: int) -> str | None:
        """Explain a walk that could not see everything, or ``None`` when it did."""
        if walk.complete:
            return None
        if not walk.root_ok:
            message = (
                f"the library root {walk.root} could not be read; "
                f"{kept} issue(s) were left untouched"
            )
        else:
            folders = ", ".join(sorted(walk.unreadable)[:5])
            message = (
                f"{len(walk.unreadable)} path(s) could not be read ({folders}); "
                f"{kept} issue(s) under them were left untouched"
            )
        log.warning("scan %d: %s", scan_id, message)
        return message

    def _slow_phase(self, result: ScanResult, started_at: str) -> ScanResult:
        """Open every PDF whose cover is still missing, on a worker pool."""
        started = time.perf_counter()
        connection = self.database.connection
        pending = self._pending_covers(connection)
        if not pending:
            return result

        done = failed = 0
        total = len(pending)
        workers = max(1, self.settings.cover_workers)
        cache_root = self.settings.cache_path
        root = self.settings.library

        def snapshot() -> None:
            self._publish(
                result.scan_id,
                "covers",
                started_at,
                files_seen=result.files_seen,
                added=result.added,
                updated=result.updated,
                removed=result.removed,
                errors=result.errors + failed,
                covers_done=done,
                covers_total=total,
            )

        with ThreadPoolExecutor(max_workers=workers, thread_name_prefix="cover") as pool:

            def render(item: tuple[str, str]) -> tuple[str, CoverResult | CoverError]:
                identifier, rel_path = item
                return identifier, render_cover(root / rel_path, identifier, cache_root)

            rendered = pool.map(render, pending)
            snapshot()
            for written, (identifier, outcome) in enumerate(rendered, start=1):
                if isinstance(outcome, CoverError):
                    failed += 1
                    connection.execute(
                        "UPDATE issues SET cover_status = 'error', cover_error = ?, "
                        "updated_at = ? WHERE id = ?",
                        (outcome.message, utc_now(), identifier),
                    )
                else:
                    done += 1
                    connection.execute(
                        "UPDATE issues SET page_count = ?, page_w = ?, page_h = ?, "
                        "first_page_text = ?, cover_status = 'ok', cover_error = NULL, "
                        "updated_at = ? WHERE id = ?",
                        (
                            outcome.page_count,
                            outcome.page_w,
                            outcome.page_h,
                            outcome.first_page_text,
                            utc_now(),
                            identifier,
                        ),
                    )
                if written % COVER_COMMIT_BATCH == 0:
                    connection.commit()
                snapshot()
            connection.commit()

        elapsed = time.perf_counter() - started
        log.info(
            "scan %d: covers in %.2fs — %d rendered, %d failed",
            result.scan_id,
            elapsed,
            done,
            failed,
        )
        return replace(result, covers_done=done, errors=result.errors + failed)

    # ---------------------------------------------------------------- helpers

    def _pending_covers(self, connection: sqlite3.Connection) -> list[tuple[str, str]]:
        """Issues needing a cover: never rendered, or rendered and since lost."""
        cache_root = self.settings.cache_path
        pending = [
            (str(row["id"]), str(row["rel_path"]))
            for row in connection.execute(
                "SELECT id, rel_path FROM issues WHERE cover_status = 'pending' ORDER BY rel_path"
            )
        ]
        pending += [
            (str(row["id"]), str(row["rel_path"]))
            for row in connection.execute(
                "SELECT id, rel_path FROM issues WHERE cover_status = 'ok' ORDER BY rel_path"
            )
            if not has_cover(cache_root, str(row["id"]))
        ]
        return pending

    def _sync_libraries(self, connection: sqlite3.Connection, config: PaperstandConfig) -> None:
        """Write one row per configured library, keeping ``created_at``.

        No suffixing here: two library names that reduce to the same identifier
        are refused when the configuration is loaded, so ``library_id(name)`` is
        the whole answer and every other place may derive it the same way.
        """
        for library in config.libraries:
            identifier = library_id(library.name)
            connection.execute(
                "INSERT INTO libraries (id, name, path, kind, title_source, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?) ON CONFLICT (id) DO UPDATE SET "
                "name = excluded.name, path = excluded.path, kind = excluded.kind, "
                "title_source = excluded.title_source",
                (
                    identifier,
                    library.name,
                    library.path,
                    library.kind,
                    ",".join(config.profile_for(library).title_source),
                    utc_now(),
                ),
            )

    def _upsert(
        self,
        connection: sqlite3.Connection,
        scan_id: int,
        found: LibraryFile,
        library: LibraryConfig,
        config: PaperstandConfig,
        titles: _TitleCache,
        stored: _Existing | None,
        changed: bool,
    ) -> None:
        """Parse one file and write its row, resetting the cover when needed."""
        mtime = dt.datetime.fromtimestamp(found.mtime_ns / 1_000_000_000)
        issue = parse_path(found.rel_path, library, config.profile_for(library), mtime)
        owner = library_id(library.name)
        title = titles.resolve(connection, owner, library.kind, issue)
        now = utc_now()
        identifier = stored.id if stored is not None else issue_id(found.rel_path)
        values = (
            identifier,
            owner,
            title,
            found.rel_path,
            found.filename,
            found.size,
            found.mtime_ns,
            issue.issue_date.isoformat() if issue.issue_date is not None else None,
            issue.date_precision,
            issue.date_source,
            str(issue.issue_number) if issue.issue_number is not None else None,
            issue.derived_title,
            issue.label,
            issue.matched_rule,
            int(issue.has_dedup_suffix),
            now,
            now,
            scan_id,
        )
        if stored is None:
            placeholders = ", ".join("?" * len(ISSUE_COLUMNS))
            connection.execute(
                f"INSERT INTO issues ({', '.join(ISSUE_COLUMNS)}) VALUES ({placeholders})",
                values,
            )
            return

        # Everything but the id and the three bookkeeping columns at the end:
        # `added_at` is when the issue joined the catalogue and never moves.
        assignments = ", ".join(f"{column} = ?" for column in UPDATABLE_COLUMNS)
        connection.execute(
            f"UPDATE issues SET {assignments}, updated_at = ?, last_seen_scan = ? WHERE id = ?",
            (*values[1:-3], now, scan_id, identifier),
        )
        if changed:
            # The bytes changed, so everything rendered from them is stale.
            clear_cache(self.settings.cache_path, identifier)
            connection.execute(
                "UPDATE issues SET cover_status = 'pending', cover_error = NULL, "
                "page_count = NULL, page_w = NULL, page_h = NULL, first_page_text = NULL "
                "WHERE id = ?",
                (identifier,),
            )

    def _remove(self, connection: sqlite3.Connection, gone: dict[str, _Existing]) -> int:
        """Delete the rows of files that are no longer there, and their caches."""
        if not gone:
            return 0
        cache_root = self.settings.cache_path
        for rel_path, stored in sorted(gone.items()):
            log.debug("removing %s", rel_path)
            clear_cache(cache_root, stored.id)
        connection.executemany(
            "DELETE FROM issues WHERE id = ?",
            ((stored.id,) for stored in gone.values()),
        )
        return len(gone)

    @staticmethod
    def _drop_empty_libraries(connection: sqlite3.Connection, config: PaperstandConfig) -> None:
        """Forget libraries that the configuration no longer describes."""
        keep = {library_id(library.name) for library in config.libraries}
        stale = [
            str(row["id"])
            for row in connection.execute("SELECT id FROM libraries")
            if str(row["id"]) not in keep
        ]
        connection.executemany(
            "DELETE FROM libraries WHERE id = ?", ((identifier,) for identifier in stale)
        )

    @staticmethod
    def _mark_duplicates(connection: sqlite3.Connection) -> None:
        """Resolve every same-title, same-date, same-number group into one winner.

        The copy whose name carries no dedup suffix wins; when they all do — or
        none does — the largest file wins, which is the one most likely to be
        the complete edition.
        """
        groups: dict[tuple[str, str, str], list[sqlite3.Row]] = defaultdict(list)
        for row in connection.execute(
            "SELECT id, title_id, issue_date, issue_number, has_dedup_suffix, size, "
            "duplicate_of FROM issues ORDER BY id"
        ):
            key = (
                str(row["title_id"]),
                str(row["issue_date"]),
                str(row["issue_number"]),
            )
            groups[key].append(row)

        updates: list[tuple[str | None, str]] = []
        for members in groups.values():
            if len(members) == 1:
                winner_id = str(members[0]["id"])
            else:
                winner = min(
                    members,
                    key=lambda row: (
                        int(row["has_dedup_suffix"]),
                        -int(row["size"]),
                        str(row["id"]),
                    ),
                )
                winner_id = str(winner["id"])
            for row in members:
                identifier = str(row["id"])
                duplicate_of = None if identifier == winner_id else winner_id
                if row["duplicate_of"] != duplicate_of:
                    updates.append((duplicate_of, identifier))
        connection.executemany("UPDATE issues SET duplicate_of = ? WHERE id = ?", updates)
        Scanner._migrate_progress(
            connection,
            [(winner, loser) for winner, loser in updates if winner is not None],
        )

    @staticmethod
    def _migrate_progress(connection: sqlite3.Connection, losers: list[tuple[str, str]]) -> None:
        """Hand a reader's position from a copy to the issue that won.

        A file catalogued on its own and read, then joined by a better copy of
        the same issue, becomes a duplicate — and duplicates are hidden from
        every list, including "continue reading". Losing the bookmark to that is
        not something a reader would understand, so it moves to the winner.

        Only onto a winner that has none: a position somebody actually set on
        the issue that stayed is never overwritten by a copy's, and in that case
        the copy keeps its own row, which costs nothing and is still there if the
        two ever change places.
        """
        for winner, loser in losers:
            row = connection.execute(
                "SELECT page, page_count, updated_at FROM reading_progress WHERE issue_id = ?",
                (loser,),
            ).fetchone()
            if row is None:
                continue
            cursor = connection.execute(
                "INSERT INTO reading_progress (issue_id, page, page_count, updated_at) "
                "VALUES (?, ?, ?, ?) ON CONFLICT (issue_id) DO NOTHING",
                (winner, row["page"], row["page_count"], row["updated_at"]),
            )
            if cursor.rowcount:
                connection.execute("DELETE FROM reading_progress WHERE issue_id = ?", (loser,))
                log.info("moved the reading position of %s onto %s", loser, winner)

    def _finish(self, result: ScanResult) -> None:
        """Close the ``scans`` row of a finished scan."""
        try:
            with self.database.transaction() as connection:
                connection.execute(
                    "UPDATE scans SET finished_at = ?, status = ?, files_seen = ?, added = ?, "
                    "updated = ?, removed = ?, covers_done = ?, errors = ?, message = ? "
                    "WHERE id = ?",
                    (
                        utc_now(),
                        result.status,
                        result.files_seen,
                        result.added,
                        result.updated,
                        result.removed,
                        result.covers_done,
                        result.errors,
                        result.message,
                        result.scan_id,
                    ),
                )
        except sqlite3.Error:  # pragma: no cover - the database went away mid-scan
            log.exception("scan %d could not be closed", result.scan_id)
            return
        log.info(
            "scan %d %s in %.2fs — %s",
            result.scan_id,
            result.status,
            result.duration,
            result.summary(),
        )


@dataclass(slots=True)
class _Title:
    """A title row as the cache remembers it."""

    id: str
    sort_name: str
    kind: str
    source: str


class _TitleCache:
    """Get-or-create for titles, with the ids it hands out kept unique.

    Titles, unlike libraries, are not named by the user, so two names that
    reduce to the same identifier get ``-2``, ``-3`` suffixes rather than an
    error.
    """

    def __init__(self, connection: sqlite3.Connection) -> None:
        self._by_key: dict[tuple[str, str], _Title] = {}
        self._ids: set[str] = set()
        self._refreshed: set[str] = set()
        for row in connection.execute(
            "SELECT id, library_id, name, sort_name, kind, source FROM titles"
        ):
            identifier = str(row["id"])
            self._by_key[(str(row["library_id"]), str(row["name"]))] = _Title(
                id=identifier,
                sort_name=str(row["sort_name"]),
                kind=str(row["kind"]),
                source=str(row["source"]),
            )
            self._ids.add(identifier)

    def resolve(
        self,
        connection: sqlite3.Connection,
        owner: str,
        kind: str,
        issue: ParsedIssue,
    ) -> str:
        """The id of ``issue``'s title, inserting or refreshing the title row.

        A title that already exists keeps its id — cache paths and future URLs
        depend on it — but not its classification: a name that used to be derived
        from a folder and is now a configured title, or a library that changed
        kind, has to be recorded as it is today. The refresh happens once per
        title per scan, on the first issue that mentions it, so a title whose
        issues disagree about where the name came from does not flap.
        """
        key = (owner, issue.title_name)
        wanted = _Title(
            id="",
            sort_name=sort_name(issue.title_name),
            kind=kind,
            source=issue.title_source,
        )
        known = self._by_key.get(key)
        if known is not None:
            self._refresh(connection, known, wanted)
            return known.id

        identifier = _unique(title_id(owner, issue.title_name), self._ids)
        connection.execute(
            "INSERT INTO titles (id, library_id, name, sort_name, kind, source, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (
                identifier,
                owner,
                issue.title_name,
                wanted.sort_name,
                wanted.kind,
                wanted.source,
                utc_now(),
            ),
        )
        wanted.id = identifier
        self._by_key[key] = wanted
        self._ids.add(identifier)
        self._refreshed.add(identifier)
        return identifier

    def _refresh(self, connection: sqlite3.Connection, known: _Title, wanted: _Title) -> None:
        if known.id in self._refreshed:
            return
        self._refreshed.add(known.id)
        if (known.sort_name, known.kind, known.source) == (
            wanted.sort_name,
            wanted.kind,
            wanted.source,
        ):
            return
        connection.execute(
            "UPDATE titles SET sort_name = ?, kind = ?, source = ? WHERE id = ?",
            (wanted.sort_name, wanted.kind, wanted.source, known.id),
        )
        known.sort_name, known.kind, known.source = (
            wanted.sort_name,
            wanted.kind,
            wanted.source,
        )

    def drop_empty(self, connection: sqlite3.Connection) -> None:
        """Delete titles no issue points at any more."""
        cursor = connection.execute(
            "DELETE FROM titles WHERE id NOT IN (SELECT DISTINCT title_id FROM issues)"
        )
        if not cursor.rowcount:
            return
        log.debug("dropped %d title(s) left without issues", cursor.rowcount)
        live = {str(row["id"]) for row in connection.execute("SELECT id FROM titles")}
        self._by_key = {key: title for key, title in self._by_key.items() if title.id in live}
        self._ids &= live
        self._refreshed &= live


def sort_name(name: str) -> str:
    """The form a title is sorted by: lowercase, unaccented, spaces kept."""
    return " ".join(strip_accents(name).casefold().split())


def _unique(base: str, taken: set[str]) -> str:
    """``base``, or ``base-2``, ``base-3``… when it is already spoken for."""
    if base not in taken:
        return base
    suffix = 2
    while f"{base}-{suffix}" in taken:
        suffix += 1
    return f"{base}-{suffix}"


def scan_once(settings: Settings) -> ScanResult:
    """Open the database, run one complete scan, close it again."""
    database = open_database(settings.db_path)
    try:
        return Scanner(settings, database).scan()
    finally:
        database.close()
