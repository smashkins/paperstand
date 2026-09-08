"""``GET|HEAD /api/issues/{id}/file`` — the PDF itself.

This is the endpoint pdf.js reads through, and pdf.js does not download a
document: it asks for the first few kilobytes, reads the cross-reference table
at the end and then fetches the pages the reader is actually looking at. So the
one thing this endpoint must get right is byte ranges, and everything around
them — ``Accept-Ranges``, a strong ``ETag``, ``If-Range``, ``416`` for a range
past the end.

Starlette's :class:`~starlette.responses.FileResponse` implements the range
machinery, so what is added here is the part that is Paperstand's own:

* an ``ETag`` that names the *issue*, not just the bytes, so that a client
  cannot carry a validator from one issue to another;
* ``If-None-Match`` handling, which ``FileResponse`` does not do;
* ``Content-Disposition: inline``, because a periodical is read in the browser,
  not downloaded;
* the path check — the file served is always ``<library root>/<rel_path>``,
  resolved, and refused if that lands anywhere else.

**The response is never content-encoded.** A range is a range of the file's own
bytes; a compressing middleware in front of this would make ``Content-Range``
and the offsets pdf.js computed from it disagree, and the document would fail to
load in a way that looks like a corrupt PDF. Paperstand installs no compression
middleware, and a test asserts the absence of ``Content-Encoding``.
"""

from __future__ import annotations

from typing import Any
from urllib.parse import quote

from fastapi import APIRouter, HTTPException, Request, Response
from fastapi.responses import FileResponse

from paperstand import queries
from paperstand.api.common import (
    PRIVATE,
    UNKNOWN_ISSUE,
    catalogue,
    etag_matches,
    library_file,
)
from paperstand.config import Settings
from paperstand.db import Database

PDF_MEDIA_TYPE = "application/pdf"


def issue_etag(issue_id: str, mtime_ns: int, size: int) -> str:
    """A strong validator naming the issue and the bytes it is made of."""
    return f'"{issue_id}-{mtime_ns}-{size}"'


def content_disposition(filename: str) -> str:
    """``inline``, with the original file name in the only universal encoding."""
    return f"inline; filename*=UTF-8''{quote(filename, safe='')}"


def create_router(settings: Settings, database: Database | None) -> APIRouter:
    """Build the file router."""
    router = APIRouter(prefix="/api", tags=["files"])

    outcomes: dict[int | str, dict[str, Any]] = {
        200: {"content": {PDF_MEDIA_TYPE: {}}, "description": "The whole document"},
        206: {"content": {PDF_MEDIA_TYPE: {}}, "description": "The requested range"},
        304: {"description": "The client's copy is current"},
        416: {"description": "The requested range is not satisfiable"},
    }

    # Registered twice rather than as one two-method route: an OpenAPI operation
    # id belongs to a method, and one route serving both would give `get` and
    # `head` the same one, which every generator complains about.
    @router.api_route(
        "/issues/{issue_id}/file",
        methods=["GET"],
        operation_id="issue_file",
        response_class=FileResponse,
        responses=outcomes,
    )
    @router.api_route(
        "/issues/{issue_id}/file",
        methods=["HEAD"],
        operation_id="issue_file_head",
        response_class=FileResponse,
        responses=outcomes,
    )
    def issue_file(issue_id: str, request: Request) -> Response:
        """Stream an issue's PDF, honouring range and conditional requests."""
        connection = catalogue(database)
        row = queries.issue_row(connection, issue_id)
        if row is None:
            raise HTTPException(status_code=404, detail=UNKNOWN_ISSUE)

        path = library_file(settings.library, str(row["rel_path"]))
        stat = path.stat()
        etag = issue_etag(issue_id, stat.st_mtime_ns, stat.st_size)
        headers = {
            "ETag": etag,
            "Cache-Control": PRIVATE,
            "Content-Disposition": content_disposition(str(row["filename"])),
            "Accept-Ranges": "bytes",
        }
        if etag_matches(request.headers.get("if-none-match"), etag):
            return Response(status_code=304, headers=headers)
        return FileResponse(
            path,
            media_type=PDF_MEDIA_TYPE,
            stat_result=stat,
            headers=headers,
        )

    return router
