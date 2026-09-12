"""The catalogue database: schema, migrations, identity and connections."""

from __future__ import annotations

import logging
import sqlite3
import threading
from pathlib import Path

import pytest

from paperstand.config import Settings
from paperstand.db import (
    SCHEMA_V1,
    SCHEMA_V2,
    SCHEMA_V3,
    SCHEMA_VERSION,
    Database,
    DatabaseError,
    get_meta,
    issue_id,
    library_id,
    migrate,
    open_database,
    read_content_hashes,
    set_meta,
    title_id,
    user_version,
    utc_now,
)
from paperstand.parsing.normalize import slugify

TABLES = {
    "libraries",
    "titles",
    "issues",
    "reading_progress",
    "scans",
    "meta",
}


def table_names(connection: sqlite3.Connection) -> set[str]:
    return {
        str(row["name"])
        for row in connection.execute("SELECT name FROM sqlite_master WHERE type = 'table'")
    }


def test_opening_creates_the_schema_and_records_its_version(tmp_path: Path) -> None:
    database = open_database(tmp_path / "nested" / "paperstand.db")
    connection = database.connection

    assert table_names(connection) >= TABLES
    assert user_version(connection) == SCHEMA_VERSION
    assert get_meta(connection, "schema_version") == str(SCHEMA_VERSION)
    database.close()


def test_the_database_runs_in_wal_mode(tmp_path: Path) -> None:
    database = open_database(tmp_path / "paperstand.db")
    mode = database.connection.execute("PRAGMA journal_mode").fetchone()[0]
    assert str(mode).lower() == "wal"
    database.close()


def test_migrating_twice_changes_nothing(tmp_path: Path) -> None:
    database = open_database(tmp_path / "paperstand.db")
    before = table_names(database.connection)

    assert migrate(database.connection) == SCHEMA_VERSION
    assert table_names(database.connection) == before
    database.close()


def test_a_newer_schema_is_refused(tmp_path: Path) -> None:
    path = tmp_path / "paperstand.db"
    connection = sqlite3.connect(path)
    connection.execute(f"PRAGMA user_version = {SCHEMA_VERSION + 7}")
    connection.close()

    with pytest.raises(DatabaseError, match="newer Paperstand"):
        open_database(path)


def test_meta_round_trips(tmp_path: Path) -> None:
    database = open_database(tmp_path / "paperstand.db")
    connection = database.connection

    assert get_meta(connection, "config_hash") is None
    set_meta(connection, "config_hash", "abc")
    set_meta(connection, "config_hash", "def")

    assert get_meta(connection, "config_hash") == "def"
    database.close()


def test_reading_progress_survives_the_issue_disappearing(tmp_path: Path) -> None:
    """Progress is deliberately not cascaded: a file may come back."""
    database = open_database(tmp_path / "paperstand.db")
    connection = database.connection
    with connection:
        connection.execute(
            "INSERT INTO libraries VALUES ('l', 'L', 'L', 'magazine', 'config', ?)", (utc_now(),)
        )
        connection.execute(
            "INSERT INTO titles (id, library_id, name, sort_name, kind, source, created_at) "
            "VALUES ('t', 'l', 'T', 't', 'magazine', 'config', ?)",
            (utc_now(),),
        )
        connection.execute(
            "INSERT INTO issues (id, library_id, title_id, rel_path, filename, size, mtime_ns, "
            "date_precision, date_source, derived_title, label, matched_rule, added_at, "
            "updated_at) VALUES ('i', 'l', 't', 'L/a.pdf', 'a.pdf', 1, 1, 'day', 'filename', "
            "'T', '', 'D1', ?, ?)",
            (utc_now(), utc_now()),
        )
        connection.execute(
            "INSERT INTO reading_progress VALUES ('i', 12, 40, ?)",
            (utc_now(),),
        )
        connection.execute("DELETE FROM issues WHERE id = 'i'")

    row = connection.execute("SELECT page FROM reading_progress WHERE issue_id = 'i'").fetchone()
    assert row is not None and row["page"] == 12
    database.close()


def test_deleting_a_library_takes_its_titles_and_issues_with_it(tmp_path: Path) -> None:
    database = open_database(tmp_path / "paperstand.db")
    connection = database.connection
    with connection:
        connection.execute(
            "INSERT INTO libraries VALUES ('l', 'L', 'L', 'magazine', 'config', ?)", (utc_now(),)
        )
        connection.execute(
            "INSERT INTO titles (id, library_id, name, sort_name, kind, source, created_at) "
            "VALUES ('t', 'l', 'T', 't', 'magazine', 'config', ?)",
            (utc_now(),),
        )
        connection.execute("DELETE FROM libraries WHERE id = 'l'")

    assert connection.execute("SELECT count(*) AS n FROM titles").fetchone()["n"] == 0
    database.close()


def test_every_thread_gets_its_own_connection(tmp_path: Path) -> None:
    database = open_database(tmp_path / "paperstand.db")
    seen: list[int] = []

    def use() -> None:
        seen.append(id(database.connection))
        database.connection.execute("SELECT 1").fetchone()

    threads = [threading.Thread(target=use) for _ in range(4)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert len(set(seen)) == 4
    database.close()


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("Corriere del Ponte", "corriere-del-ponte"),
        ("L'Almanacco", "l-almanacco"),
        ("Cronaca 24 Pagine", "cronaca-24-pagine"),
        ("Città", "citta"),
        ("!!!", "item"),
    ],
)
def test_slugify(text: str, expected: str) -> None:
    assert slugify(text) == expected


def test_title_ids_are_unique_across_libraries() -> None:
    assert library_id("Newspapers") == "newspapers"
    assert title_id("newspapers", "Circuito") != title_id("magazines", "Circuito")
    assert title_id("newspapers", "Circuito") == "newspapers-circuito"


def test_issue_ids_are_stable_and_short() -> None:
    """An id is the first sixteen characters of a content digest, verbatim."""
    digest = "b94d27b9934d3e08a52e52d7da7dabfac484efe37a5380ee9088f7ace2efcde9"
    identifier = issue_id(digest)

    assert identifier == digest[:16]
    assert identifier == issue_id(digest)
    assert len(identifier) == 16
    other = "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855"
    assert identifier != issue_id(other)


# -------------------------------------------------------------- schema 2 migration


def _populated_schema_1(path: Path) -> None:
    """A schema-1 database, built straight from ``SCHEMA_V1``, with one row
    in every table the migration touches or must leave alone."""
    connection = sqlite3.connect(path)
    try:
        connection.execute("PRAGMA foreign_keys = ON")
        connection.executescript(SCHEMA_V1)
        connection.execute("PRAGMA user_version = 1")
        with connection:
            connection.execute(
                "INSERT INTO libraries VALUES ('l', 'Newspapers', 'Newspapers', 'newspaper', "
                "'config', ?)",
                (utc_now(),),
            )
            connection.execute(
                "INSERT INTO titles VALUES ('t', 'l', 'Corriere del Ponte', "
                "'corriere del ponte', 'newspaper', 'config', ?)",
                (utc_now(),),
            )
            connection.execute(
                "INSERT INTO issues (id, library_id, title_id, rel_path, filename, size, "
                "mtime_ns, date_precision, date_source, derived_title, label, matched_rule, "
                "added_at, updated_at) VALUES ('i', 'l', 't', 'Newspapers/a.pdf', 'a.pdf', 1, 1, "
                "'day', 'filename', 'Corriere del Ponte', '', 'D1', ?, ?)",
                (utc_now(), utc_now()),
            )
            connection.execute("INSERT INTO reading_progress VALUES ('i', 12, 40, ?)", (utc_now(),))
    finally:
        connection.close()


def test_a_schema_1_database_migrates_to_schema_2_keeping_every_row(tmp_path: Path) -> None:
    path = tmp_path / "paperstand.db"
    _populated_schema_1(path)

    database = open_database(path)
    connection = database.connection

    assert user_version(connection) == SCHEMA_VERSION
    assert connection.execute("SELECT count(*) AS n FROM libraries").fetchone()["n"] == 1
    assert connection.execute("SELECT count(*) AS n FROM issues").fetchone()["n"] == 1
    assert connection.execute("SELECT count(*) AS n FROM reading_progress").fetchone()["n"] == 1

    title = connection.execute("SELECT * FROM titles WHERE id = 't'").fetchone()
    assert title["name"] == "Corriere del Ponte"
    assert title["source"] == "config"
    # The new columns exist and carry no value for a row the migration did not touch.
    assert title["slug"] is None
    assert title["frequency"] is None
    assert title["language"] is None
    assert title["issue_key"] is None
    assert title["parent_slug"] is None
    assert title["supplements"] is None

    issue = connection.execute("SELECT variant, volume FROM issues WHERE id = 'i'").fetchone()
    assert issue["variant"] is None
    assert issue["volume"] is None

    assert connection.execute("PRAGMA foreign_key_check").fetchall() == []
    database.close()


def test_schema_2_accepts_a_declared_title_and_its_new_columns(tmp_path: Path) -> None:
    path = tmp_path / "paperstand.db"
    _populated_schema_1(path)
    database = open_database(path)
    connection = database.connection

    with connection:
        connection.execute(
            "INSERT INTO titles (id, library_id, name, sort_name, kind, source, slug, "
            "frequency, language, issue_key, parent_slug, supplements, created_at) VALUES "
            "('t2', 'l', 'Bright Meadows', 'bright meadows', 'magazine', 'publication', "
            "'bright-meadows', 'monthly', 'en', 'date+number', NULL, '[\"Weekend\"]', ?)",
            (utc_now(),),
        )
        connection.execute("UPDATE issues SET variant = 'Weekend', volume = 2024 WHERE id = 'i'")

    declared = connection.execute("SELECT * FROM titles WHERE id = 't2'").fetchone()
    assert declared["source"] == "publication"
    assert declared["slug"] == "bright-meadows"
    assert declared["supplements"] == '["Weekend"]'
    issue = connection.execute("SELECT variant, volume FROM issues WHERE id = 'i'").fetchone()
    assert (issue["variant"], issue["volume"]) == ("Weekend", 2024)
    assert connection.execute("PRAGMA foreign_key_check").fetchall() == []
    database.close()


def test_schema_2_keeps_the_titles_library_index(tmp_path: Path) -> None:
    path = tmp_path / "paperstand.db"
    _populated_schema_1(path)
    database = open_database(path)

    index_names = {
        str(row["name"])
        for row in database.connection.execute(
            "SELECT name FROM sqlite_master WHERE type = 'index' AND tbl_name = 'titles'"
        )
    }

    assert "titles_library" in index_names
    database.close()


# -------------------------------------------------------------- schema 3 migration


def _populated_schema_2(path: Path) -> None:
    """A schema-2 database, with one row in every table the migration touches
    or must leave alone — the pattern of :func:`_populated_schema_1`, one
    version further along."""
    connection = sqlite3.connect(path)
    try:
        connection.execute("PRAGMA foreign_keys = ON")
        connection.executescript(SCHEMA_V1)
        connection.executescript(SCHEMA_V2)
        connection.execute("PRAGMA user_version = 2")
        with connection:
            connection.execute(
                "INSERT INTO libraries VALUES ('l', 'Newspapers', 'Newspapers', 'newspaper', "
                "'config', ?)",
                (utc_now(),),
            )
            connection.execute(
                "INSERT INTO titles (id, library_id, name, sort_name, kind, source, created_at) "
                "VALUES ('t', 'l', 'Corriere del Ponte', 'corriere del ponte', 'newspaper', "
                "'config', ?)",
                (utc_now(),),
            )
            connection.execute(
                "INSERT INTO issues (id, library_id, title_id, rel_path, filename, size, "
                "mtime_ns, date_precision, date_source, derived_title, label, matched_rule, "
                "added_at, updated_at) VALUES ('i', 'l', 't', 'Newspapers/a.pdf', 'a.pdf', 1, 1, "
                "'day', 'filename', 'Corriere del Ponte', '', 'D1', ?, ?)",
                (utc_now(), utc_now()),
            )
            connection.execute("INSERT INTO reading_progress VALUES ('i', 12, 40, ?)", (utc_now(),))
    finally:
        connection.close()


def test_a_schema_2_database_migrates_to_schema_3_keeping_every_row(tmp_path: Path) -> None:
    path = tmp_path / "paperstand.db"
    _populated_schema_2(path)

    database = open_database(path)
    connection = database.connection

    assert user_version(connection) == SCHEMA_VERSION
    assert connection.execute("SELECT count(*) AS n FROM libraries").fetchone()["n"] == 1
    assert connection.execute("SELECT count(*) AS n FROM titles").fetchone()["n"] == 1
    assert connection.execute("SELECT count(*) AS n FROM issues").fetchone()["n"] == 1
    assert connection.execute("SELECT count(*) AS n FROM reading_progress").fetchone()["n"] == 1

    # The new column exists and carries no value for a row the migration did
    # not touch: `content_hash IS NULL` is how the scanner recognises a
    # legacy row still waiting to be hashed.
    issue = connection.execute("SELECT content_hash FROM issues WHERE id = 'i'").fetchone()
    assert issue["content_hash"] is None

    assert connection.execute("PRAGMA foreign_key_check").fetchall() == []
    database.close()


def test_schema_3_keeps_the_issues_hash_index(tmp_path: Path) -> None:
    path = tmp_path / "paperstand.db"
    _populated_schema_2(path)
    database = open_database(path)

    index_names = {
        str(row["name"])
        for row in database.connection.execute(
            "SELECT name FROM sqlite_master WHERE type = 'index' AND tbl_name = 'issues'"
        )
    }

    assert "issues_hash" in index_names
    database.close()


def test_schema_3_accepts_a_content_hash(tmp_path: Path) -> None:
    path = tmp_path / "paperstand.db"
    _populated_schema_2(path)
    database = open_database(path)
    connection = database.connection

    digest = "b94d27b9934d3e08a52e52d7da7dabfac484efe37a5380ee9088f7ace2efcde9"
    with connection:
        connection.execute("UPDATE issues SET content_hash = ? WHERE id = 'i'", (digest,))

    row = connection.execute("SELECT content_hash FROM issues WHERE id = 'i'").fetchone()
    assert row["content_hash"] == digest
    assert connection.execute("PRAGMA foreign_key_check").fetchall() == []
    database.close()


# -------------------------------------------------------------- schema 4 migration


def _populated_schema_3(path: Path) -> None:
    """A schema-3 database, with one row in every table the migration touches
    or must leave alone — the pattern of :func:`_populated_schema_2`, one
    version further along."""
    connection = sqlite3.connect(path)
    try:
        connection.execute("PRAGMA foreign_keys = ON")
        connection.executescript(SCHEMA_V1)
        connection.executescript(SCHEMA_V2)
        connection.executescript(SCHEMA_V3)
        connection.execute("PRAGMA user_version = 3")
        with connection:
            connection.execute(
                "INSERT INTO libraries VALUES ('l', 'Newspapers', 'Newspapers', 'newspaper', "
                "'config', ?)",
                (utc_now(),),
            )
            connection.execute(
                "INSERT INTO titles (id, library_id, name, sort_name, kind, source, created_at) "
                "VALUES ('t', 'l', 'Corriere del Ponte', 'corriere del ponte', 'newspaper', "
                "'config', ?)",
                (utc_now(),),
            )
            connection.execute(
                "INSERT INTO issues (id, library_id, title_id, rel_path, filename, size, "
                "mtime_ns, date_precision, date_source, derived_title, label, matched_rule, "
                "added_at, updated_at) VALUES ('i', 'l', 't', 'Newspapers/a.pdf', 'a.pdf', 1, 1, "
                "'day', 'filename', 'Corriere del Ponte', '', 'D1', ?, ?)",
                (utc_now(), utc_now()),
            )
            connection.execute("INSERT INTO reading_progress VALUES ('i', 12, 40, ?)", (utc_now(),))
            connection.execute(
                "INSERT INTO scans (started_at, status) VALUES (?, 'ok')", (utc_now(),)
            )
    finally:
        connection.close()


def test_a_schema_3_database_migrates_to_schema_4_keeping_every_row(tmp_path: Path) -> None:
    path = tmp_path / "paperstand.db"
    _populated_schema_3(path)

    database = open_database(path)
    connection = database.connection

    assert user_version(connection) == SCHEMA_VERSION
    assert connection.execute("SELECT count(*) AS n FROM libraries").fetchone()["n"] == 1
    assert connection.execute("SELECT count(*) AS n FROM titles").fetchone()["n"] == 1
    assert connection.execute("SELECT count(*) AS n FROM issues").fetchone()["n"] == 1
    assert connection.execute("SELECT count(*) AS n FROM reading_progress").fetchone()["n"] == 1
    assert connection.execute("SELECT count(*) AS n FROM scans").fetchone()["n"] == 1

    # The new columns exist and carry no value, or their default, for a row
    # the migration did not touch.
    issue = connection.execute("SELECT missing_since FROM issues WHERE id = 'i'").fetchone()
    assert issue["missing_since"] is None
    scan = connection.execute("SELECT missing FROM scans").fetchone()
    assert scan["missing"] == 0

    assert connection.execute("PRAGMA foreign_key_check").fetchall() == []
    database.close()


def test_schema_4_keeps_the_issues_missing_index(tmp_path: Path) -> None:
    path = tmp_path / "paperstand.db"
    _populated_schema_3(path)
    database = open_database(path)

    index_names = {
        str(row["name"])
        for row in database.connection.execute(
            "SELECT name FROM sqlite_master WHERE type = 'index' AND tbl_name = 'issues'"
        )
    }

    assert "issues_missing" in index_names
    database.close()


def test_schema_4_accepts_a_missing_since_and_a_missing_count(tmp_path: Path) -> None:
    path = tmp_path / "paperstand.db"
    _populated_schema_3(path)
    database = open_database(path)
    connection = database.connection

    with connection:
        connection.execute("UPDATE issues SET missing_since = ? WHERE id = 'i'", (utc_now(),))
        connection.execute("UPDATE scans SET missing = 1")

    issue = connection.execute("SELECT missing_since FROM issues WHERE id = 'i'").fetchone()
    assert issue["missing_since"] is not None
    scan = connection.execute("SELECT missing FROM scans").fetchone()
    assert scan["missing"] == 1
    assert connection.execute("PRAGMA foreign_key_check").fetchall() == []
    database.close()


def test_closing_twice_is_harmless(tmp_path: Path) -> None:
    database = Database(tmp_path / "paperstand.db")
    database.connection.execute("SELECT 1")
    database.close()
    database.close()


# -------------------------------------------------------------- read_content_hashes


def test_read_content_hashes_on_an_absent_file_warns_and_returns_empty(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    with caplog.at_level(logging.WARNING, logger="paperstand"):
        assert read_content_hashes(tmp_path / "absent.db") == {}
    warnings = [record for record in caplog.records if record.levelno >= logging.WARNING]
    assert len(warnings) == 1


def test_read_content_hashes_on_a_schema_2_database_warns_and_returns_empty(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    path = tmp_path / "paperstand.db"
    _populated_schema_2(path)

    with caplog.at_level(logging.WARNING, logger="paperstand"):
        assert read_content_hashes(path) == {}
    warnings = [record for record in caplog.records if record.levelno >= logging.WARNING]
    assert len(warnings) == 1


def test_read_content_hashes_on_a_populated_schema_3_database(
    catalogue_settings: Settings,
) -> None:
    database = open_database(catalogue_settings.db_path)
    expected = {
        str(row["content_hash"]): str(row["rel_path"])
        for row in database.connection.execute(
            "SELECT content_hash, rel_path FROM issues WHERE content_hash IS NOT NULL"
        )
    }
    database.close()
    assert expected, "the scanned sample library produced no hashed row to check"

    assert read_content_hashes(catalogue_settings.db_path) == expected
