"""Covers, thumbnails and rendered pages.

``GET /api/issues/{id}/cover.jpg``, ``.../thumb.jpg`` and
``.../pages/{n}.webp`` all answer the same way: look in the cache, and render
into it when the answer is not there.

The two kinds of image are cached differently on purpose.

**Covers** are produced by the scanner for every issue it catalogues, so this
endpoint is a safety net: a cache wiped by hand, a ``/data`` restored without
it, an issue whose slow phase has not run yet. Their URLs carry ``?v=`` — the
modification time of the PDF the image came from — which makes them immutable
for as long as the file does not change, and revalidated when a caller drops the
parameter.

**Pages** are never produced ahead of time; there are too many. They are
rendered on demand, at one of a handful of widths, and carry the same ``?v=``.
Deleting the server's copies — which the scanner does whenever a file changes —
says nothing to a browser or a proxy that cached the image for a year, so the
version has to be in the URL for ``immutable`` to be honest.

Which is why ``immutable`` is only ever answered to a ``v`` that *matches* the
issue's current modification time. A stale ``v`` still gets its image, but with
``no-cache``, so the client comes back and finds out the URL has moved on.
"""

from __future__ import annotations

from pathlib import Path
from typing import Annotated, Any

from fastapi import APIRouter, HTTPException, Query, Request, Response
from fastapi.responses import FileResponse

from paperstand import queries
from paperstand.api.common import (
    IMMUTABLE,
    REVALIDATE,
    UNKNOWN_ISSUE,
    catalogue,
    etag_matches,
    library_file,
)
from paperstand.config import Settings
from paperstand.db import Database
from paperstand.logging import get_logger
from paperstand.render.pages import (
    MAX_WIDTH_QUERY,
    PageOutOfRange,
    PageRenderer,
    RenderError,
    snap_width,
)
from paperstand.scanner.covers import CoverError, cover_paths, render_cover

log = get_logger(__name__)

JPEG = "image/jpeg"
WEBP = "image/webp"

COVER_FAILED = "the cover of this issue could not be rendered"
PAGE_FAILED = "this page could not be rendered"
NO_SUCH_PAGE = "the document has no such page"


def caching(version: str | None, mtime_ns: int) -> str:
    """``immutable`` for the URL that names this version of the file, else not.

    An id names one set of bytes for good — a PDF replaced with different
    content is a new issue with a new id, never this one — but the URL is
    still versioned by the file's modification time, which moves on a touch
    even when the bytes, and so the id, do not. A ``v`` that is no longer the
    file's current modification time is a client holding an address that may
    have moved on, and it is told to check back rather than trust a stale
    cache blindly.
    """
    return IMMUTABLE if version == str(mtime_ns) else REVALIDATE


def image_response(
    request: Request,
    path: Path,
    media_type: str,
    etag: str,
    cache_control: str,
) -> Response:
    """Serve a cached image, or a ``304`` when the client already has it."""
    headers = {"ETag": etag, "Cache-Control": cache_control}
    if etag_matches(request.headers.get("if-none-match"), etag):
        return Response(status_code=304, headers=headers)
    return FileResponse(path, media_type=media_type, headers=headers)


def create_router(
    settings: Settings, database: Database | None, renderer: PageRenderer
) -> APIRouter:
    """Build the image router."""
    router = APIRouter(prefix="/api", tags=["images"])
    cache_root = settings.cache_path

    def cover_image(
        request: Request, issue_id: str, version: str | None, thumbnail: bool
    ) -> Response:
        connection = catalogue(database)
        row = queries.issue_row(connection, issue_id)
        if row is None:
            raise HTTPException(status_code=404, detail=UNKNOWN_ISSUE)
        cover, thumb = cover_paths(cache_root, issue_id)
        wanted = thumb if thumbnail else cover
        if not wanted.is_file():
            pdf = library_file(settings.library, str(row["rel_path"]))
            # One render per issue, not per image: `render_cover` writes *both*
            # the cover and the thumbnail from a single rasterisation, so a
            # request for each of them arriving together would otherwise render
            # the document twice and write the same two paths at once. The
            # second one through waits here and then finds its file done.
            with renderer.locks(issue_id):
                if not wanted.is_file():
                    with renderer.semaphore:
                        outcome = render_cover(pdf, issue_id, cache_root)
                    if isinstance(outcome, CoverError):
                        log.warning("cannot render the cover of %s: %s", issue_id, outcome.message)
                        raise HTTPException(status_code=503, detail=COVER_FAILED)
        stat = wanted.stat()
        kind = "thumb" if thumbnail else "cover"
        mtime_ns = int(row["mtime_ns"])
        etag = f'"{issue_id}-{kind}-{mtime_ns}-{stat.st_mtime_ns}"'
        return image_response(request, wanted, JPEG, etag, caching(version, mtime_ns))

    cover_outcomes: dict[int | str, dict[str, Any]] = {
        200: {"content": {JPEG: {}}, "description": "The image"},
        304: {"description": "The client's copy is current"},
    }

    # Each image route is registered twice, once per method, for the reason
    # `files.py` gives: one two-method route would give `get` and `head` the same
    # OpenAPI operation id. `HEAD` is answered because a reader that stores a
    # catalogue probes a cover before fetching it, and because it costs nothing
    # here — Starlette's `FileResponse` drops the body itself. It does still
    # render a missing image, which is the point: the headers a `HEAD` asks about
    # are the finished image's.
    @router.api_route(
        "/issues/{issue_id}/cover.jpg",
        methods=["GET"],
        operation_id="issue_cover",
        response_class=FileResponse,
        responses=cover_outcomes,
    )
    @router.api_route(
        "/issues/{issue_id}/cover.jpg",
        methods=["HEAD"],
        operation_id="issue_cover_head",
        response_class=FileResponse,
        responses=cover_outcomes,
    )
    def cover(
        request: Request,
        issue_id: str,
        v: Annotated[str | None, Query(description="Cache buster: the PDF's mtime")] = None,
    ) -> Response:
        """The issue's cover, rendered on demand when the cache has lost it."""
        return cover_image(request, issue_id, v, thumbnail=False)

    @router.api_route(
        "/issues/{issue_id}/thumb.jpg",
        methods=["GET"],
        operation_id="issue_thumb",
        response_class=FileResponse,
        responses=cover_outcomes,
    )
    @router.api_route(
        "/issues/{issue_id}/thumb.jpg",
        methods=["HEAD"],
        operation_id="issue_thumb_head",
        response_class=FileResponse,
        responses=cover_outcomes,
    )
    def thumbnail(
        request: Request,
        issue_id: str,
        v: Annotated[str | None, Query(description="Cache buster: the PDF's mtime")] = None,
    ) -> Response:
        """The issue's thumbnail, rendered on demand when the cache has lost it."""
        return cover_image(request, issue_id, v, thumbnail=True)

    page_outcomes: dict[int | str, dict[str, Any]] = {
        200: {"content": {WEBP: {}}, "description": "The rendered page"},
        304: {"description": "The client's copy is current"},
        404: {"description": "No such issue, or no such page"},
    }

    @router.api_route(
        "/issues/{issue_id}/pages/{page}.webp",
        methods=["GET"],
        operation_id="issue_page",
        response_class=FileResponse,
        responses=page_outcomes,
    )
    @router.api_route(
        "/issues/{issue_id}/pages/{page}.webp",
        methods=["HEAD"],
        operation_id="issue_page_head",
        response_class=FileResponse,
        responses=page_outcomes,
    )
    async def page_image(
        request: Request,
        issue_id: str,
        page: int,
        w: Annotated[
            int | None,
            Query(ge=1, le=MAX_WIDTH_QUERY, description="Snapped to the next width up"),
        ] = None,
        v: Annotated[str | None, Query(description="Cache buster: the PDF's mtime")] = None,
    ) -> Response:
        """One page of an issue as a WebP image, rendered on demand."""
        connection = catalogue(database)
        row = queries.issue_row(connection, issue_id)
        if row is None:
            raise HTTPException(status_code=404, detail=UNKNOWN_ISSUE)
        known = row["page_count"]
        if page < 1 or (known is not None and page > int(known)):
            raise HTTPException(status_code=404, detail=NO_SUCH_PAGE)

        width = snap_width(w)
        pdf = library_file(settings.library, str(row["rel_path"]))
        try:
            image = await renderer.page(pdf, issue_id, page, width)
        except PageOutOfRange as error:
            raise HTTPException(status_code=404, detail=NO_SUCH_PAGE) from error
        except RenderError as error:
            log.warning("cannot render page %d of %s: %s", page, issue_id, error)
            raise HTTPException(status_code=503, detail=PAGE_FAILED) from error
        mtime_ns = int(row["mtime_ns"])
        etag = f'"{issue_id}-{mtime_ns}-{page}-{width}"'
        return image_response(request, image, WEBP, etag, caching(v, mtime_ns))

    return router
