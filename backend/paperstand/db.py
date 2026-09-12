"""The catalogue database.

SQLite through the standard library, in WAL mode so that a scan writing in the
background never blocks a request reading. Every thread gets a connection of its
own — a :class:`sqlite3.Connection` may not be shared across threads — and they
are all closed together when the application shuts down.

The schema is versioned with ``PRAGMA user_version``: :data:`MIGRATIONS` is an
append-only list of statements, and :func:`migrate` runs the ones the file has
not seen yet. There is no downgrade path; a database from a newer Paperstand is
refused rather than silently mangled.

Identity, which the scanner relies on: a library's id is the slug of its name,
a title's id the slug of ``<library id>/<title name>`` and an issue's id the
first sixteen hexadecimal characters of the SHA-256 of its own bytes — an
issue *is* its content, not its path. Renaming or moving a file keeps its id;
a file replaced with different bytes is a new issue. Ids are stable across
scans, which is what lets a cached cover survive one. Two *libraries* whose
names slugify the same way are refused when the configuration is loaded; two
*titles* that do, or two issues whose content hashes share their first sixteen
characters, are given ``-2``, ``-3`` suffixes, because neither a title name
nor a hash prefix is something a user chose for Paperstand.
"""

from __future__ import annotations

import datetime as dt
import sqlite3
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from paperstand.logging import get_logger
from paperstand.parsing.normalize import slugify

log = get_logger(__name__)

#: Version of the schema this build of Paperstand writes.
SCHEMA_VERSION = 4

#: How long a writer waits for a lock before giving up, in milliseconds.
BUSY_TIMEOUT_MS = 10_000

SCHEMA_V1 = """
CREATE TABLE libraries (
    id           TEXT PRIMARY KEY,
    name         TEXT NOT NULL,
    path         TEXT NOT NULL,
    kind         TEXT NOT NULL CHECK (kind IN ('newspaper', 'magazine')),
    title_source TEXT NOT NULL,
    created_at   TEXT NOT NULL
);

CREATE TABLE titles (
    id         TEXT PRIMARY KEY,
    library_id TEXT NOT NULL REFERENCES libraries(id) ON DELETE CASCADE,
    name       TEXT NOT NULL,
    sort_name  TEXT NOT NULL,
    kind       TEXT NOT NULL CHECK (kind IN ('newspaper', 'magazine')),
    source     TEXT NOT NULL
        CHECK (source IN ('config', 'folder', 'filename', 'pattern', 'unsorted')),
    created_at TEXT NOT NULL,
    UNIQUE (library_id, name)
);

CREATE INDEX titles_library ON titles (library_id, sort_name);

CREATE TABLE issues (
    id               TEXT PRIMARY KEY,
    library_id       TEXT NOT NULL REFERENCES libraries(id) ON DELETE CASCADE,
    title_id         TEXT NOT NULL REFERENCES titles(id) ON DELETE CASCADE,
    rel_path         TEXT NOT NULL UNIQUE,
    filename         TEXT NOT NULL,
    size             INTEGER NOT NULL,
    mtime_ns         INTEGER NOT NULL,
    issue_date       TEXT,
    date_precision   TEXT NOT NULL
        CHECK (date_precision IN ('day', 'month', 'year', 'none')),
    date_source      TEXT NOT NULL
        CHECK (date_source IN ('filename', 'mixed', 'folder', 'mtime', 'none')),
    issue_number     TEXT,
    derived_title    TEXT NOT NULL,
    label            TEXT NOT NULL,
    matched_rule     TEXT NOT NULL,
    has_dedup_suffix INTEGER NOT NULL DEFAULT 0,
    duplicate_of     TEXT REFERENCES issues(id) ON DELETE SET NULL,
    page_count       INTEGER,
    page_w           REAL,
    page_h           REAL,
    cover_status     TEXT NOT NULL DEFAULT 'pending'
        CHECK (cover_status IN ('pending', 'ok', 'error')),
    cover_error      TEXT,
    first_page_text  TEXT,
    added_at         TEXT NOT NULL,
    updated_at       TEXT NOT NULL,
    last_seen_scan   INTEGER NOT NULL DEFAULT 0
);

CREATE INDEX issues_title_date ON issues (title_id, issue_date DESC);
CREATE INDEX issues_library ON issues (library_id);
CREATE INDEX issues_date ON issues (issue_date);
CREATE INDEX issues_added ON issues (added_at DESC);

-- Deliberately not a foreign key: where a reader got to survives the file
-- disappearing from the library and coming back later.
CREATE TABLE reading_progress (
    issue_id   TEXT PRIMARY KEY,
    page       INTEGER NOT NULL,
    page_count INTEGER,
    updated_at TEXT NOT NULL
);

CREATE TABLE scans (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    started_at  TEXT NOT NULL,
    finished_at TEXT,
    status      TEXT NOT NULL CHECK (status IN ('running', 'ok', 'error')),
    files_seen  INTEGER NOT NULL DEFAULT 0,
    added       INTEGER NOT NULL DEFAULT 0,
    updated     INTEGER NOT NULL DEFAULT 0,
    removed     INTEGER NOT NULL DEFAULT 0,
    covers_done INTEGER NOT NULL DEFAULT 0,
    errors      INTEGER NOT NULL DEFAULT 0,
    message     TEXT
);

CREATE TABLE meta (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
"""

# `titles` is rebuilt rather than given a side column: `source` is the one
# classification the API exposes, the storefront filters on and the frontend
# branches on, and a declaration is where the *name* came from, in the same
# sense as `config` or `folder` — a second column would let the two disagree.
# SQLite cannot `ALTER` a `CHECK` constraint, so the table is copied instead.
#
# `_prepare` turns `PRAGMA foreign_keys` on for every connection, and
# `DROP TABLE titles` would then cascade into `issues` (`ON DELETE CASCADE`):
# the pragma is turned off for the duration of the script, which is only safe
# outside a transaction, hence the `PRAGMA` calls sitting either side of the
# script's own `BEGIN` / `COMMIT` rather than relying on `migrate`'s.
SCHEMA_V2 = """
PRAGMA foreign_keys = OFF;
BEGIN;
CREATE TABLE titles_v2 (
    id          TEXT PRIMARY KEY,
    library_id  TEXT NOT NULL REFERENCES libraries(id) ON DELETE CASCADE,
    name        TEXT NOT NULL,
    sort_name   TEXT NOT NULL,
    kind        TEXT NOT NULL CHECK (kind IN ('newspaper', 'magazine')),
    source      TEXT NOT NULL
        CHECK (source IN ('config', 'folder', 'filename', 'pattern', 'unsorted', 'publication')),
    slug        TEXT,
    frequency   TEXT CHECK (frequency IN ('daily', 'weekly', 'monthly', 'irregular')),
    language    TEXT,
    issue_key   TEXT CHECK (issue_key IN ('date', 'number', 'date+number')),
    parent_slug TEXT,
    supplements TEXT,   -- JSON array; NULL when the title is not declared
    created_at  TEXT NOT NULL,
    UNIQUE (library_id, name)
);
INSERT INTO titles_v2 (id, library_id, name, sort_name, kind, source, created_at)
    SELECT id, library_id, name, sort_name, kind, source, created_at FROM titles;
DROP TABLE titles;
ALTER TABLE titles_v2 RENAME TO titles;
CREATE INDEX titles_library ON titles (library_id, sort_name);
ALTER TABLE issues ADD COLUMN variant TEXT;
ALTER TABLE issues ADD COLUMN volume INTEGER;
COMMIT;
PRAGMA foreign_keys = ON;
"""

# `content_hash` is nullable: a row written before this migration has none
# until the scanner backfills it, and `content_hash IS NULL` is how the fast
# phase recognises a legacy row worth hashing. The index is what lets it find
# a byte-identical copy — or the row a moved file used to be — by hash alone.
SCHEMA_V3 = """
BEGIN;
ALTER TABLE issues ADD COLUMN content_hash TEXT;
CREATE INDEX issues_hash ON issues (content_hash);
COMMIT;
"""

# `missing_since` is the `started_at` of the scan that first failed to find
# the file behind a row; `NULL` means present. It is what lets a scan hide a
# vanished issue for a grace period instead of deleting it on the spot, the
# way `content_hash` let a moved file keep its identity. `scans.missing`
# counts how many rows are missing — new or still within grace — at the end
# of a scan, a number `removed` alone cannot report.
SCHEMA_V4 = """
BEGIN;
ALTER TABLE issues ADD COLUMN missing_since TEXT;
CREATE INDEX issues_missing ON issues (missing_since);
ALTER TABLE scans ADD COLUMN missing INTEGER NOT NULL DEFAULT 0;
COMMIT;
"""

#: One entry per schema version, in order. Append; never edit a released one.
MIGRATIONS: tuple[str, ...] = (SCHEMA_V1, SCHEMA_V2, SCHEMA_V3, SCHEMA_V4)


class DatabaseError(RuntimeError):
    """The database could not be opened or migrated."""


def utc_now() -> str:
    """The current instant, as the ISO 8601 string the tables store."""
    return dt.datetime.now(dt.UTC).replace(microsecond=0).isoformat()


def library_id(name: str) -> str:
    """Identifier of a library, derived from its configured name."""
    return slugify(name)


def title_id(library: str, name: str) -> str:
    """Identifier of a title, unique across libraries."""
    return slugify(f"{library}/{name}")


def issue_id(content_hash: str) -> str:
    """Identifier of an issue: the truncated SHA-256 of its own bytes.

    ``content_hash`` is the full 64-character digest
    :func:`paperstand.scanner.hashing.content_hash` computes; only its first
    sixteen characters become the id — the whole digest is stored separately,
    for a future lookup by hash.
    """
    return content_hash[:16]


def user_version(connection: sqlite3.Connection) -> int:
    """The schema version recorded in the file."""
    row = connection.execute("PRAGMA user_version").fetchone()
    return int(row[0]) if row is not None else 0


def migrate(connection: sqlite3.Connection) -> int:
    """Bring a connection's database up to :data:`SCHEMA_VERSION`."""
    version = user_version(connection)
    if version > SCHEMA_VERSION:
        raise DatabaseError(
            f"the database was written by a newer Paperstand "
            f"(schema {version}, this build understands {SCHEMA_VERSION})"
        )
    for target in range(version + 1, SCHEMA_VERSION + 1):
        log.info("migrating the database to schema %d", target)
        with connection:
            connection.executescript(MIGRATIONS[target - 1])
            connection.execute(f"PRAGMA user_version = {target}")
    if version != SCHEMA_VERSION:
        with connection:
            connection.execute(
                "INSERT INTO meta (key, value) VALUES ('schema_version', ?) "
                "ON CONFLICT (key) DO UPDATE SET value = excluded.value",
                (str(SCHEMA_VERSION),),
            )
    return SCHEMA_VERSION


def _prepare(connection: sqlite3.Connection) -> None:
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA journal_mode = WAL")
    connection.execute("PRAGMA synchronous = NORMAL")
    connection.execute("PRAGMA foreign_keys = ON")
    connection.execute(f"PRAGMA busy_timeout = {BUSY_TIMEOUT_MS}")


class Database:
    """A SQLite file, with one connection per thread.

    The instance is shared; the connections are not. Threads that come and go —
    the scheduler, the scan threads, the request handlers — each get their own on
    first use.

    A connection may only be closed by the thread that opened it, so a
    short-lived thread has to hand its own back with :meth:`close_thread` before
    it ends. :meth:`close` then closes what is left — the connections of threads
    still alive — and forgets the rest instead of leaking file descriptors for
    the life of the process.
    """

    def __init__(self, path: Path) -> None:
        self.path = path
        self._local = threading.local()
        self._connections: list[sqlite3.Connection] = []
        self._lock = threading.Lock()

    @property
    def connection(self) -> sqlite3.Connection:
        """This thread's connection, opened on first use."""
        existing: sqlite3.Connection | None = getattr(self._local, "connection", None)
        if existing is not None:
            return existing
        connection = sqlite3.connect(self.path, timeout=BUSY_TIMEOUT_MS / 1000)
        _prepare(connection)
        self._local.connection = connection
        with self._lock:
            self._connections.append(connection)
        return connection

    @property
    def open_connections(self) -> int:
        """How many connections this database is currently holding."""
        with self._lock:
            return len(self._connections)

    def close_thread(self) -> None:
        """Close and forget the calling thread's connection, if it has one.

        Called at the end of every thread that is not going to outlive the
        process: without it the registry — and the open file descriptors — grow
        by one per scan.
        """
        connection: sqlite3.Connection | None = getattr(self._local, "connection", None)
        if connection is None:
            return
        self._local.connection = None
        with self._lock:
            if connection in self._connections:
                self._connections.remove(connection)
        try:
            connection.close()
        except sqlite3.Error as error:  # pragma: no cover - closing twice is harmless
            log.debug("closing this thread's connection failed: %s", error)

    @contextmanager
    def transaction(self) -> Iterator[sqlite3.Connection]:
        """Run a block inside a transaction on this thread's connection."""
        connection = self.connection
        with connection:
            yield connection

    def close(self) -> None:
        """Close every connection this database has handed out."""
        with self._lock:
            connections, self._connections = self._connections, []
        for connection in connections:
            try:
                connection.close()
            except sqlite3.ProgrammingError:
                # Opened by a thread that has since ended without handing it
                # back: SQLite refuses to close it from here. Nothing else can
                # reach it either, so letting it go is the best available end.
                log.debug("a connection belonging to a finished thread was left to the GC")
            except sqlite3.Error:  # pragma: no cover - closing twice is harmless
                log.debug("a database connection was already closed")
        self._local = threading.local()


def open_database(path: Path) -> Database:
    """Open — and migrate — the database at ``path``, creating its directory."""
    path.parent.mkdir(parents=True, exist_ok=True)
    database = Database(path)
    migrate(database.connection)
    return database


def get_meta(connection: sqlite3.Connection, key: str) -> str | None:
    """Read one ``meta`` value."""
    row = connection.execute("SELECT value FROM meta WHERE key = ?", (key,)).fetchone()
    return str(row["value"]) if row is not None else None


def read_content_hashes(path: Path) -> dict[str, str]:
    """Every catalogued content hash, mapped to the relative path that carries it.

    Opened read-only, through ``file:...?mode=ro`` so a catalogue that does not
    exist raises rather than being created; the schema is checked with
    ``PRAGMA user_version`` before the query ever touches ``content_hash`` — a
    column a schema-2 catalogue does not have. An absent database, a catalogue
    written by an older Paperstand, or any other :class:`sqlite3.Error` are all
    the same case to a caller organizing an inbox: an empty mapping, one
    ``log.warning``, and the run continues — a byte-identical file is then only
    caught by the destination check at move time. The connection is closed
    before this function returns, well before anything moves.

    Two rows sharing a hash — a duplicate the scanner has not caught up with
    yet — resolve to the row with the earliest ``rel_path``, so the mapping is
    deterministic however the table's own row order happens to fall.
    """
    try:
        connection = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    except sqlite3.Error as error:
        log.warning("cannot read the catalogue at %s: %s", path, error)
        return {}
    try:
        version = user_version(connection)
        if version < 3:
            log.warning(
                "the catalogue at %s is schema %d, too old to carry content hashes",
                path,
                version,
            )
            return {}
        rows = connection.execute(
            "SELECT content_hash, rel_path FROM issues "
            "WHERE content_hash IS NOT NULL ORDER BY rel_path"
        ).fetchall()
    except sqlite3.Error as error:
        log.warning("cannot read the catalogue at %s: %s", path, error)
        return {}
    finally:
        connection.close()
    hashes: dict[str, str] = {}
    for row_hash, rel_path in rows:
        hashes.setdefault(str(row_hash), str(rel_path))
    return hashes


def set_meta(connection: sqlite3.Connection, key: str, value: str) -> None:
    """Write one ``meta`` value."""
    connection.execute(
        "INSERT INTO meta (key, value) VALUES (?, ?) "
        "ON CONFLICT (key) DO UPDATE SET value = excluded.value",
        (key, value),
    )
