"""Accounts without HTTP: passwords, tokens, the administrator rules, the bootstrap."""

from __future__ import annotations

import base64
import hashlib
import logging
import sqlite3
import threading
import time
from collections.abc import Iterator
from pathlib import Path

import pytest
from pydantic import SecretStr

from paperstand import accounts
from paperstand.accounts import (
    LastAdmin,
    SelfDelete,
    UnknownUser,
    UsernameTaken,
    authenticate,
    bootstrap,
    create_user,
    delete_user,
    find_user,
    get_user,
    hash_password,
    issue_token,
    list_users,
    parse_hash,
    resolve_token,
    revoke_token,
    set_admin,
    set_password,
    validate_password,
    validate_username,
    verify_password,
)
from paperstand.db import Database, open_database
from tests.conftest import PRODUCTION_SCRYPT_N, quiet_settings

PASSWORD = "correct horse battery"


@pytest.fixture
def database(tmp_path: Path) -> Iterator[Database]:
    opened = open_database(tmp_path / "paperstand.db")
    yield opened
    opened.close()


@pytest.fixture
def connection(database: Database) -> sqlite3.Connection:
    return database.connection


def last_used(connection: sqlite3.Connection, token_id: int) -> str:
    row = connection.execute(
        "SELECT last_used_at FROM auth_tokens WHERE id = ?", (token_id,)
    ).fetchone()
    return str(row["last_used_at"])


def stamp(connection: sqlite3.Connection, token_id: int, value: str) -> None:
    with connection:
        connection.execute(
            "UPDATE auth_tokens SET last_used_at = ? WHERE id = ?", (value, token_id)
        )


# -------------------------------------------------------------------- passwords


def test_the_production_cost_is_two_to_the_fourteenth() -> None:
    assert PRODUCTION_SCRYPT_N == 2**14
    assert accounts.SCRYPT_R == 8
    assert accounts.SCRYPT_P == 1


def test_a_password_round_trips_through_its_hash() -> None:
    stored = hash_password(PASSWORD)

    assert verify_password(PASSWORD, stored)
    assert not verify_password(PASSWORD + "!", stored)
    assert not verify_password("", stored)


def test_a_hash_carries_its_parameters_and_a_fresh_salt() -> None:
    stored = hash_password(PASSWORD)
    scheme, n, r, p, salt, key = stored.split("$")

    assert (scheme, int(n), int(r), int(p)) == ("scrypt", accounts.SCRYPT_N, 8, 1)
    assert len(base64.b64decode(salt)) == 16
    assert len(base64.b64decode(key)) == 32
    assert hash_password(PASSWORD) != stored


def test_a_hash_made_at_another_cost_still_verifies(monkeypatch: pytest.MonkeyPatch) -> None:
    """Raising the cost later must not lock out a single existing password."""
    monkeypatch.setattr(accounts, "SCRYPT_N", 2**5)
    older = hash_password(PASSWORD)
    monkeypatch.setattr(accounts, "SCRYPT_N", 2**6)

    parsed = parse_hash(older)
    assert parsed is not None and parsed.n == 2**5
    assert verify_password(PASSWORD, older)


def test_a_password_with_non_ascii_text_round_trips() -> None:
    stored = hash_password("città sul lago — 2026")
    assert verify_password("città sul lago — 2026", stored)
    assert not verify_password("citta sul lago — 2026", stored)


@pytest.mark.parametrize(
    "stored",
    [
        "",
        "plain text",
        "bcrypt$16$8$1$c2FsdA==$a2V5",
        "scrypt$16$8$1$c2FsdA==",
        "scrypt$16$8$1$c2FsdA==$a2V5$extra",
        "scrypt$15$8$1$c2FsdA==$a2V5",  # not a power of two
        "scrypt$1$8$1$c2FsdA==$a2V5",  # too small
        "scrypt$0$8$1$c2FsdA==$a2V5",
        "scrypt$16$0$1$c2FsdA==$a2V5",
        "scrypt$16$8$0$c2FsdA==$a2V5",
        "scrypt$16$8$17$c2FsdA==$a2V5",
        "scrypt$1073741824$8$1$c2FsdA==$a2V5",  # far more memory than allowed
        "scrypt$16$8$1$not base64$a2V5",
        "scrypt$16$8$1$c2FsdA=$a2V5",  # bad padding
        "scrypt$1_6$8$1$c2FsdA==$a2V5",
        "scrypt$+16$8$1$c2FsdA==$a2V5",
    ],
)
def test_a_malformed_hash_is_a_failed_check_not_an_error(stored: str) -> None:
    assert parse_hash(stored) is None
    assert verify_password(PASSWORD, stored) is False


# ------------------------------------------------------------------- validation


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("reader", "reader"),
        ("  reader  ", "reader"),
        ("Second Reader", "Second Reader"),
        ("lettrice-di-Valdora", "lettrice-di-Valdora"),
        ("ü" * 64, "ü" * 64),
    ],
)
def test_an_acceptable_username_comes_back_trimmed(raw: str, expected: str) -> None:
    assert validate_username(raw) == expected


@pytest.mark.parametrize(
    "raw",
    ["", "   ", "a" * 65, "rea:der", "rea\nder", "rea\x00der", "rea\u200bder", "rea\u2028der"],
)
def test_an_unacceptable_username_is_refused(raw: str) -> None:
    with pytest.raises(ValueError):
        validate_username(raw)


def test_a_password_is_between_eight_and_1024_characters_and_never_trimmed() -> None:
    assert validate_password(" " * 8) == " " * 8
    assert validate_password("a" * 1024) == "a" * 1024
    for refused in ("a" * 7, "a" * 1025, "abcdefg\ud800"):
        with pytest.raises(ValueError):
            validate_password(refused)


# ------------------------------------------------------------------------ users


def test_a_user_is_created_found_and_listed(connection: sqlite3.Connection) -> None:
    created = create_user(connection, "  Reader ", PASSWORD)
    admin = create_user(connection, "admin", PASSWORD, is_admin=True)
    create_user(connection, "second", PASSWORD)

    assert created.username == "Reader"
    assert created.is_admin is False
    assert admin.is_admin is True
    assert get_user(connection, created.id) == created
    assert find_user(connection, "READER") == created
    assert find_user(connection, " reader ") == created
    assert find_user(connection, "nobody") is None
    assert [account.username for account in list_users(connection)] == [
        "admin",
        "Reader",
        "second",
    ]


def test_a_password_hash_never_reaches_a_repr(connection: sqlite3.Connection) -> None:
    account = create_user(connection, "reader", PASSWORD)
    assert account.password_hash not in repr(account)


def test_a_name_is_taken_whatever_its_case(connection: sqlite3.Connection) -> None:
    create_user(connection, "reader", PASSWORD)

    with pytest.raises(UsernameTaken):
        create_user(connection, "READER", PASSWORD)
    assert len(list_users(connection)) == 1


def test_creating_validates_both_fields(connection: sqlite3.Connection) -> None:
    with pytest.raises(ValueError):
        create_user(connection, "rea:der", PASSWORD)
    with pytest.raises(ValueError):
        create_user(connection, "reader", "short")
    assert list_users(connection) == []


def test_authenticate_accepts_the_right_password_only(connection: sqlite3.Connection) -> None:
    account = create_user(connection, "reader", PASSWORD)

    assert authenticate(connection, "reader", PASSWORD) == account
    assert authenticate(connection, "Reader", PASSWORD) == account
    assert authenticate(connection, "reader", "wrong password") is None


def test_an_unknown_name_still_costs_a_hash_verification(
    connection: sqlite3.Connection, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The time a refusal takes must not say which names exist."""
    checked: list[str] = []
    real = accounts.verify_password

    def counting(password: str, stored: str) -> bool:
        checked.append(stored)
        return real(password, stored)

    monkeypatch.setattr(accounts, "verify_password", counting)

    assert authenticate(connection, "nobody", PASSWORD) is None
    assert len(checked) == 1
    assert parse_hash(checked[0]) is not None


def test_a_new_password_replaces_the_old_and_revokes_the_other_tokens(
    connection: sqlite3.Connection,
) -> None:
    account = create_user(connection, "reader", PASSWORD)
    first, first_id = issue_token(connection, account.id, "tablet")
    second, _ = issue_token(connection, account.id, "phone")
    other = create_user(connection, "second", PASSWORD)
    unrelated, _ = issue_token(connection, other.id)

    assert set_password(connection, account.id, "a new password", keep_token_id=first_id) == 1

    assert authenticate(connection, "reader", PASSWORD) is None
    assert authenticate(connection, "reader", "a new password") is not None
    assert resolve_token(connection, first) is not None
    assert resolve_token(connection, second) is None
    assert resolve_token(connection, unrelated) is not None


def test_a_reset_without_a_token_to_keep_revokes_them_all(connection: sqlite3.Connection) -> None:
    account = create_user(connection, "reader", PASSWORD)
    raw, _ = issue_token(connection, account.id)

    assert set_password(connection, account.id, "a new password") == 1
    assert resolve_token(connection, raw) is None


def test_a_reset_is_refused_for_nobody_and_for_a_bad_password(
    connection: sqlite3.Connection,
) -> None:
    account = create_user(connection, "reader", PASSWORD)

    with pytest.raises(UnknownUser):
        set_password(connection, account.id + 100, "a new password")
    with pytest.raises(ValueError):
        set_password(connection, account.id, "short")
    assert authenticate(connection, "reader", PASSWORD) is not None


# ------------------------------------------------------------ administrator rules


def test_the_last_admin_cannot_be_demoted(connection: sqlite3.Connection) -> None:
    admin = create_user(connection, "admin", PASSWORD, is_admin=True)

    with pytest.raises(LastAdmin):
        set_admin(connection, admin.id, False)
    assert get_user(connection, admin.id) == admin


def test_an_admin_with_a_peer_can_be_demoted_and_promoted(connection: sqlite3.Connection) -> None:
    admin = create_user(connection, "admin", PASSWORD, is_admin=True)
    reader = create_user(connection, "reader", PASSWORD)

    assert set_admin(connection, reader.id, True).is_admin is True
    assert set_admin(connection, admin.id, False).is_admin is False
    with pytest.raises(LastAdmin):
        set_admin(connection, reader.id, False)
    # Demoting somebody who is not an administrator is no change, not an error.
    assert set_admin(connection, admin.id, False).is_admin is False


def test_promoting_or_demoting_nobody_is_unknown(connection: sqlite3.Connection) -> None:
    with pytest.raises(UnknownUser):
        set_admin(connection, 42, True)
    with pytest.raises(UnknownUser):
        set_admin(connection, 42, False)


def test_nobody_deletes_their_own_account(connection: sqlite3.Connection) -> None:
    admin = create_user(connection, "admin", PASSWORD, is_admin=True)
    create_user(connection, "second", PASSWORD, is_admin=True)

    with pytest.raises(SelfDelete):
        delete_user(connection, admin.id, acting_user_id=admin.id)
    assert get_user(connection, admin.id) is not None


def test_the_last_admin_cannot_be_deleted(connection: sqlite3.Connection) -> None:
    admin = create_user(connection, "admin", PASSWORD, is_admin=True)
    reader = create_user(connection, "reader", PASSWORD)

    with pytest.raises(LastAdmin):
        delete_user(connection, admin.id, acting_user_id=reader.id)
    with pytest.raises(LastAdmin):
        delete_user(connection, admin.id, acting_user_id=None)
    with pytest.raises(UnknownUser):
        delete_user(connection, 42, acting_user_id=admin.id)
    assert get_user(connection, admin.id) is not None


def test_deleting_an_account_takes_its_tokens_with_it(connection: sqlite3.Connection) -> None:
    admin = create_user(connection, "admin", PASSWORD, is_admin=True)
    reader = create_user(connection, "reader", PASSWORD)
    raw, _ = issue_token(connection, reader.id)

    delete_user(connection, reader.id, acting_user_id=admin.id)

    assert get_user(connection, reader.id) is None
    assert resolve_token(connection, raw) is None
    assert connection.execute("SELECT count(*) FROM auth_tokens").fetchone()[0] == 0


def test_two_admins_demoting_each_other_at_once_leave_one(database: Database) -> None:
    """The guard is evaluated under the write lock, so only one of them wins."""
    first = create_user(database.connection, "admin", PASSWORD, is_admin=True)
    second = create_user(database.connection, "second", PASSWORD, is_admin=True)
    barrier = threading.Barrier(2)
    outcomes: list[str] = []
    lock = threading.Lock()

    def demote(user_id: int) -> None:
        connection = database.connection
        barrier.wait()
        try:
            set_admin(connection, user_id, False)
            outcome = "demoted"
        except LastAdmin:
            outcome = "refused"
        finally:
            database.close_thread()
        with lock:
            outcomes.append(outcome)

    threads = [threading.Thread(target=demote, args=(user.id,)) for user in (first, second)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert sorted(outcomes) == ["demoted", "refused"]
    admins = [account for account in list_users(database.connection) if account.is_admin]
    assert len(admins) == 1


# ----------------------------------------------------------------------- tokens


def test_a_token_resolves_to_its_account_until_revoked(connection: sqlite3.Connection) -> None:
    account = create_user(connection, "reader", PASSWORD)
    raw, token_id = issue_token(connection, account.id, "tablet")

    resolved = resolve_token(connection, raw)
    assert resolved is not None
    assert resolved.token_id == token_id
    assert resolved.account == account

    assert revoke_token(connection, token_id) is True
    assert resolve_token(connection, raw) is None
    assert revoke_token(connection, token_id) is False


def test_only_the_hash_of_a_token_is_stored(connection: sqlite3.Connection) -> None:
    account = create_user(connection, "reader", PASSWORD)
    raw, token_id = issue_token(connection, account.id)

    row = connection.execute("SELECT * FROM auth_tokens WHERE id = ?", (token_id,)).fetchone()
    assert row["token_hash"] == hashlib.sha256(raw.encode()).hexdigest()
    assert raw not in [str(value) for value in tuple(row)]
    assert len(raw) >= 43  # 256 bits, URL-safe


def test_a_token_that_names_nobody_resolves_to_nothing(connection: sqlite3.Connection) -> None:
    assert resolve_token(connection, "") is None
    assert resolve_token(connection, "not-a-token") is None
    with pytest.raises(UnknownUser):
        issue_token(connection, 42)


def test_a_device_label_is_cleaned_and_bounded(connection: sqlite3.Connection) -> None:
    account = create_user(connection, "reader", PASSWORD)
    _, long_id = issue_token(connection, account.id, "  iPad\n" + "x" * 500)
    _, blank_id = issue_token(connection, account.id, "   ")

    devices = dict(connection.execute("SELECT id, device FROM auth_tokens").fetchall())
    assert devices[long_id] == ("iPad" + "x" * 500)[: accounts.DEVICE_MAX]
    assert devices[blank_id] is None


def test_last_used_is_written_at_most_once_a_minute(connection: sqlite3.Connection) -> None:
    account = create_user(connection, "reader", PASSWORD)
    raw, token_id = issue_token(connection, account.id)

    stamp(connection, token_id, "2026-01-01T00:00:00+00:00")
    assert resolve_token(connection, raw) is not None
    touched = last_used(connection, token_id)
    assert touched > "2026-10-01"

    # Within the minute: the request reads, and writes nothing.
    recent = touched
    assert resolve_token(connection, raw) is not None
    assert last_used(connection, token_id) == recent


def test_a_locked_database_skips_the_last_used_write(database: Database, tmp_path: Path) -> None:
    """A scan holding the write lock delays nobody's request for a timestamp."""
    connection = database.connection
    account = create_user(connection, "reader", PASSWORD)
    raw, token_id = issue_token(connection, account.id)
    stamp(connection, token_id, "2026-01-01T00:00:00+00:00")

    writer = sqlite3.connect(database.path, isolation_level=None)
    try:
        writer.execute("BEGIN IMMEDIATE")
        started = time.monotonic()
        resolved = resolve_token(connection, raw)
        elapsed = time.monotonic() - started
        writer.execute("ROLLBACK")
    finally:
        writer.close()

    assert resolved is not None and resolved.account == account
    assert elapsed < 2
    assert last_used(connection, token_id) == "2026-01-01T00:00:00+00:00"
    # And the connection is back to its usual patience afterwards.
    assert connection.execute("PRAGMA busy_timeout").fetchone()[0] == 10_000


# -------------------------------------------------------------------- bootstrap


def test_bootstrap_creates_the_first_admin(database: Database, tmp_path: Path) -> None:
    settings = quiet_settings(
        tmp_path, tmp_path, admin_username="lettore", admin_password=SecretStr(PASSWORD)
    )

    created = bootstrap(settings, database)

    assert created is not None
    assert created.username == "lettore" and created.is_admin
    assert authenticate(database.connection, "lettore", PASSWORD) == created


def test_bootstrap_defaults_to_an_admin_called_admin(database: Database, tmp_path: Path) -> None:
    settings = quiet_settings(tmp_path, tmp_path, admin_password=PASSWORD)

    created = bootstrap(settings, database)

    assert created is not None and created.username == "admin"


def test_without_a_password_bootstrap_says_how_to_recover(
    database: Database, tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    settings = quiet_settings(tmp_path, tmp_path, admin_password=None)

    with caplog.at_level(logging.WARNING, logger="paperstand"):
        assert bootstrap(settings, database) is None

    errors = [record.getMessage() for record in caplog.records if record.levelno >= logging.ERROR]
    assert len(errors) == 1
    assert "PAPERSTAND_ADMIN_PASSWORD" in errors[0]
    assert "paperstand users add" in errors[0]
    assert list_users(database.connection) == []


def test_an_empty_password_counts_as_unset(database: Database, tmp_path: Path) -> None:
    settings = quiet_settings(tmp_path, tmp_path, admin_password="")

    assert settings.admin_password is None
    assert bootstrap(settings, database) is None
    assert list_users(database.connection) == []


def test_bootstrap_ignores_the_password_once_accounts_exist(
    database: Database, tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    create_user(database.connection, "reader", PASSWORD)
    settings = quiet_settings(tmp_path, tmp_path, admin_password="another password")

    with caplog.at_level(logging.WARNING, logger="paperstand"):
        assert bootstrap(settings, database) is None

    warnings = [
        record.getMessage() for record in caplog.records if record.levelno >= logging.WARNING
    ]
    assert len(warnings) == 1
    assert "can be unset" in warnings[0]
    assert [account.username for account in list_users(database.connection)] == ["reader"]


def test_bootstrap_is_quiet_once_accounts_exist_and_the_password_is_gone(
    database: Database, tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    create_user(database.connection, "reader", PASSWORD)
    settings = quiet_settings(tmp_path, tmp_path, admin_password=None)

    with caplog.at_level(logging.WARNING, logger="paperstand"):
        assert bootstrap(settings, database) is None

    assert [record for record in caplog.records if record.levelno >= logging.WARNING] == []


def test_a_password_bootstrap_cannot_use_is_logged_not_raised(
    database: Database, tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    settings = quiet_settings(tmp_path, tmp_path, admin_password="short")

    with caplog.at_level(logging.WARNING, logger="paperstand"):
        assert bootstrap(settings, database) is None

    errors = [record.getMessage() for record in caplog.records if record.levelno >= logging.ERROR]
    assert len(errors) == 1 and "at least 8 characters" in errors[0]
    assert list_users(database.connection) == []
