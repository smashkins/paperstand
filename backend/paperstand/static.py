"""Static file serving with a single-page-application fallback.

The frontend is built as a static SPA, so any path that is neither an API route
nor an existing file has to be answered with ``index.html`` and let the client
side router take over.
"""

from __future__ import annotations

from pathlib import Path

from starlette.exceptions import HTTPException
from starlette.responses import FileResponse, PlainTextResponse, Response
from starlette.staticfiles import StaticFiles
from starlette.types import Scope

#: Path prefixes that must never fall back to ``index.html``.
API_PREFIXES: tuple[str, ...] = ("/api", "/opds", "/openapi.json", "/docs", "/redoc")

MISSING_BUILD_MESSAGE = (
    "Paperstand frontend not built. Run `make dev-web` for the development "
    "server, or `cd frontend && npm run build` to build the SPA."
)


def is_api_path(path: str) -> bool:
    """Return ``True`` for paths that belong to the API rather than to the SPA."""
    return any(path == prefix or path.startswith(prefix + "/") for prefix in API_PREFIXES)


class SPAStaticFiles(StaticFiles):
    """Serve a built SPA, falling back to ``index.html`` for unknown routes.

    A missing build directory is tolerated: requests then get a plain text
    explanation instead of a crash, which keeps the API usable while the
    frontend has not been built yet.
    """

    def __init__(self, directory: Path | str | None) -> None:
        self.build_dir = Path(directory) if directory is not None else None
        self.index_file = self.build_dir / "index.html" if self.build_dir else None
        self.available = self.index_file is not None and self.index_file.is_file()
        super().__init__(
            directory=self.build_dir if self.available else None,
            html=True,
            check_dir=False,
        )

    async def get_response(self, path: str, scope: Scope) -> Response:
        request_path = str(scope.get("path", "/"))
        if is_api_path(request_path):
            raise HTTPException(status_code=404, detail="Not Found")
        if not self.available:
            return PlainTextResponse(MISSING_BUILD_MESSAGE, status_code=503)
        try:
            return await super().get_response(path, scope)
        except HTTPException as exc:
            if exc.status_code != 404:
                raise
            assert self.index_file is not None
            return FileResponse(self.index_file)
