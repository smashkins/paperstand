"""The catalogue database: schema, migrations, identity and connections."""

from __future__ import annotations

import sqlite3
import threading
from pathlib import Path

import pytest

from paperstand.db import (
    SCHEMA_VERSION,
    Database,
    DatabaseError,
    get_meta,
    issue_id,
    library_id,
    migrate,
    open_database,
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
            "INSERT INTO titles VALUES ('t', 'l', 'T', 't', 'magazine', 'config', ?)", (utc_now(),)
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
            "INSERT INTO titles VALUES ('t', 'l', 'T', 't', 'magazine', 'config', ?)", (utc_now(),)
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
    identifier = issue_id("Newspapers/2026/03/17/x.pdf")
    assert identifier == issue_id("Newspapers/2026/03/17/x.pdf")
    assert len(identifier) == 16
    assert identifier != issue_id("Newspapers/2026/03/18/x.pdf")


def test_closing_twice_is_harmless(tmp_path: Path) -> None:
    database = Database(tmp_path / "paperstand.db")
    database.connection.execute("SELECT 1")
    database.close()
    database.close()
