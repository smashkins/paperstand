"""``users list``, ``users add`` and ``users password``: accounts from a shell.

The recovery route. A server with no account, or whose only administrator
forgot their password, is fixed from here, and it works while the server runs:
the catalogue is in WAL mode, a write here waits its turn behind a scan like
any other, and the server notices a changed password on the very next request
— the Basic credential cache re-reads the stored hash on every hit, and a
reset revokes every token the account held.

The rules are :mod:`paperstand.accounts`'s, the same ones the API enforces. A
password is asked for twice with :func:`getpass.getpass`, or read as one line
from standard input with ``--password-stdin`` — for a script, and so that a
password never has to sit in a shell history or a process listing.
"""

from __future__ import annotations

import getpass
import sqlite3
from pathlib import Path
from typing import TextIO

from paperstand import accounts
from paperstand.cli.parse import format_table
from paperstand.db import Database, open_database

__all__ = ["read_password", "users_add", "users_list", "users_password"]


def read_password(password_stdin: bool, stdin: TextIO, err: TextIO) -> str | None:
    """The new password, or ``None`` after saying why there is none.

    From ``stdin`` it is the first line without its line ending — spaces are
    part of a password, so nothing else is trimmed. Interactively it is asked
    for twice, and two answers that differ are refused rather than guessed at.
    """
    if password_stdin:
        line = stdin.readline()
        password = line.removesuffix("\n").removesuffix("\r")
        if not password:
            print("error: no password on standard input", file=err)
            return None
        return password
    first = getpass.getpass("Password: ")
    second = getpass.getpass("Repeat the password: ")
    if first != second:
        print("error: the two passwords differ", file=err)
        return None
    return first


def _open(data: Path, err: TextIO) -> Database | None:
    try:
        return open_database(data / "paperstand.db")
    except (OSError, sqlite3.Error) as error:
        print(f"error: cannot open the database under {data}: {error}", file=err)
        return None


def users_list(data: Path, stream: TextIO, err: TextIO) -> int:
    """Print every account: id, name, whether it administers, when it was made."""
    database = _open(data, err)
    if database is None:
        return 1
    try:
        found = accounts.list_users(database.connection)
    finally:
        database.close()
    rows = [
        (str(account.id), account.username, "yes" if account.is_admin else "no", account.created_at)
        for account in found
    ]
    print(format_table(("id", "username", "admin", "created_at"), rows), file=stream)
    print(f"{len(rows)} account(s)", file=stream)
    return 0


def users_add(
    data: Path,
    username: str,
    *,
    admin: bool,
    password_stdin: bool,
    stdin: TextIO,
    stream: TextIO,
    err: TextIO,
) -> int:
    """Create an account; ``1`` when the name is taken or not acceptable."""
    try:
        name = accounts.validate_username(username)
    except ValueError as error:
        print(f"error: {error}", file=err)
        return 1
    password = read_password(password_stdin, stdin, err)
    if password is None:
        return 1
    database = _open(data, err)
    if database is None:
        return 1
    try:
        account = accounts.create_user(database.connection, name, password, is_admin=admin)
    except (ValueError, accounts.UsernameTaken) as error:
        print(f"error: {error}", file=err)
        return 1
    finally:
        database.close()
    role = "administrator" if account.is_admin else "account"
    print(f"created the {role} {account.username!r} (id {account.id})", file=stream)
    return 0


def users_password(
    data: Path,
    username: str,
    *,
    password_stdin: bool,
    stdin: TextIO,
    stream: TextIO,
    err: TextIO,
) -> int:
    """Reset an account's password and sign out every device it was signed in on."""
    database = _open(data, err)
    if database is None:
        return 1
    try:
        account = accounts.find_user(database.connection, username)
        if account is None:
            print(f"error: no account is called {username.strip()!r}", file=err)
            return 1
        password = read_password(password_stdin, stdin, err)
        if password is None:
            return 1
        try:
            revoked = accounts.set_password(database.connection, account.id, password)
        except (ValueError, accounts.UnknownUser) as error:
            print(f"error: {error}", file=err)
            return 1
    finally:
        database.close()
    print(
        f"changed the password of {account.username!r}; {revoked} session(s) signed out",
        file=stream,
    )
    return 0
