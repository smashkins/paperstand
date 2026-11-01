"""Who is asking: the three credentials, and ``/api/auth/*``.

Every protected route accepts three kinds of credential, tried in this order,
and the first one that names a live account wins — one that does not is
skipped rather than refused, so a stale cookie never hides a good password:

1. ``Authorization: Bearer <token>`` — native clients, which keep the token
   :func:`paperstand.accounts.issue_token` handed them at sign-in.
2. The ``paperstand_session`` cookie — the web interface. It holds the same
   kind of token, set ``HttpOnly`` and ``SameSite=Lax`` by ``/api/auth/login``.
3. ``Authorization: Basic`` with a Paperstand username and password — OPDS
   readers, which know nothing else. Accepted on every route, so the cover,
   page and file URLs a feed links to work with the same credentials.

The cookie comes before Basic so that a browser behind a proxy doing basic
auth of its own still signs in with its cookie.

A Basic check costs a scrypt verification, and a reading app sends the header
with every thumbnail of a feed. A successful one is remembered by
:class:`BasicCache`, together with the password hash it was verified against;
a hit costs one indexed ``SELECT`` that must find the same hash still stored.
A password changed anywhere — the API, or ``paperstand users password`` in
another process — is therefore refused on the very next request, without a
TTL to wait out. Failures are never cached.

Access is enforced centrally: :func:`paperstand.main.create_app` builds one
:class:`Authenticator` and includes every router with one of its dependencies
— :attr:`Authenticator.user` for the catalogue, :attr:`Authenticator.admin`
for maintenance, :attr:`Authenticator.opds_user` for ``/opds``, whose ``401``
carries the Basic challenge a reading app needs to ask for a password. Under
``/api`` a ``401`` never carries one: a browser would pop its own dialog over
the web interface's sign-in page. FastAPI caches a dependency per request, so
a route that asks for the caller again — or for ``admin`` on top of ``user``
— resolves the credential once.
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import sqlite3
import threading
from collections import OrderedDict
from collections.abc import Callable
from dataclasses import dataclass
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Request, Response

from paperstand import accounts
from paperstand.accounts import Account
from paperstand.api.common import CATALOGUE_UNAVAILABLE, catalogue
from paperstand.db import Database
from paperstand.logging import get_logger
from paperstand.schemas import LoginRequest, LoginResponse, PasswordChange, User

log = get_logger(__name__)

COOKIE_NAME = "paperstand_session"

#: Tokens never expire; 400 days is the longest a browser keeps a cookie.
COOKIE_MAX_AGE = 400 * 24 * 60 * 60

#: The challenge a ``401`` under ``/opds`` carries.
CHALLENGE = 'Basic realm="Paperstand"'

#: How many verified Basic credentials are remembered; the oldest go first.
BASIC_CACHE_SIZE = 256

NOT_SIGNED_IN = "not signed in"
WRONG_CREDENTIALS = "wrong username or password"
WRONG_PASSWORD = "the current password is wrong"
ADMIN_ONLY = "only an administrator can do this"
CROSS_SITE = "a request from another site cannot use the session cookie to change anything"

#: Methods that change nothing, and so need no protection from another site.
SAFE_METHODS = frozenset({"GET", "HEAD", "OPTIONS", "TRACE"})

#: ``Sec-Fetch-Site`` values that mean the page itself, or the user, asked.
SAME_ORIGIN = frozenset({"same-origin", "none"})

Credential = Literal["bearer", "cookie", "basic"]


@dataclass(frozen=True)
class Caller:
    """The account a request is made as, and how it proved it.

    ``token_id`` is the token behind a bearer or cookie credential — what
    signing out revokes, and what a password change keeps — and ``None`` for
    Basic, which has no token.
    """

    account: Account
    credential: Credential
    token_id: int | None


def user_model(account: Account) -> User:
    """The public shape of an account."""
    return User(
        id=account.id,
        username=account.username,
        is_admin=account.is_admin,
        created_at=account.created_at,
    )


def parse_basic(credentials: str) -> tuple[str, str] | None:
    """The username and password in a Basic credential, decoded as UTF-8.

    Parsed here rather than with FastAPI's ``HTTPBasic``, which decodes ASCII
    only and answers malformed base64 with a challenge of its own even when
    told not to — a challenge that must never appear under ``/api``.
    """
    try:
        decoded = base64.b64decode(credentials, validate=True).decode("utf-8")
    except (binascii.Error, ValueError):
        return None
    username, separator, password = decoded.partition(":")
    if not separator:
        return None
    return username, password


class BasicCache:
    """Recently verified Basic credentials, bounded and shared by every request thread.

    The key is the SHA-256 of ``username\\0password`` — never the password
    itself — and the value the account's id with the hash it was verified
    against. Least recently used entries are dropped first.
    """

    def __init__(self, size: int = BASIC_CACHE_SIZE) -> None:
        self.size = size
        self._entries: OrderedDict[bytes, tuple[int, str]] = OrderedDict()
        self._lock = threading.Lock()

    @staticmethod
    def key(username: str, password: str) -> bytes:
        """The cache key of one credential."""
        return hashlib.sha256(f"{username}\0{password}".encode("utf-8", "surrogatepass")).digest()

    def get(self, key: bytes) -> tuple[int, str] | None:
        """The account id and password hash remembered for ``key``."""
        with self._lock:
            entry = self._entries.get(key)
            if entry is not None:
                self._entries.move_to_end(key)
            return entry

    def put(self, key: bytes, user_id: int, password_hash: str) -> None:
        """Remember a credential that just verified."""
        with self._lock:
            self._entries[key] = (user_id, password_hash)
            self._entries.move_to_end(key)
            while len(self._entries) > self.size:
                self._entries.popitem(last=False)

    def discard(self, key: bytes) -> None:
        """Forget a credential that no longer verifies."""
        with self._lock:
            self._entries.pop(key, None)

    def __len__(self) -> int:
        with self._lock:
            return len(self._entries)


class Authenticator:
    """The credentials of a request, checked against one catalogue.

    Its four dependencies are attributes rather than methods because
    :attr:`admin` is built on :attr:`user` through ``Depends`` — which is what
    lets FastAPI resolve the credential once per request — and a method's
    default cannot name ``self``. Every one of them is a plain ``def``, so
    FastAPI runs it in the thread pool: a scrypt check never blocks the event
    loop, and each thread uses its own database connection.
    """

    optional_user: Callable[..., Caller | None]
    """The caller, or ``None``: never a ``401``, and never a ``503`` either —
    ``/api/health`` must answer whatever state the catalogue is in."""

    user: Callable[..., Caller]
    """The caller, or a ``401`` without a challenge."""

    opds_user: Callable[..., Caller]
    """The caller, or a ``401`` with the Basic challenge."""

    admin: Callable[..., Caller]
    """The caller when an administrator; a ``403`` when signed in as anybody else."""

    def __init__(self, database: Database | None, *, cache_size: int = BASIC_CACHE_SIZE) -> None:
        self.database = database
        self.basic_cache = BasicCache(cache_size)

        def optional_user(request: Request) -> Caller | None:
            try:
                return self.identify(request)
            except HTTPException as error:
                if error.status_code == 503:
                    return None
                raise

        def user(request: Request) -> Caller:
            caller = self.identify(request)
            if caller is None:
                raise HTTPException(status_code=401, detail=NOT_SIGNED_IN)
            return caller

        def opds_user(request: Request) -> Caller:
            caller = self.identify(request)
            if caller is None:
                raise HTTPException(
                    status_code=401,
                    detail=NOT_SIGNED_IN,
                    headers={"WWW-Authenticate": CHALLENGE},
                )
            return caller

        # A default rather than `Annotated[Caller, Depends(user)]`: this module
        # uses postponed annotations, FastAPI resolves a string annotation
        # against the module's globals only, and `user` is a local name. The
        # annotated form would silently turn `caller` into a query parameter.
        def admin(caller: Caller = Depends(user)) -> Caller:
            if not caller.account.is_admin:
                raise HTTPException(status_code=403, detail=ADMIN_ONLY)
            return caller

        self.optional_user = optional_user
        self.user = user
        self.opds_user = opds_user
        self.admin = admin

    def identify(self, request: Request) -> Caller | None:
        """The first credential of the request that names a live account.

        ``None`` when there is none at all, or none that is valid. A ``503``
        when there is one to check and no catalogue to check it against: a
        client holding a good token must not be told it was signed out
        because the database could not be opened. A ``403`` for a cookie used
        by another site to change something.
        """
        scheme, _, value = request.headers.get("authorization", "").partition(" ")
        scheme = scheme.lower()
        value = value.strip()
        bearer = value if scheme == "bearer" else ""
        basic = value if scheme == "basic" else ""
        cookie = request.cookies.get(COOKIE_NAME, "")
        if not (bearer or basic or cookie):
            return None
        connection = catalogue(self.database)
        try:
            if bearer:
                caller = self._token(connection, bearer, "bearer")
                if caller is not None:
                    return caller
            if cookie:
                caller = self._token(connection, cookie, "cookie")
                if caller is not None:
                    refuse_cross_site(request)
                    return caller
            if basic:
                account = self._basic(connection, basic)
                if account is not None:
                    return Caller(account=account, credential="basic", token_id=None)
        except sqlite3.Error as error:
            log.warning("cannot check a credential: %s", error)
            raise HTTPException(status_code=503, detail=CATALOGUE_UNAVAILABLE) from error
        return None

    @staticmethod
    def _token(connection: sqlite3.Connection, raw: str, credential: Credential) -> Caller | None:
        resolved = accounts.resolve_token(connection, raw)
        if resolved is None:
            return None
        return Caller(account=resolved.account, credential=credential, token_id=resolved.token_id)

    def _basic(self, connection: sqlite3.Connection, credentials: str) -> Account | None:
        parsed = parse_basic(credentials)
        if parsed is None:
            return None
        username, password = parsed
        key = BasicCache.key(username, password)
        cached = self.basic_cache.get(key)
        if cached is not None:
            user_id, verified_hash = cached
            account = accounts.get_user(connection, user_id)
            if account is not None and account.password_hash == verified_hash:
                return account
            self.basic_cache.discard(key)
        account = accounts.authenticate(connection, username, password)
        if account is not None:
            self.basic_cache.put(key, account.id, account.password_hash)
        return account


def refuse_cross_site(request: Request) -> None:
    """A ``403`` for a cookie-carrying request from another site that changes something.

    ``SameSite=Lax`` keeps the cookie off a cross-site ``POST``, but not off one
    from a sibling subdomain, which is the *same site*: a page on another host
    of the same domain could otherwise start a scan with the reader's cookie.
    A browser says where a request came from in ``Sec-Fetch-Site``; bearer and
    Basic clients never send the cookie and are never asked.
    """
    if request.method in SAFE_METHODS:
        return
    site = request.headers.get("sec-fetch-site")
    if site is not None and site.lower() not in SAME_ORIGIN:
        raise HTTPException(status_code=403, detail=CROSS_SITE)


def set_session_cookie(response: Response, request: Request, token: str) -> None:
    """Set the session cookie; ``Secure`` when the client reached us over https.

    ``request.url.scheme`` is what the client used: behind a trusted reverse
    proxy, :class:`paperstand.proxy.ForwardedHeadersMiddleware` has already
    replaced the plain-http hop with the proxy's ``X-Forwarded-Proto``.
    """
    response.set_cookie(
        COOKIE_NAME,
        token,
        max_age=COOKIE_MAX_AGE,
        path="/",
        secure=request.url.scheme == "https",
        httponly=True,
        samesite="lax",
    )


def clear_session_cookie(response: Response, request: Request) -> None:
    """Tell the browser to forget the session cookie."""
    response.delete_cookie(
        COOKIE_NAME,
        path="/",
        secure=request.url.scheme == "https",
        httponly=True,
        samesite="lax",
    )


def create_router(authenticator: Authenticator, database: Database | None) -> APIRouter:
    """Build the ``/api/auth`` router.

    Included without a router-wide dependency, because signing in is the one
    thing nobody can be signed in for; the other three routes ask for the
    caller themselves.
    """
    router = APIRouter(prefix="/api/auth", tags=["auth"])

    @router.post("/login", responses={401: {"description": WRONG_CREDENTIALS}})
    def login(body: LoginRequest, request: Request, response: Response) -> LoginResponse:
        """Sign in: a new token, in the body for a native client and in the cookie for a browser.

        An unknown name and a wrong password are the same ``401`` and take the
        same time. The response is ``no-store``: it is the one that carries a
        token in its body.
        """
        connection = catalogue(database)
        account = accounts.authenticate(connection, body.username, body.password)
        if account is None:
            raise HTTPException(status_code=401, detail=WRONG_CREDENTIALS)
        token, _ = accounts.issue_token(connection, account.id, body.device)
        set_session_cookie(response, request, token)
        response.headers["Cache-Control"] = "no-store"
        return LoginResponse(token=token, user=user_model(account))

    @router.post("/logout", status_code=204, response_class=Response)
    def logout(request: Request, caller: Caller = Depends(authenticator.user)) -> Response:
        """Revoke the token or cookie this request was made with, and clear the cookie.

        Basic credentials have no token to revoke; the answer is a ``204`` all
        the same.
        """
        if caller.token_id is not None:
            accounts.revoke_token(catalogue(database), caller.token_id)
        response = Response(status_code=204)
        clear_session_cookie(response, request)
        return response

    @router.get("/me")
    def me(caller: Caller = Depends(authenticator.user)) -> User:
        """The account this request is made as."""
        return user_model(caller.account)

    @router.put(
        "/password",
        status_code=204,
        response_class=Response,
        responses={403: {"description": WRONG_PASSWORD}},
    )
    def change_password(
        body: PasswordChange, caller: Caller = Depends(authenticator.user)
    ) -> Response:
        """Change the caller's own password, and sign out every other device.

        The token this request was made with stays valid, so the device that
        changed the password is the one that stays signed in.
        """
        connection = catalogue(database)
        if not accounts.verify_password(body.current_password, caller.account.password_hash):
            raise HTTPException(status_code=403, detail=WRONG_PASSWORD)
        try:
            accounts.set_password(
                connection, caller.account.id, body.new_password, keep_token_id=caller.token_id
            )
        except accounts.UnknownUser as error:  # deleted while this request ran
            raise HTTPException(status_code=401, detail=NOT_SIGNED_IN) from error
        return Response(status_code=204)

    return router
