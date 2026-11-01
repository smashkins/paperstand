"""Accounts: who may read the catalogue, and how they prove it.

The rules live here, with no HTTP in them, so that the API
(:mod:`paperstand.api.auth`, :mod:`paperstand.api.users`) and the command
line (:mod:`paperstand.cli.users`) enforce exactly the same ones — the CLI is
the recovery route, and a rule only the API knew would be one the CLI could
break.

Three things are decided here rather than left to a caller:

* **Passwords** are hashed with the standard library's scrypt. The cost
  parameters travel inside the stored hash, so :data:`SCRYPT_N` can be raised
  later without invalidating a single existing password, and a test can lower
  it without a hash written under the test cost being unreadable.
* **Tokens** are 256 random bits handed to the client once and stored only as
  their SHA-256: a slow hash buys nothing against a secret nobody can guess,
  and a copy of the database is not a copy of anybody's session.
* **There is always an administrator.** Demoting or deleting one is a single
  guarded statement — ``... WHERE id = ? AND (another admin exists)`` — and
  its ``rowcount`` says whether it happened. SQLite runs one writer at a time
  and evaluates the guard under the write lock, so two administrators demoting
  each other at the same instant cannot both win and leave nobody in charge.
"""

from __future__ import annotations

import base64
import binascii
import datetime as dt
import hashlib
import hmac
import re
import secrets
import sqlite3
import unicodedata
from dataclasses import dataclass, field
from typing import NamedTuple

from paperstand.config import Settings
from paperstand.db import BUSY_TIMEOUT_MS, Database, utc_now
from paperstand.logging import get_logger

log = get_logger(__name__)

#: scrypt's CPU/memory cost. Read at hashing time, so a test can lower it.
SCRYPT_N = 2**14
#: scrypt's block size.
SCRYPT_R = 8
#: scrypt's parallelisation.
SCRYPT_P = 1
SALT_BYTES = 16
KEY_BYTES = 32

#: The most memory a stored hash may ask scrypt for. A hash is data read from
#: the database, and a corrupted one must not be able to take the server down.
MAX_SCRYPT_MEMORY = 256 * 1024 * 1024

USERNAME_MAX = 64
PASSWORD_MIN = 8
PASSWORD_MAX = 1024

#: A device name is a label a client sends at sign-in; longer ones are cut.
DEVICE_MAX = 128

TOKEN_BYTES = 32

#: How stale ``last_used_at`` may get before a request writes it again.
TOUCH_INTERVAL = dt.timedelta(minutes=1)

#: How long that write waits for a lock, in milliseconds, before it is skipped.
TOUCH_BUSY_TIMEOUT_MS = 50

#: Unicode categories a username may not contain: control and format
#: characters (invisible, or able to reorder what a log line shows), lone
#: surrogates (not encodable, so not storable) and the line and paragraph
#: separators.
FORBIDDEN_CATEGORIES = frozenset({"Cc", "Cf", "Cs", "Zl", "Zp"})

_HASH = re.compile(
    r"^scrypt\$([1-9][0-9]{0,9})\$([1-9][0-9]{0,3})\$([1-9][0-9]{0,3})"
    r"\$([A-Za-z0-9+/]+={0,2})\$([A-Za-z0-9+/]+={0,2})$"
)

_USER_COLUMNS = "id, username, password_hash, is_admin, created_at"

# The guard every statement that could remove an administrator carries: the
# row is not an admin, or some *other* row is. One parameter, the row's id.
_KEEPS_AN_ADMIN = (
    "(is_admin = 0 OR (SELECT count(*) FROM users AS other "
    "WHERE other.is_admin = 1 AND other.id <> ?) > 0)"
)


class AccountError(Exception):
    """An account operation that was refused."""


class UnknownUser(AccountError):
    """No account has that id or name."""


class UsernameTaken(AccountError):
    """Another account already has that name, in some spelling of its case."""


class LastAdmin(AccountError):
    """The change would leave the server without an administrator."""


class SelfDelete(AccountError):
    """An administrator tried to delete their own account."""


@dataclass(frozen=True)
class Account:
    """One row of ``users``.

    ``password_hash`` is carried along because the Basic credential cache
    compares it to tell a password changed since it was verified; it is kept
    out of ``repr`` so that it never lands in a log line by accident.
    """

    id: int
    username: str
    is_admin: bool
    created_at: str
    password_hash: str = field(repr=False)


@dataclass(frozen=True)
class ResolvedToken:
    """A bearer or session token that names a live account."""

    token_id: int
    account: Account


class ScryptHash(NamedTuple):
    """A stored password hash, taken apart."""

    n: int
    r: int
    p: int
    salt: bytes
    key: bytes


# ------------------------------------------------------------------ validation


def validate_username(value: str) -> str:
    """The username ``value`` names, trimmed, or a :class:`ValueError`.

    No ``:``, because a Basic credential is split on the first one, and
    nothing invisible, because a name is something an administrator reads.
    """
    name = value.strip()
    if not name:
        raise ValueError("a username cannot be empty")
    if len(name) > USERNAME_MAX:
        raise ValueError(f"a username is at most {USERNAME_MAX} characters")
    if ":" in name:
        raise ValueError("a username cannot contain ':'")
    if any(unicodedata.category(char) in FORBIDDEN_CATEGORIES for char in name):
        raise ValueError("a username cannot contain control characters")
    return name


def validate_password(value: str) -> str:
    """``value`` itself, when it is acceptable as a password; never trimmed."""
    if len(value) < PASSWORD_MIN:
        raise ValueError(f"a password is at least {PASSWORD_MIN} characters")
    if len(value) > PASSWORD_MAX:
        raise ValueError(f"a password is at most {PASSWORD_MAX} characters")
    if any(unicodedata.category(char) == "Cs" for char in value):
        raise ValueError("a password must be valid Unicode text")
    return value


# --------------------------------------------------------------------- hashing


def _b64(raw: bytes) -> str:
    return base64.b64encode(raw).decode("ascii")


def _maxmem(n: int, r: int, p: int) -> int:
    """What scrypt needs for these parameters, with a margin, in bytes.

    OpenSSL refuses to run past its own default of 32 MiB unless told
    otherwise; saying so explicitly is what lets a stored hash ask for more
    later, and :data:`MAX_SCRYPT_MEMORY` is what stops it asking for too much.
    """
    return 128 * r * (n + p + 2) + 1024 * 1024


def _derive(password: str, salt: bytes, n: int, r: int, p: int, length: int) -> bytes:
    return hashlib.scrypt(
        password.encode("utf-8", "surrogatepass"),
        salt=salt,
        n=n,
        r=r,
        p=p,
        maxmem=_maxmem(n, r, p),
        dklen=length,
    )


def hash_password(password: str) -> str:
    """``scrypt$<n>$<r>$<p>$<salt b64>$<hash b64>``, with a fresh salt."""
    n, r, p = SCRYPT_N, SCRYPT_R, SCRYPT_P
    salt = secrets.token_bytes(SALT_BYTES)
    key = _derive(password, salt, n, r, p, KEY_BYTES)
    return "$".join(("scrypt", str(n), str(r), str(p), _b64(salt), _b64(key)))


def parse_hash(stored: str) -> ScryptHash | None:
    """The parts of a stored hash, or ``None`` when it is not one this build reads.

    ``None`` covers a malformed string as well as parameters that are not a
    valid scrypt cost (``n`` a power of two above one) or that would need more
    than :data:`MAX_SCRYPT_MEMORY`: verifying against any of them is simply a
    failed sign-in, never an exception.
    """
    match = _HASH.match(stored)
    if match is None:
        return None
    n, r, p = (int(match.group(index)) for index in (1, 2, 3))
    try:
        salt = base64.b64decode(match.group(4), validate=True)
        key = base64.b64decode(match.group(5), validate=True)
    except (binascii.Error, ValueError):
        return None
    if n < 2 or n & (n - 1) or p > 16:
        return None
    if not 1 <= len(salt) <= 64 or not 1 <= len(key) <= 64:
        return None
    if _maxmem(n, r, p) > MAX_SCRYPT_MEMORY:
        return None
    return ScryptHash(n=n, r=r, p=p, salt=salt, key=key)


def verify_password(password: str, stored: str) -> bool:
    """Whether ``password`` is the one ``stored`` was made from."""
    parsed = parse_hash(stored)
    if parsed is None:
        return False
    candidate = _derive(password, parsed.salt, parsed.n, parsed.r, parsed.p, len(parsed.key))
    return hmac.compare_digest(candidate, parsed.key)


_dummy_hashes: dict[tuple[int, int, int], str] = {}


def _dummy_hash() -> str:
    """A hash of nothing anybody knows, at the current cost, made once per cost."""
    params = (SCRYPT_N, SCRYPT_R, SCRYPT_P)
    stored = _dummy_hashes.get(params)
    if stored is None:
        stored = hash_password(secrets.token_urlsafe(16))
        _dummy_hashes[params] = stored
    return stored


# ------------------------------------------------------------------- the users


def _account(row: sqlite3.Row) -> Account:
    return Account(
        id=int(row["id"]),
        username=str(row["username"]),
        is_admin=bool(row["is_admin"]),
        created_at=str(row["created_at"]),
        password_hash=str(row["password_hash"]),
    )


def count_users(connection: sqlite3.Connection) -> int:
    """How many accounts exist."""
    row = connection.execute("SELECT count(*) AS total FROM users").fetchone()
    return int(row["total"])


def get_user(connection: sqlite3.Connection, user_id: int) -> Account | None:
    """The account with this id."""
    row = connection.execute(
        f"SELECT {_USER_COLUMNS} FROM users WHERE id = ?", (user_id,)
    ).fetchone()
    return _account(row) if row is not None else None


def find_user(connection: sqlite3.Connection, username: str) -> Account | None:
    """The account with this name, trimmed and compared without regard to case."""
    row = connection.execute(
        f"SELECT {_USER_COLUMNS} FROM users WHERE username = ?", (username.strip(),)
    ).fetchone()
    return _account(row) if row is not None else None


def list_users(connection: sqlite3.Connection) -> list[Account]:
    """Every account, by name without regard to case (the column's collation)."""
    rows = connection.execute(f"SELECT {_USER_COLUMNS} FROM users ORDER BY username, id")
    return [_account(row) for row in rows]


def authenticate(connection: sqlite3.Connection, username: str, password: str) -> Account | None:
    """The account these credentials sign in as, or ``None``.

    An unknown name costs exactly what a wrong password does — a dummy hash is
    verified in its place — so the time a refusal takes does not say which
    names exist.
    """
    account = find_user(connection, username)
    if account is None:
        verify_password(password, _dummy_hash())
        return None
    if not verify_password(password, account.password_hash):
        return None
    return account


def create_user(
    connection: sqlite3.Connection,
    username: str,
    password: str,
    *,
    is_admin: bool = False,
) -> Account:
    """Create an account; :class:`ValueError` or :class:`UsernameTaken` if it cannot be."""
    name = validate_username(username)
    password_hash = hash_password(validate_password(password))
    created_at = utc_now()
    try:
        with connection:
            cursor = connection.execute(
                "INSERT INTO users (username, password_hash, is_admin, created_at) "
                "VALUES (?, ?, ?, ?)",
                (name, password_hash, int(is_admin), created_at),
            )
    except sqlite3.IntegrityError as error:
        raise UsernameTaken(f"the username {name!r} is taken") from error
    assert cursor.lastrowid is not None
    return Account(
        id=int(cursor.lastrowid),
        username=name,
        is_admin=is_admin,
        created_at=created_at,
        password_hash=password_hash,
    )


def set_password(
    connection: sqlite3.Connection,
    user_id: int,
    password: str,
    *,
    keep_token_id: int | None = None,
) -> int:
    """Give an account a new password and sign out its other devices.

    Every token of the account is revoked except ``keep_token_id`` — the one
    the change was made with, when it was made through the API — in the same
    transaction as the new hash. Returns how many tokens were revoked.
    """
    password_hash = hash_password(validate_password(password))
    with connection:
        cursor = connection.execute(
            "UPDATE users SET password_hash = ? WHERE id = ?", (password_hash, user_id)
        )
        if cursor.rowcount == 0:
            raise UnknownUser(f"no account has the id {user_id}")
        revoked = connection.execute(
            "DELETE FROM auth_tokens WHERE user_id = ? AND id IS NOT ?",
            (user_id, keep_token_id),
        ).rowcount
    return int(revoked)


def set_admin(connection: sqlite3.Connection, user_id: int, is_admin: bool) -> Account:
    """Promote or demote an account; :class:`LastAdmin` when nobody would be left."""
    with connection:
        if is_admin:
            cursor = connection.execute("UPDATE users SET is_admin = 1 WHERE id = ?", (user_id,))
        else:
            cursor = connection.execute(
                f"UPDATE users SET is_admin = 0 WHERE id = ? AND {_KEEPS_AN_ADMIN}",
                (user_id, user_id),
            )
        changed = cursor.rowcount
    account = get_user(connection, user_id)
    if account is None:
        raise UnknownUser(f"no account has the id {user_id}")
    if changed == 0:
        raise LastAdmin("this would leave no administrator")
    return account


def delete_user(
    connection: sqlite3.Connection, user_id: int, *, acting_user_id: int | None
) -> None:
    """Delete an account and, by ``ON DELETE CASCADE``, its tokens.

    ``acting_user_id`` is who asked: nobody deletes their own account — an
    administrator who could would be one click from locking everybody out —
    and the last administrator is never deleted by anybody.
    """
    if acting_user_id is not None and user_id == acting_user_id:
        raise SelfDelete("an administrator cannot delete their own account")
    with connection:
        cursor = connection.execute(
            f"DELETE FROM users WHERE id = ? AND {_KEEPS_AN_ADMIN}", (user_id, user_id)
        )
        deleted = cursor.rowcount
    if deleted == 0:
        if get_user(connection, user_id) is None:
            raise UnknownUser(f"no account has the id {user_id}")
        raise LastAdmin("this would leave no administrator")


# ------------------------------------------------------------------ the tokens


def token_hash(raw: str) -> str:
    """What ``auth_tokens.token_hash`` stores for a token."""
    return hashlib.sha256(raw.encode("utf-8", "surrogatepass")).hexdigest()


def _device(device: str | None) -> str | None:
    if device is None:
        return None
    label = "".join(
        char for char in device if unicodedata.category(char) not in FORBIDDEN_CATEGORIES
    ).strip()
    return label[:DEVICE_MAX] or None


def issue_token(
    connection: sqlite3.Connection, user_id: int, device: str | None = None
) -> tuple[str, int]:
    """A new token for an account: the raw value, shown once, and its row id."""
    raw = secrets.token_urlsafe(TOKEN_BYTES)
    now = utc_now()
    try:
        with connection:
            cursor = connection.execute(
                "INSERT INTO auth_tokens (token_hash, user_id, device, created_at, last_used_at) "
                "VALUES (?, ?, ?, ?, ?)",
                (token_hash(raw), user_id, _device(device), now, now),
            )
    except sqlite3.IntegrityError as error:
        raise UnknownUser(f"no account has the id {user_id}") from error
    assert cursor.lastrowid is not None
    return raw, int(cursor.lastrowid)


def resolve_token(connection: sqlite3.Connection, raw: str) -> ResolvedToken | None:
    """The account a token belongs to, or ``None`` for one that names nobody.

    One indexed lookup, plus — at most once a minute per token — a write of
    ``last_used_at``, which is best effort: see :func:`_touch`.
    """
    if not raw:
        return None
    row = connection.execute(
        "SELECT t.id AS token_id, t.last_used_at, "
        "u.id, u.username, u.password_hash, u.is_admin, u.created_at "
        "FROM auth_tokens AS t JOIN users AS u ON u.id = t.user_id "
        "WHERE t.token_hash = ?",
        (token_hash(raw),),
    ).fetchone()
    if row is None:
        return None
    token_id = int(row["token_id"])
    _touch(connection, token_id, str(row["last_used_at"]))
    return ResolvedToken(token_id=token_id, account=_account(row))


def _touch(connection: sqlite3.Connection, token_id: int, last_used_at: str) -> None:
    """Record that a token was used, unless it was in the last minute.

    A scan holds the write lock for as long as a batch takes, and the
    connection's own ``busy_timeout`` would make a request wait up to ten
    seconds for it — for a timestamp nobody needs to the second. The write
    waits :data:`TOUCH_BUSY_TIMEOUT_MS` instead and is skipped when that is
    not enough: a request never fails, and never stalls, because of it. The
    ``WHERE`` repeats the value read, so of two requests racing to touch the
    same token only one writes.
    """
    if connection.in_transaction:
        return
    now = dt.datetime.now(dt.UTC)
    try:
        previous = dt.datetime.fromisoformat(last_used_at)
    except ValueError:
        previous = None
    if previous is not None:
        if previous.tzinfo is None:
            previous = previous.replace(tzinfo=dt.UTC)
        if now - previous < TOUCH_INTERVAL:
            return
    stamp = now.replace(microsecond=0).isoformat()
    try:
        connection.execute(f"PRAGMA busy_timeout = {TOUCH_BUSY_TIMEOUT_MS}")
        try:
            with connection:
                connection.execute(
                    "UPDATE auth_tokens SET last_used_at = ? WHERE id = ? AND last_used_at = ?",
                    (stamp, token_id, last_used_at),
                )
        finally:
            connection.execute(f"PRAGMA busy_timeout = {BUSY_TIMEOUT_MS}")
    except sqlite3.OperationalError as error:
        log.debug("did not record the use of token %d: %s", token_id, error)


def revoke_token(connection: sqlite3.Connection, token_id: int) -> bool:
    """Forget one token; whether there was one to forget."""
    with connection:
        cursor = connection.execute("DELETE FROM auth_tokens WHERE id = ?", (token_id,))
    return cursor.rowcount > 0


# ------------------------------------------------------------------- bootstrap


def bootstrap(settings: Settings, database: Database) -> Account | None:
    """Create the first administrator from the environment, when there is none.

    Called once at startup, right after the catalogue opens. Three cases:

    * no account and ``PAPERSTAND_ADMIN_PASSWORD`` set — the administrator
      ``PAPERSTAND_ADMIN_USERNAME`` is created, and returned;
    * no account and no password — an error names both ways out, and the
      server runs on with every protected route answering ``401``: refusing to
      start would turn a missing variable into a restart loop;
    * accounts already exist — the password is ignored, with a warning when it
      is still set, because a secret left in the environment for nothing is a
      secret that leaks for nothing.

    Never raises: a database that cannot be read or written here is logged,
    and the catalogue routes will say the same thing on their own.
    """
    password = settings.admin_password.get_secret_value() if settings.admin_password else None
    try:
        connection = database.connection
        if count_users(connection) > 0:
            if password:
                log.warning(
                    "PAPERSTAND_ADMIN_PASSWORD is set but accounts already exist: "
                    "it is ignored and can be unset"
                )
            return None
        if not password:
            log.error(
                "no account exists, so nobody can sign in: set PAPERSTAND_ADMIN_PASSWORD "
                "(and optionally PAPERSTAND_ADMIN_USERNAME) and restart, or run "
                "`paperstand users add NAME --admin`"
            )
            return None
        account = create_user(connection, settings.admin_username, password, is_admin=True)
    except (ValueError, UsernameTaken) as error:
        log.error(
            "cannot create the administrator %r from PAPERSTAND_ADMIN_PASSWORD: %s",
            settings.admin_username,
            error,
        )
        return None
    except sqlite3.Error as error:
        log.error("cannot set up the first account: %s", error)
        return None
    log.info("created the administrator %r from PAPERSTAND_ADMIN_PASSWORD", account.username)
    return account
