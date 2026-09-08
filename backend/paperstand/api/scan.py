"""``POST /api/scan`` and ``GET /api/scan/status``.

Asking for a scan is cheap and returns immediately: the endpoint opens the
``scans`` row, hands back its id with ``202 Accepted`` and lets a background
thread do the work. A second request while that scan runs is a ``409``, with the
id of the scan already in flight so that the caller can follow it instead.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter
from fastapi.responses import JSONResponse

from paperstand.logging import get_logger
from paperstand.scanner.scheduler import ScanInProgress, ScanScheduler

log = get_logger(__name__)

SCAN_UNAVAILABLE = "scanning is unavailable: the database could not be opened"


def create_router(scheduler: ScanScheduler | None) -> APIRouter:
    """Build the scan router."""
    router = APIRouter(prefix="/api", tags=["scan"])

    @router.post("/scan", status_code=202)
    def start_scan() -> JSONResponse:
        """Start a scan, unless one is already running."""
        if scheduler is None:
            return JSONResponse({"detail": SCAN_UNAVAILABLE}, status_code=503)
        try:
            scan_id = scheduler.request_scan()
        except ScanInProgress as running:
            return JSONResponse(
                {"detail": "scan running", "scan_id": running.scan_id},
                status_code=409,
            )
        return JSONResponse({"scan_id": scan_id}, status_code=202)

    @router.get("/scan/status")
    def scan_status() -> dict[str, Any]:
        """Report whether a scan is running, and how the last one went."""
        if scheduler is None:
            return {"running": False, "current": None, "last": None}
        return scheduler.status()

    return router
