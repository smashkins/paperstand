"""FastAPI application factory."""

from __future__ import annotations

import sqlite3
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

from fastapi import Depends, FastAPI
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from paperstand import __version__
from paperstand.accounts import bootstrap
from paperstand.api import auth as auth_api
from paperstand.api import files as files_api
from paperstand.api import health as health_api
from paperstand.api import images as images_api
from paperstand.api import issues as issues_api
from paperstand.api import libraries as libraries_api
from paperstand.api import maintenance as maintenance_api
from paperstand.api import progress as progress_api
from paperstand.api import scan as scan_api
from paperstand.api import titles as titles_api
from paperstand.api import today as today_api
from paperstand.api import users as users_api
from paperstand.api.auth import Authenticator
from paperstand.api.common import REVALIDATE
from paperstand.config import Settings, get_settings
from paperstand.db import Database, DatabaseError, open_database
from paperstand.logging import configure_logging, get_logger
from paperstand.opds import router as opds_api
from paperstand.proxy import ForwardedHeadersMiddleware
from paperstand.render.pages import PageRenderer
from paperstand.scanner.scheduler import ScanScheduler
from paperstand.static import SPAStaticFiles

log = get_logger(__name__)

#: The paths whose responses are private to the account that asked for them.
PRIVATE_PREFIXES: tuple[str, ...] = ("/api", "/opds")


class PrivateCacheMiddleware:
    """Mark every ``/api`` and ``/opds`` response that says nothing as private.

    Everything under those paths is behind an account, and a response with no
    ``Cache-Control`` is one a shared cache in front of the server may keep and
    hand to the next client — signed in or not. Such a response is given
    ``private, no-cache``; one that set its own (the images, the PDF, the
    sign-in) keeps it.

    Pure ASGI and headers only: it rewrites the ``http.response.start``
    message and passes every body message through untouched, so a byte range
    of the PDF is exactly the bytes the file endpoint sent.
    """

    def __init__(self, app: ASGIApp, value: str = REVALIDATE) -> None:
        self.app = app
        self.value = value.encode("latin-1")

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http" or not is_private_path(scope["path"]):
            await self.app(scope, receive, send)
            return

        async def send_with_default(message: Message) -> None:
            if message["type"] == "http.response.start":
                headers = list(message.get("headers", ()))
                if not any(name.lower() == b"cache-control" for name, _ in headers):
                    headers.append((b"cache-control", self.value))
                    message = {**message, "headers": headers}
            await send(message)

        await self.app(scope, receive, send_with_default)


def is_private_path(path: str) -> bool:
    """Whether ``path`` is under one of :data:`PRIVATE_PREFIXES`."""
    return any(path == prefix or path.startswith(prefix + "/") for prefix in PRIVATE_PREFIXES)


def open_catalogue(settings: Settings) -> Database | None:
    """Open the catalogue database, or explain why the API runs without one.

    A data directory that cannot be written to is a misconfigured deployment,
    not a reason to refuse to start: the service comes up, says so in the logs
    and reports ``db_ok: false`` on ``/api/health``.
    """
    try:
        return open_database(settings.db_path)
    except (OSError, sqlite3.Error, DatabaseError) as error:
        log.error("cannot open the database at %s: %s", settings.db_path, error)
        return None


def create_app(settings: Settings | None = None) -> FastAPI:
    """Build the ASGI application.

    Everything is built here and handed to the routers as arguments — the
    settings, the database, the scheduler, the page renderer — so that a test can
    stand a whole application up around a throwaway library without a global in
    sight — the OPDS router included.

    Nothing that could fail is done at import time: the database is opened here,
    and a data directory that cannot be written to leaves ``database`` as
    ``None``, which every catalogue endpoint reports as a ``503``.

    **Every router is included with the dependency that guards it**, here and
    nowhere else, so one look at this function says who may call what:
    ``user`` for the catalogue, ``admin`` for maintenance and the accounts,
    ``opds_user`` for ``/opds``. ``/api/health``, ``/api/auth/login``, the SPA
    and FastAPI's own schema pages are the only things left public. The first
    administrator is created right after the database opens, from
    ``PAPERSTAND_ADMIN_PASSWORD``; see :func:`paperstand.accounts.bootstrap`.

    **No compression middleware is installed, and none may be.** The PDF
    endpoint serves byte ranges, and a middleware re-encoding the body would
    make the offsets a reader computed from ``Content-Range`` point at the wrong
    bytes.

    **Two middlewares, both header-only.** One reads the reverse proxy's
    ``X-Forwarded-*`` headers. It lives here rather than in the server so that
    the absolute URLs of the OPDS feed — and the ``Secure`` flag of the session
    cookie — are decided the same way under a test client as they are behind a
    proxy; :mod:`paperstand.proxy` explains what it believes and from whom. The
    other, :class:`PrivateCacheMiddleware`, marks what it has to as private
    and never touches a body.
    """
    settings = settings or get_settings()
    configure_logging(settings.log_level)

    database = open_catalogue(settings)
    if database is not None:
        bootstrap(settings, database)
    authenticator = Authenticator(database, base_url=settings.base_url)
    scheduler = ScanScheduler(settings, database) if database is not None else None
    renderer = PageRenderer(settings)

    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        if not settings.library.is_dir():
            log.warning("the library root %s does not exist", settings.library)
        if scheduler is not None:
            scheduler.start()
        try:
            yield
        finally:
            if scheduler is not None:
                scheduler.stop()
            if database is not None:
                database.close()

    app = FastAPI(
        title="Paperstand",
        version=__version__,
        description="Self-hosted reader for collections of PDF periodicals.",
        lifespan=lifespan,
    )
    app.state.settings = settings
    app.state.database = database
    app.state.scheduler = scheduler
    app.state.renderer = renderer
    app.state.authenticator = authenticator

    app.add_middleware(PrivateCacheMiddleware)
    app.add_middleware(ForwardedHeadersMiddleware, trusted=settings.trusted_proxies)

    user = [Depends(authenticator.user)]
    admin = [Depends(authenticator.admin)]

    app.include_router(health_api.create_router(settings, database, scheduler, authenticator))
    app.include_router(auth_api.create_router(authenticator, database))
    app.include_router(users_api.create_router(authenticator, database), dependencies=admin)
    # `user` for the whole router, and `admin` on `POST /api/scan` itself:
    # following a scan is for everybody, starting one is not.
    app.include_router(scan_api.create_router(scheduler, authenticator), dependencies=user)
    app.include_router(libraries_api.create_router(settings, database), dependencies=user)
    app.include_router(titles_api.create_router(database, authenticator), dependencies=user)
    app.include_router(
        today_api.create_router(settings, database, authenticator), dependencies=user
    )
    app.include_router(progress_api.create_router(database, authenticator), dependencies=user)
    app.include_router(files_api.create_router(settings, database), dependencies=user)
    app.include_router(images_api.create_router(settings, database, renderer), dependencies=user)
    app.include_router(maintenance_api.create_router(settings, database), dependencies=admin)
    # Last of the catalogue routers: `/api/issues/{issue_id}` would otherwise
    # shadow `/api/issues/{issue_id}/progress` and the image routes for a client
    # that sent a trailing slash.
    app.include_router(issues_api.create_router(database, authenticator), dependencies=user)
    app.include_router(
        opds_api.create_router(settings, database, authenticator),
        dependencies=[Depends(authenticator.opds_user)],
    )
    app.mount("/", SPAStaticFiles(settings.static), name="spa")
    return app


def __getattr__(name: str) -> Any:
    """Build the module level ``app`` the first time something asks for it.

    ``uvicorn paperstand.main:app`` and the ``serve`` command both name the
    application by that string, so it has to exist — but building it opens the
    database, and importing this module must not do that. Anything that only
    wants to *read* the module (the OpenAPI export, a test, a tool collecting
    type hints) never touches the attribute and never opens anything.
    """
    if name == "app":
        application = create_app()
        globals()["app"] = application
        return application
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
