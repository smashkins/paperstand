"""FastAPI application factory."""

from __future__ import annotations

import sqlite3
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

from fastapi import FastAPI

from paperstand import __version__
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
from paperstand.config import Settings, get_settings
from paperstand.db import Database, DatabaseError, open_database
from paperstand.logging import configure_logging, get_logger
from paperstand.opds import router as opds_api
from paperstand.proxy import ForwardedHeadersMiddleware
from paperstand.render.pages import PageRenderer
from paperstand.scanner.scheduler import ScanScheduler
from paperstand.static import SPAStaticFiles

log = get_logger(__name__)


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

    **No compression middleware is installed, and none may be.** The PDF
    endpoint serves byte ranges, and a middleware re-encoding the body would
    make the offsets a reader computed from ``Content-Range`` point at the wrong
    bytes.

    **The one middleware there is** reads the reverse proxy's ``X-Forwarded-*``
    headers. It lives here rather than in the server so that the absolute URLs
    of the OPDS feed are built the same way under a test client as they are
    behind a proxy; :mod:`paperstand.proxy` explains what it believes and from
    whom.
    """
    settings = settings or get_settings()
    configure_logging(settings.log_level)

    database = open_catalogue(settings)
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

    app.add_middleware(ForwardedHeadersMiddleware, trusted=settings.trusted_proxies)

    app.include_router(health_api.create_router(settings, database, scheduler))
    app.include_router(scan_api.create_router(scheduler))
    app.include_router(libraries_api.create_router(settings, database))
    app.include_router(titles_api.create_router(database))
    app.include_router(today_api.create_router(settings, database))
    app.include_router(progress_api.create_router(database))
    app.include_router(files_api.create_router(settings, database))
    app.include_router(images_api.create_router(settings, database, renderer))
    app.include_router(maintenance_api.create_router(settings, database))
    # Last of the catalogue routers: `/api/issues/{issue_id}` would otherwise
    # shadow `/api/issues/{issue_id}/progress` and the image routes for a client
    # that sent a trailing slash.
    app.include_router(issues_api.create_router(database))
    app.include_router(opds_api.create_router(settings, database))
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
