"""The scan itself.

Two phases, deliberately unequal.

The **fast phase** walks the library and compares each PDF's ``(size, mtime_ns)``
with the row already in the database; that pair alone decides whether a file
needs a closer look. A file that does — new, touched or genuinely changed — is
opened just long enough to hash it, because an issue's identity is its content,
not its path: a rename or a move keeps the same id, cover, pages and reading
position, a byte-identical copy is a duplicate wherever it sits, and only
different bytes make a new issue. A file whose stat has not moved and already
carries a hash is never reopened. Files that disappeared take their rows and
their cached images with them — unless the same content turns up again
elsewhere in the same scan, in which case the row moves rather than being
replaced — titles left without issues are dropped, and every surviving group of
same-day, same-number files is resolved into one winner and its duplicates.
When the configuration changed since the last scan — a title added, a library
renamed — every row that already carries a hash is re-parsed from its path
alone, without being reopened.

The **slow phase** opens the PDFs whose cover is still missing, on a small worker
pool, and records what it finds. A file that cannot be read costs that row an
error message; the scan still ends ``ok``.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import json
import sqlite3
import time
from collections import defaultdict
from collections.abc import Callable, Iterable
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
from paperstand.parsing.profile import canonical_pattern
from paperstand.publication import DeclaredPublication, PublicationIndex
from paperstand.scanner.covers import (
    CoverError,
    CoverResult,
    clear_cache,
    has_cover,
    move_cache,
    render_cover,
)
from paperstand.scanner.hashing import content_hash
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
    "variant",
    "volume",
    "content_hash",
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

    ``covers_done`` counts successes only, so it still equals the finished
    scan's own ``covers_done`` — a cover the pool could not render is
    ``covers_failed``, and a caller drawing "N of M" wants both: a broken PDF
    must still let the bar, and the count beside it, reach ``covers_total``.
    """

    scan_id: int
    phase: ScanPhase
    started_at: str
    files_seen: int = 0
    added: int = 0
    updated: int = 0
    removed: int = 0
    errors: int = 0
    hashed: int = 0
    covers_done: int = 0
    covers_failed: int = 0
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
    hashed: int = 0
    message: str | None = None
    duration: float = 0.0

    def summary(self) -> str:
        """One line, the way the logs and the command line report a scan."""
        return (
            f"files_seen={self.files_seen} added={self.added} updated={self.updated} "
            f"removed={self.removed} covers_done={self.covers_done} errors={self.errors} "
            f"hashed={self.hashed}"
        )


@dataclass(frozen=True, slots=True)
class _Existing:
    """The little of a stored row the fast phase needs to make its decision."""

    id: str
    size: int
    mtime_ns: int
    content_hash: str | None
    """``None`` marks a legacy row, written before schema 3, still waiting to
    be hashed once — the fast phase's own backfill, not a migration script."""


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

    def started_at(self, scan_id: int) -> str:
        """The instant ``begin`` opened ``scan_id`` — read back, not regenerated.

        ``run`` and the scheduler's seed snapshot both need this timestamp,
        and two independent ``utc_now()`` calls a few milliseconds apart would
        let the elapsed timer jump when the first real snapshot replaces the
        seed. The ``scans`` row is the one place it is stored, so both read it
        back from there rather than each keeping — and risking losing sync
        with — a copy of their own.
        """
        row = self.database.connection.execute(
            "SELECT started_at FROM scans WHERE id = ?", (scan_id,)
        ).fetchone()
        return str(row["started_at"]) if row is not None else utc_now()

    def scan(self) -> ScanResult:
        """Run a complete scan, from the ``scans`` row to the last cover."""
        return self.run(self.begin())

    def run(self, scan_id: int) -> ScanResult:
        """Run both phases of scan ``scan_id`` and close its row."""
        started = time.perf_counter()
        started_at = self.started_at(scan_id)
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
        hashed: int = 0,
        covers_done: int = 0,
        covers_failed: int = 0,
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
                hashed=hashed,
                covers_done=covers_done,
                covers_failed=covers_failed,
                covers_total=covers_total,
            )
        )

    def _fast_phase(self, scan_id: int, config: PaperstandConfig, started_at: str) -> ScanResult:
        """Walk, hash what needs it, remove what is gone, resolve duplicates."""
        root = self.settings.library
        connection = self.database.connection
        stored_hash = get_meta(connection, "config_hash")

        with self.database.transaction():
            self._sync_libraries(connection, config)
        titles = _TitleCache(connection)

        existing = {
            str(row["rel_path"]): _Existing(
                id=str(row["id"]),
                size=int(row["size"]),
                mtime_ns=int(row["mtime_ns"]),
                content_hash=row["content_hash"],
            )
            for row in connection.execute(
                "SELECT id, rel_path, size, mtime_ns, content_hash FROM issues"
            )
        }
        # Every id already spoken for, kept apart from `existing` so that
        # popping a matched path never shrinks it: an id stays taken for the
        # whole scan, whether its row was just matched or not touched at all.
        taken = {item.id for item in existing.values()}
        legacy = sum(1 for item in existing.values() if item.content_hash is None)
        if legacy:
            log.info(
                "scan %d: %d row(s) predate content hashing and will be hashed once",
                scan_id,
                legacy,
            )

        files_seen = added = updated = removed = errors = hashed = 0
        unchanged: list[str] = []
        # Files whose path matched no row in pass one, carried into pass two
        # once `gone` is known: only then can a rename or a move be told apart
        # from an unrelated arrival. A replacement already has its hash by the
        # time it is queued, so pass two never reads that file a second time.
        queue: list[tuple[LibraryFile, LibraryConfig, DeclaredPublication | None, str | None]] = []

        def snapshot() -> None:
            self._publish(
                scan_id,
                "catalogue",
                started_at,
                files_seen=files_seen,
                added=added,
                updated=updated,
                removed=removed,
                errors=errors,
                hashed=hashed,
            )

        snapshot()
        # The walk is buffered rather than processed as it streams: every
        # `publication.yml` it finds has to be known before a single file is
        # upserted, because the digest of the whole set is folded into the
        # hash that decides whether everything gets re-parsed. `files_seen`
        # still climbs snapshot by snapshot while this runs, so a caller
        # watching progress sees the walk itself, not a stall before it.
        walk = Walk(root, config)
        buffered: list[LibraryFile] = []
        for found in walk:
            files_seen += 1
            buffered.append(found)
            snapshot()

        index = PublicationIndex(root)
        digest = index.load_all(walk.publications)
        catalogue_hash = hashlib.sha256(f"{config.config_hash}\n{digest}".encode()).hexdigest()
        reparse_all = stored_hash != catalogue_hash
        if reparse_all and stored_hash is not None:
            log.info("scan %d: the configuration changed; every row is re-parsed", scan_id)
        self._warn_missing_canonical_pattern(scan_id, config, walk.publications)

        with self.database.transaction():
            # Pass one: every file whose path is already catalogued.
            for found in buffered:
                library = config.library_for(found.rel_path)
                if library is None:
                    snapshot()
                    continue
                stored = existing.pop(found.rel_path, None)
                publication = index.resolve(found.publication_dir, library, config)
                if stored is None:
                    queue.append((found, library, publication, None))
                    snapshot()
                    continue

                try:
                    if stored.content_hash is None:
                        # A legacy row: there was never a hash to compare
                        # against, so whatever this path holds now becomes its
                        # content identity, once.
                        stale = stored.size != found.size or stored.mtime_ns != found.mtime_ns
                        found_hash = content_hash(root / found.rel_path)
                        stored = self._rewrite_id(connection, stored, found_hash, taken)
                        self._upsert(
                            connection,
                            scan_id,
                            found,
                            library,
                            config,
                            titles,
                            stored,
                            found_hash,
                            publication,
                            taken,
                        )
                        if stale:
                            # The file changed while no hash was on record, so
                            # there is no telling whether the bytes did too:
                            # nothing rendered from them can be trusted.
                            self._reset_derivatives(connection, stored.id)
                        hashed += 1
                        updated += 1
                    elif stored.size == found.size and stored.mtime_ns == found.mtime_ns:
                        if not reparse_all:
                            unchanged.append(stored.id)
                            snapshot()
                            continue
                        self._upsert(
                            connection,
                            scan_id,
                            found,
                            library,
                            config,
                            titles,
                            stored,
                            stored.content_hash,
                            publication,
                            taken,
                        )
                        updated += 1
                    else:
                        found_hash = content_hash(root / found.rel_path)
                        if found_hash == stored.content_hash:
                            # A touch: the bytes are exactly what they were.
                            self._upsert(
                                connection,
                                scan_id,
                                found,
                                library,
                                config,
                                titles,
                                stored,
                                found_hash,
                                publication,
                                taken,
                            )
                            updated += 1
                        else:
                            # A replacement is a new issue; the old one leaves
                            # nothing for it to inherit.
                            removed += self._remove(connection, {found.rel_path: stored})
                            queue.append((found, library, publication, found_hash))
                except (sqlite3.Error, ValueError, OSError) as error:
                    errors += 1
                    log.warning("scan %d: cannot catalogue %s: %s", scan_id, found.rel_path, error)
                    snapshot()
                    continue
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

            # Pass two: files no row was matched to in pass one. A gone row
            # sharing a queued file's content hash is that same issue, moved —
            # never a deletion plus an unrelated arrival.
            by_hash: dict[str, list[str]] = defaultdict(list)
            for rel_path, stored in gone.items():
                if stored.content_hash is not None:
                    by_hash[stored.content_hash].append(rel_path)
            for paths in by_hash.values():
                paths.sort()

            for found, library, publication, precomputed in queue:
                try:
                    found_hash = (
                        precomputed
                        if precomputed is not None
                        else content_hash(root / found.rel_path)
                    )
                    matches = by_hash.get(found_hash)
                    if matches:
                        old_rel_path = matches.pop(0)
                        moved = gone.pop(old_rel_path)
                        self._upsert(
                            connection,
                            scan_id,
                            found,
                            library,
                            config,
                            titles,
                            moved,
                            found_hash,
                            publication,
                            taken,
                        )
                        updated += 1
                        log.info("scan %d: %s moved to %s", scan_id, old_rel_path, found.rel_path)
                    else:
                        self._upsert(
                            connection,
                            scan_id,
                            found,
                            library,
                            config,
                            titles,
                            None,
                            found_hash,
                            publication,
                            taken,
                        )
                        added += 1
                except (sqlite3.Error, ValueError, OSError) as error:
                    errors += 1
                    log.warning("scan %d: cannot catalogue %s: %s", scan_id, found.rel_path, error)
                snapshot()

            # A row about to be removed hands its reading position to a row
            # that shares its content hash, if one is still live: the copy
            # that stays is the one a reader would expect to pick up from.
            for stored in gone.values():
                if stored.content_hash is None:
                    continue
                survivor = connection.execute(
                    "SELECT id FROM issues WHERE content_hash = ? AND id != ?",
                    (stored.content_hash, stored.id),
                ).fetchone()
                if survivor is not None:
                    self._migrate_progress(connection, [(str(survivor["id"]), stored.id)])

            removed += self._remove(connection, gone)
            titles.drop_empty(connection)
            if walk.complete:
                # A library row is only forgotten when the scan saw the whole
                # root: with no `paperstand.yml` the configuration is discovered
                # from the very folders the walk could not list, and dropping
                # libraries on that basis cascades the catalogue away. The
                # catalogue hash is held back for the same reason — an
                # incomplete scan is no evidence that the catalogue is current.
                self._drop_empty_libraries(connection, config)
                set_meta(connection, "config_hash", catalogue_hash)
            self._mark_duplicates(connection)
            # Folded in before the final snapshot, not after, so that snapshot
            # and the ScanResult below report the same total: an unreadable
            # path is an error the scan found, even though no single file
            # upsert ever raised for it.
            if not walk.complete:
                errors += max(1, len(walk.unreadable))
            snapshot()

        message = self._incomplete(scan_id, walk, kept)
        return ScanResult(
            scan_id=scan_id,
            status="ok",
            files_seen=files_seen,
            added=added,
            updated=updated,
            removed=removed,
            errors=errors,
            hashed=hashed,
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

    @staticmethod
    def _warn_missing_canonical_pattern(
        scan_id: int, config: PaperstandConfig, publications: Iterable[str]
    ) -> None:
        """One warning per scan per library whose profile has lost the grammar.

        A custom profile that replaces ``patterns:`` without a leading ``"+"``
        loses the two bundled volume patterns *and* the declared-publication
        one along with them. The declaration itself still applies — every file
        beneath the folder is filed under the declared title — but the volume
        and the variant are no longer read off a canonical name, so a
        supplement sharing its date with the plain issue is marked its
        duplicate. Silent, unless a library actually holds a publication
        folder; hence the warning.
        """
        warned: set[str] = set()
        for folder in publications:
            library = config.library_for(folder)
            if library is None or library.name in warned:
                continue
            if canonical_pattern() not in config.profile_for(library).patterns:
                warned.add(library.name)
                log.warning(
                    "scan %d: library %r uses parser %r, which does not include the "
                    "canonical declared-publication pattern; files under its "
                    "publication.yml folders keep the declared title but lose the "
                    'volume and the variant (add "+" to the profile\'s patterns)',
                    scan_id,
                    library.name,
                    library.parser,
                )

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
                hashed=result.hashed,
                covers_done=done,
                covers_failed=failed,
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
        digest: str,
        publication: DeclaredPublication | None,
        taken: set[str],
    ) -> None:
        """Parse one file and write its row, keeping an existing id or minting one.

        ``digest`` is always supplied by the caller, the only place that knows
        whether a file actually needs (re)hashing: pass one reuses a stored
        hash for a touch or a plain re-parse, pass two always has a fresh one.
        `_upsert` itself never opens a file, and never clears a cache — a row
        it writes either keeps the bytes its cache was rendered from, or is a
        brand new row whose cover starts out pending like any other.
        """
        mtime = dt.datetime.fromtimestamp(found.mtime_ns / 1_000_000_000)
        issue = parse_path(found.rel_path, library, config.profile_for(library), mtime, publication)
        owner = library_id(library.name)
        kind = publication.kind_for(library.kind) if publication is not None else library.kind
        title = titles.resolve(connection, owner, kind, issue, publication)
        now = utc_now()
        if stored is not None:
            identifier = stored.id
        else:
            # Two different paths sharing the same bytes derive the same id;
            # the second one found falls back to `_unique`, exactly like a
            # colliding title name does.
            identifier = _unique(issue_id(digest), taken)
            taken.add(identifier)
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
            issue.variant,
            issue.volume,
            digest,
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

    def _reset_derivatives(self, connection: sqlite3.Connection, identifier: str) -> None:
        """Forget everything rendered from a row's bytes: cache, cover, pages."""
        clear_cache(self.settings.cache_path, identifier)
        connection.execute(
            "UPDATE issues SET cover_status = 'pending', cover_error = NULL, "
            "page_count = NULL, page_w = NULL, page_h = NULL, first_page_text = NULL "
            "WHERE id = ?",
            (identifier,),
        )

    def _rewrite_id(
        self, connection: sqlite3.Connection, stored: _Existing, digest: str, taken: set[str]
    ) -> _Existing:
        """Give a legacy row the id its content always implied.

        Called once per row, the scan its hash is first computed. SQLite
        refuses to update a primary key while another row's ``duplicate_of``
        still references it, so those pointers are cleared first —
        :meth:`_mark_duplicates` recomputes them before the scan ends — and
        the row's cache and any reading position move with it. A digest that
        happens to already collide with a *different* live row's id is the
        same case a brand new file falls back to `_unique` for.
        """
        candidate = issue_id(digest)
        if candidate == stored.id:
            new_id = stored.id
        else:
            new_id = _unique(candidate, taken - {stored.id})
            taken.discard(stored.id)
            taken.add(new_id)

        if new_id == stored.id:
            connection.execute("UPDATE issues SET content_hash = ? WHERE id = ?", (digest, new_id))
            return replace(stored, content_hash=digest)

        connection.execute(
            "UPDATE issues SET duplicate_of = NULL WHERE duplicate_of = ?", (stored.id,)
        )
        connection.execute(
            "UPDATE issues SET id = ?, content_hash = ? WHERE id = ?", (new_id, digest, stored.id)
        )
        move_cache(self.settings.cache_path, stored.id, new_id)
        self._migrate_progress(connection, [(new_id, stored.id)])
        return replace(stored, id=new_id, content_hash=digest)

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
    def _pick_winner(members: list[sqlite3.Row]) -> sqlite3.Row:
        """The one member of a duplicate group that is not a duplicate.

        The copy whose name carries no dedup suffix wins; when they all do —
        or none does — the largest file wins, which is the one most likely
        to be the complete edition; a further tie is broken by the id, so
        the choice is deterministic.
        """
        return min(
            members,
            key=lambda row: (int(row["has_dedup_suffix"]), -int(row["size"]), str(row["id"])),
        )

    @staticmethod
    def _mark_duplicates(connection: sqlite3.Connection) -> None:
        """Resolve duplicates in two levels, then flatten every pointer.

        First, rows sharing a non-``NULL`` content hash are the same issue
        regardless of how their names parsed — the byte-identical copy in an
        unrelated folder is exactly as much a duplicate as the dedup-suffixed
        one beside the original. One is kept and the rest point at it,
        taking no further part below. Second, whatever survives that — one
        row per distinct set of bytes — is grouped the way it always was, by
        the title's own ``issue_key``; the variant is always part of that
        key, so a supplement never looks like a duplicate of the plain issue
        it shares a date with.

        A row that wins at the first level can still lose at the second, so
        every stored pointer is flattened here onto the row that is not
        itself a duplicate of anything — a reader, or an API response, is
        never left to follow a chain.
        """
        rows = connection.execute(
            "SELECT i.id, i.title_id, i.issue_date, i.issue_number, i.variant, "
            "i.has_dedup_suffix, i.size, i.duplicate_of, i.content_hash, t.issue_key "
            "FROM issues i JOIN titles t ON t.id = i.title_id ORDER BY i.id"
        ).fetchall()
        by_id = {str(row["id"]): row for row in rows}

        hash_groups: dict[str, list[sqlite3.Row]] = defaultdict(list)
        for row in rows:
            if row["content_hash"] is not None:
                hash_groups[str(row["content_hash"])].append(row)

        hash_pairs: list[tuple[str, str]] = []
        losers: set[str] = set()
        for members in hash_groups.values():
            if len(members) < 2:
                continue
            winner_id = str(Scanner._pick_winner(members)["id"])
            for row in members:
                identifier = str(row["id"])
                if identifier != winner_id:
                    hash_pairs.append((winner_id, identifier))
                    losers.add(identifier)

        key_groups: dict[tuple[str, str, str, str], list[sqlite3.Row]] = defaultdict(list)
        for row in rows:
            identifier = str(row["id"])
            if identifier in losers:
                continue  # already resolved at the content level
            key = _duplicate_key(
                str(row["title_id"]),
                row["issue_date"],
                row["issue_number"],
                row["variant"],
                row["issue_key"],
            )
            key_groups[key].append(row)

        key_pairs: list[tuple[str, str]] = []
        for members in key_groups.values():
            if len(members) < 2:
                continue
            winner_id = str(Scanner._pick_winner(members)["id"])
            for row in members:
                identifier = str(row["id"])
                if identifier != winner_id:
                    key_pairs.append((winner_id, identifier))

        # `hash_pairs`/`key_pairs` are `(winner, loser)`, matching what
        # `_migrate_progress` expects; the pointer a duplicate is looked up by
        # runs the other way, from the loser to whatever it points at.
        pointer: dict[str, str] = {loser: winner for winner, loser in hash_pairs}
        pointer.update({loser: winner for winner, loser in key_pairs})

        def root(identifier: str) -> str:
            seen = {identifier}
            while identifier in pointer:
                identifier = pointer[identifier]
                if identifier in seen:  # pragma: no cover - a cycle cannot arise here
                    break
                seen.add(identifier)
            return identifier

        updates: list[tuple[str | None, str]] = []
        for identifier, row in by_id.items():
            duplicate_of = root(identifier) if identifier in pointer else None
            if row["duplicate_of"] != duplicate_of:
                updates.append((duplicate_of, identifier))
        connection.executemany("UPDATE issues SET duplicate_of = ? WHERE id = ?", updates)

        # Hash pairs first, then key pairs: a position moved onto a row that
        # then turns out to be a duplicate itself is picked straight back up
        # by the second call, walking the whole chain within one scan.
        Scanner._migrate_progress(connection, hash_pairs)
        Scanner._migrate_progress(connection, key_pairs)

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
    slug: str | None = None
    frequency: str | None = None
    language: str | None = None
    issue_key: str | None = None
    parent_slug: str | None = None
    supplements: str | None = None
    """JSON array text, or ``None`` when the title is not declared."""


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
            "SELECT id, library_id, name, sort_name, kind, source, slug, frequency, "
            "language, issue_key, parent_slug, supplements FROM titles"
        ):
            identifier = str(row["id"])
            self._by_key[(str(row["library_id"]), str(row["name"]))] = _Title(
                id=identifier,
                sort_name=str(row["sort_name"]),
                kind=str(row["kind"]),
                source=str(row["source"]),
                slug=row["slug"],
                frequency=row["frequency"],
                language=row["language"],
                issue_key=row["issue_key"],
                parent_slug=row["parent_slug"],
                supplements=row["supplements"],
            )
            self._ids.add(identifier)

    def resolve(
        self,
        connection: sqlite3.Connection,
        owner: str,
        kind: str,
        issue: ParsedIssue,
        publication: DeclaredPublication | None = None,
    ) -> str:
        """The id of ``issue``'s title, inserting or refreshing the title row.

        A title that already exists keeps its id — cache paths and future URLs
        depend on it — but not its classification: a name that used to be derived
        from a folder and is now a configured title, or a library that changed
        kind, has to be recorded as it is today. The refresh happens once per
        title per scan, on the first issue that mentions it — except a
        ``publication`` source, which always applies: a canonical file may sort
        after a non-canonical one belonging to the same title within the same
        scan, and the row still has to end up describing the declaration, not
        whichever file the walk reached first.
        """
        key = (owner, issue.title_name)
        wanted = _Title(
            id="",
            sort_name=sort_name(issue.title_name),
            kind=kind,
            source=issue.title_source,
            slug=publication.slug if publication is not None else None,
            frequency=publication.config.frequency if publication is not None else None,
            language=publication.config.language if publication is not None else None,
            issue_key=publication.config.issue_key if publication is not None else None,
            parent_slug=publication.config.parent if publication is not None else None,
            supplements=(
                json.dumps(publication.config.supplements) if publication is not None else None
            ),
        )
        known = self._by_key.get(key)
        if known is not None:
            self._refresh(connection, known, wanted)
            return known.id

        identifier = _unique(title_id(owner, issue.title_name), self._ids)
        connection.execute(
            "INSERT INTO titles (id, library_id, name, sort_name, kind, source, slug, "
            "frequency, language, issue_key, parent_slug, supplements, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                identifier,
                owner,
                issue.title_name,
                wanted.sort_name,
                wanted.kind,
                wanted.source,
                wanted.slug,
                wanted.frequency,
                wanted.language,
                wanted.issue_key,
                wanted.parent_slug,
                wanted.supplements,
                utc_now(),
            ),
        )
        wanted.id = identifier
        self._by_key[key] = wanted
        self._ids.add(identifier)
        self._refreshed.add(identifier)
        return identifier

    def _refresh(self, connection: sqlite3.Connection, known: _Title, wanted: _Title) -> None:
        already_refreshed = known.id in self._refreshed
        if already_refreshed and (known.source == "publication" or wanted.source != "publication"):
            return
        self._refreshed.add(known.id)
        fields = (
            wanted.sort_name,
            wanted.kind,
            wanted.source,
            wanted.slug,
            wanted.frequency,
            wanted.language,
            wanted.issue_key,
            wanted.parent_slug,
            wanted.supplements,
        )
        if (
            known.sort_name,
            known.kind,
            known.source,
            known.slug,
            known.frequency,
            known.language,
            known.issue_key,
            known.parent_slug,
            known.supplements,
        ) == fields:
            return
        connection.execute(
            "UPDATE titles SET sort_name = ?, kind = ?, source = ?, slug = ?, frequency = ?, "
            "language = ?, issue_key = ?, parent_slug = ?, supplements = ? WHERE id = ?",
            (*fields, known.id),
        )
        (
            known.sort_name,
            known.kind,
            known.source,
            known.slug,
            known.frequency,
            known.language,
            known.issue_key,
            known.parent_slug,
            known.supplements,
        ) = fields

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


def _duplicate_key(
    title_id_: str,
    issue_date: object,
    issue_number: object,
    variant: object,
    issue_key: object,
) -> tuple[str, str, str, str]:
    """The group ``_mark_duplicates`` resolves one issue into, per its title.

    ``date`` and ``number`` narrow the default key to one component, but only
    when that component is actually there: an issue with no date, in a title
    declared ``issue_key: date``, still has to land in *some* group, so a
    missing key component falls back to the full ``date+number`` key rather
    than grouping every dateless issue of the title together.
    """
    if issue_key == "date" and issue_date is not None:
        return (title_id_, str(issue_date), "None", str(variant))
    if issue_key == "number" and issue_number is not None:
        return (title_id_, "None", str(issue_number), str(variant))
    return (title_id_, str(issue_date), str(issue_number), str(variant))


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
