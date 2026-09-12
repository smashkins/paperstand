"""Keeping the catalogue fresh, in the background.

The scheduler owns the single rule that makes the rest of the scanner safe: at
most one scan runs at a time. Everything asking for one — the periodic timer,
``POST /api/scan``, the start-up scan, the scan trigger file — goes through
:meth:`ScanScheduler.request_scan` or the background loop's own
:meth:`ScanScheduler._try_scan`, which either starts a scan on a thread of its
own or, for the loop, runs it and waits.

Nothing a request does here blocks it: the API only ever creates the ``scans``
row and hands back its id.

The background loop is a single poll, every :data:`TRIGGER_POLL_SECONDS`: it
runs the periodic scan when the interval has elapsed, and a scan on request
when ``<data>/scan.request`` exists — touched by ``paperstand organize``
after an apply run that moved a file, or by hand. It always runs, even with
automatic scanning turned off entirely, because the trigger has to work in
that deployment too.
"""

from __future__ import annotations

import datetime as dt
import threading
import time
from pathlib import Path
from typing import Any

from paperstand.cache import ensure_layout
from paperstand.config import Settings
from paperstand.db import Database
from paperstand.logging import get_logger
from paperstand.render.pages import evict as evict_pages
from paperstand.scanner.scanner import Scanner, ScanProgress, ScanResult

log = get_logger(__name__)

#: How often the background loop checks the scan trigger file. An instance
#: attribute (``ScanScheduler.poll``) rather than only this constant, so a
#: test can set it low without waiting out the real interval.
TRIGGER_POLL_SECONDS = 5.0


class ScanInProgress(RuntimeError):
    """A scan was asked for while one was already running."""

    def __init__(self, scan_id: int | None) -> None:
        super().__init__("scan running")
        self.scan_id = scan_id


class ScanScheduler:
    """Runs scans: once at start-up, then every ``scan_interval`` seconds."""

    def __init__(self, settings: Settings, database: Database) -> None:
        self.settings = settings
        self.scanner = Scanner(settings, database, on_progress=self._on_progress)
        self.poll = TRIGGER_POLL_SECONDS
        self._busy = threading.Lock()
        self._stop = threading.Event()
        self._loop: threading.Thread | None = None
        self._scan_thread: threading.Thread | None = None
        self._current: int | None = None
        self._last: ScanResult | None = None
        self._progress: ScanProgress | None = None

    # ------------------------------------------------------------------ public

    @property
    def running(self) -> bool:
        """Whether a scan is running right now."""
        return self._current is not None

    @property
    def current(self) -> int | None:
        """The id of the running scan, when there is one."""
        return self._current

    @property
    def last(self) -> ScanResult | None:
        """The result of the last scan this process finished."""
        return self._last

    def start(self) -> None:
        """Start the background loop, and the start-up scan if it is enabled.

        The loop always runs, even with automatic scanning turned off
        entirely (``scan_on_start=false``, ``scan_interval=0``): it is also
        what serves the scan trigger file, and that must keep working in a
        deployment that has switched the timer off.

        ``ensure_layout`` runs here unconditionally, before the check below:
        ``Scanner.begin`` runs it too, at the top of every scan, but a
        deployment with automatic scanning turned off would otherwise not
        call it until the first requested or trigger-driven scan, and a
        version bump or an upgrade from the unversioned layout has to be
        caught at start-up regardless.
        """
        if self._loop is not None:  # pragma: no cover - start is called once
            return
        ensure_layout(self.settings.cache_path)
        if not self.settings.scan_on_start and self.settings.scan_interval <= 0:
            log.info(
                "automatic scanning is off: scan_on_start=false, scan_interval=0; "
                "POST /api/scan and %s still work",
                self.settings.scan_trigger_path,
            )
        self._stop.clear()
        self._loop = threading.Thread(target=self._sleep_and_scan, name="scan-loop", daemon=True)
        self._loop.start()

    def stop(self, timeout: float | None = None) -> None:
        """Ask the loop to stop and wait for any running scan to finish.

        A scan is never cut short: the caller — the application lifespan — closes
        the database as soon as this returns, so returning while a scan is still
        writing would leave a half-written catalogue behind. With no ``timeout``
        this waits as long as the scan takes; with one, it gives up and says so.
        """
        self._stop.set()
        for name, thread in (("scan loop", self._loop), ("scan", self._scan_thread)):
            if thread is None:
                continue
            thread.join(timeout)
            if thread.is_alive():
                log.warning(
                    "the %s thread is still running after %ss; shutting down anyway",
                    name,
                    timeout,
                )
        self._loop = None
        self._scan_thread = None

    def request_scan(self) -> int:
        """Start a scan and return its id, or raise :class:`ScanInProgress`."""
        if not self._busy.acquire(blocking=False):
            raise ScanInProgress(self._current)
        try:
            scan_id = self.scanner.begin()
        except Exception:
            self._busy.release()
            raise
        self._current = scan_id
        self._progress = self._seed_progress(scan_id)
        thread = threading.Thread(
            target=self._run, args=(scan_id,), name=f"scan-{scan_id}", daemon=True
        )
        self._scan_thread = thread
        thread.start()
        return scan_id

    def scan_now(self) -> ScanResult:
        """Run a scan on the calling thread, for the command line and tests."""
        if not self._busy.acquire(blocking=False):
            raise ScanInProgress(self._current)
        try:
            scan_id = self.scanner.begin()
        except Exception:
            self._busy.release()
            raise
        self._current = scan_id
        self._progress = self._seed_progress(scan_id)
        return self._finish(scan_id)

    def status(self) -> dict[str, Any]:
        """What ``GET /api/scan/status`` reports."""
        return {
            "running": self.running,
            "current": progress_summary(self._progress),
            "last": scan_summary(self._last),
        }

    # ----------------------------------------------------------------- private

    def _seed_progress(self, scan_id: int) -> ScanProgress:
        """A zero-counter snapshot, published the instant a scan is asked for.

        Without this, a poller landing between ``request_scan`` returning and
        the scan thread's first callback would see ``running: true`` with
        ``current: null`` — a shape the design rules out. ``started_at`` comes
        from the ``scans`` row itself, the same place ``run`` reads it from,
        so the seed and every snapshot that replaces it agree on the instant
        the elapsed timer counts from.
        """
        return ScanProgress(
            scan_id=scan_id, phase="catalogue", started_at=self.scanner.started_at(scan_id)
        )

    def _on_progress(self, progress: ScanProgress) -> None:
        """The callback handed to the scanner: keep the latest snapshot."""
        self._progress = progress

    def _run(self, scan_id: int) -> None:
        """The body of a scan thread: run it, then hand the connection back."""
        try:
            self._finish(scan_id)
        finally:
            self.scanner.database.close_thread()

    def _finish(self, scan_id: int) -> ScanResult:
        try:
            result = self.scanner.run(scan_id)
            self._last = result
            self._sweep_page_cache()
            return result
        finally:
            self._current = None
            self._progress = None
            self._busy.release()

    def _sweep_page_cache(self) -> None:
        """Bring the rendered-page cache back under its limit.

        After a scan is the natural moment: a scan is when pages are orphaned —
        a file changed, a file went away — and it is already off the request
        path, so the sweep costs nobody a slow page load. The API sweeps too,
        every so many renders, for a process that is serving and never scanning.
        """
        limit = max(0, self.settings.page_cache_max_mb) * 1_048_576
        try:
            evict_pages(self.settings.cache_path, limit)
        except OSError as error:  # pragma: no cover - an unreadable cache
            log.warning("the page cache could not be swept: %s", error)

    def _sleep_and_scan(self) -> None:
        """The background loop: an optional first scan, then a poll for the rest."""
        try:
            self._loop_forever()
        finally:
            self.scanner.database.close_thread()

    def _loop_forever(self) -> None:
        """An optional start-up scan, then a poll every ``self.poll`` seconds.

        The poll is what serves the scan trigger file, so it runs
        unconditionally — even with ``scan_interval`` at ``0`` — and wakes up
        at least that often regardless of how far away the periodic scan is.
        A requested scan resets the periodic clock: the catalogue was just
        refreshed, so the next one is a full interval away again.
        """
        interval = self.settings.scan_interval
        if self.settings.scan_on_start:
            self._try_scan("start-up")
        due = time.monotonic() + interval if interval > 0 else None
        while not self._stop.is_set():
            wait = self.poll if due is None else max(0.0, min(self.poll, due - time.monotonic()))
            if self._stop.wait(wait):
                return
            if self._trigger_present():
                requested = self._try_scan("requested", consume=self.settings.scan_trigger_path)
                if requested and due is not None:
                    due = time.monotonic() + interval
            elif due is not None and time.monotonic() >= due:
                self._try_scan("scheduled")
                due = time.monotonic() + interval

    def _trigger_present(self) -> bool:
        """Whether the scan trigger file is sitting there, asking for a scan."""
        return self.settings.scan_trigger_path.is_file()

    def _try_scan(self, reason: str, *, consume: Path | None = None) -> bool:
        """Try to start a scan for ``reason``; ``True`` only when one actually ran.

        ``consume`` — the scan trigger file, when this call is for it — is
        unlinked only once ``_busy`` is held and ``scanner.begin()`` has
        succeeded, and only just before the scan's walk starts. The order is
        the whole point: a touch that lands while a scan is running is not
        consumed, so it waits for the next wake-up and gets a scan of its
        own; a touch that lands after the unlink and before the walk reaches
        its folder still gets a second scan, which finds nothing to do and
        costs a fast phase; and a ``begin()`` that raises leaves the trigger
        in place, so a failed start is retried on the next poll instead of
        the request being dropped. Nothing is ever lost, at worst one scan
        is redundant.
        """
        if not self._busy.acquire(blocking=False):
            log.info("skipping the %s scan: one is already running", reason)
            return False
        try:
            scan_id = self.scanner.begin()
        except Exception:  # pragma: no cover - the database is unusable
            log.exception("the %s scan could not be started", reason)
            self._busy.release()
            return False
        if consume is not None:
            try:
                consume.unlink(missing_ok=True)
            except OSError as error:  # pragma: no cover - an unwritable data dir
                log.warning("could not remove the scan trigger at %s: %s", consume, error)
        self._current = scan_id
        self._progress = self._seed_progress(scan_id)
        log.info("starting the %s scan (%d)", reason, scan_id)
        self._finish(scan_id)
        return True


def scan_summary(result: ScanResult | None) -> dict[str, Any] | None:
    """A finished scan, as the API reports it."""
    if result is None:
        return None
    return {
        "scan_id": result.scan_id,
        "status": result.status,
        "files_seen": result.files_seen,
        "added": result.added,
        "updated": result.updated,
        "removed": result.removed,
        "covers_done": result.covers_done,
        "errors": result.errors,
        "missing": result.missing,
        "message": result.message,
        "duration": round(result.duration, 3),
    }


def progress_summary(progress: ScanProgress | None) -> dict[str, Any] | None:
    """A scan already in progress, as the API reports it.

    ``elapsed`` is computed here, from ``started_at``, rather than carried on
    the snapshot itself — a duration frozen at publish time would be stale by
    the time a poller two seconds later reads it.
    """
    if progress is None:
        return None
    started = dt.datetime.fromisoformat(progress.started_at)
    elapsed = (dt.datetime.now(dt.UTC) - started).total_seconds()
    return {
        "scan_id": progress.scan_id,
        "phase": progress.phase,
        "started_at": progress.started_at,
        "elapsed": round(max(0.0, elapsed), 3),
        "files_seen": progress.files_seen,
        "added": progress.added,
        "updated": progress.updated,
        "removed": progress.removed,
        "errors": progress.errors,
        "hashed": progress.hashed,
        "missing": progress.missing,
        "covers_done": progress.covers_done,
        "covers_failed": progress.covers_failed,
        "covers_total": progress.covers_total,
    }
