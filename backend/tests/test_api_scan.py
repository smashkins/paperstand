"""``POST /api/scan``, ``GET /api/scan/status`` and the scheduler behind them."""

from __future__ import annotations

import inspect
import threading
import time
from collections.abc import Callable, Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from paperstand.config import Settings
from paperstand.db import open_database
from paperstand.scanner.scheduler import ScanInProgress, ScanScheduler
from paperstand.scanner.walker import LibraryFile, Walk
from tests.conftest import SampleLibrary, quiet_settings, write_sample_config

WAIT = 10.0


def wait_until(predicate: Callable[[], bool], timeout: float = WAIT) -> bool:
    """Poll ``predicate`` until it is true or the timeout runs out."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.01)
    return False


@pytest.fixture
def gate(monkeypatch: pytest.MonkeyPatch) -> Iterator[threading.Event]:
    """Hold the walk of a scan open until the test lets it through."""
    opened = threading.Event()

    class SlowWalk(Walk):
        def __iter__(self) -> Iterator[LibraryFile]:
            opened.wait(timeout=WAIT)
            yield from super().__iter__()

    monkeypatch.setattr("paperstand.scanner.scanner.Walk", SlowWalk)
    try:
        yield opened
    finally:
        opened.set()


def test_a_scan_is_accepted_and_reported(sample_client: TestClient) -> None:
    response = sample_client.post("/api/scan")

    assert response.status_code == 202
    scan_id = response.json()["scan_id"]
    assert isinstance(scan_id, int)
    assert wait_until(lambda: not sample_client.get("/api/scan/status").json()["running"])

    status = sample_client.get("/api/scan/status").json()
    assert status["running"] is False
    assert status["current"] is None
    assert status["last"]["scan_id"] == scan_id
    assert status["last"]["status"] == "ok"
    assert status["last"]["added"] > 0


def test_a_second_scan_while_one_runs_is_a_conflict(
    sample_client: TestClient, gate: threading.Event
) -> None:
    first = sample_client.post("/api/scan")
    assert first.status_code == 202
    scan_id = first.json()["scan_id"]

    second = sample_client.post("/api/scan")

    assert second.status_code == 409
    assert second.json() == {"detail": "scan running", "scan_id": scan_id}
    gate.set()
    assert wait_until(lambda: not sample_client.get("/api/scan/status").json()["running"])


def test_status_reports_the_running_scan_s_progress(
    sample_client: TestClient, gate: threading.Event
) -> None:
    """``current`` is a live snapshot, never a bare id, while a scan runs."""
    accepted = sample_client.post("/api/scan")
    scan_id = accepted.json()["scan_id"]

    status = sample_client.get("/api/scan/status").json()

    assert status["running"] is True
    current = status["current"]
    # Held at the gate before the walk has yielded a single file: the
    # catalogue phase's own first snapshot, seeded the instant the scan was
    # asked for — never `None` while `running` is `true`.
    assert current["scan_id"] == scan_id
    assert current["phase"] == "catalogue"
    assert current["files_seen"] == 0
    assert current["added"] == 0
    assert current["covers_done"] == 0
    assert current["covers_total"] is None
    assert current["elapsed"] >= 0

    gate.set()
    assert wait_until(lambda: not sample_client.get("/api/scan/status").json()["running"])

    settled = sample_client.get("/api/scan/status").json()
    assert settled["current"] is None
    assert settled["last"]["scan_id"] == scan_id


def test_health_answers_while_a_scan_is_running(
    sample_client: TestClient, gate: threading.Event
) -> None:
    sample_client.post("/api/scan")

    response = sample_client.get("/api/health")

    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "ok"
    assert payload["scanning"] is True
    gate.set()
    assert wait_until(lambda: not sample_client.get("/api/scan/status").json()["running"])


def test_status_before_any_scan(client: TestClient) -> None:
    status = client.get("/api/scan/status").json()

    assert status == {"running": False, "current": None, "last": None}


def test_the_scheduler_runs_at_most_one_scan(
    sample_settings: Settings, gate: threading.Event
) -> None:
    database = open_database(sample_settings.db_path)
    scheduler = ScanScheduler(sample_settings, database)
    try:
        first = scheduler.request_scan()
        with pytest.raises(ScanInProgress) as conflict:
            scheduler.request_scan()
        assert conflict.value.scan_id == first
        gate.set()
        scheduler.stop()
    finally:
        database.close()

    assert scheduler.last is not None
    assert scheduler.last.scan_id == first


def test_the_scheduler_scans_on_start(sample_library: SampleLibrary, data_dir: Path) -> None:
    settings = quiet_settings(sample_library.root, data_dir, scan_on_start=True)
    write_sample_config(settings.config_path)
    database = open_database(settings.db_path)
    scheduler = ScanScheduler(settings, database)
    try:
        scheduler.start()
        assert wait_until(lambda: scheduler.last is not None)
        scheduler.stop()
    finally:
        database.close()

    assert scheduler.last is not None
    assert scheduler.last.added > 0


def test_the_scheduler_stays_quiet_when_it_is_told_to(settings: Settings) -> None:
    database = open_database(settings.db_path)
    scheduler = ScanScheduler(settings, database)
    try:
        scheduler.start()
        time.sleep(0.05)
        assert scheduler.last is None
        assert scheduler.status() == {"running": False, "current": None, "last": None}
        scheduler.stop()
    finally:
        database.close()


def test_repeated_scans_do_not_leak_connections(sample_settings: Settings) -> None:
    """A scan thread opens a connection of its own; it must hand it back."""
    database = open_database(sample_settings.db_path)
    scheduler = ScanScheduler(sample_settings, database)
    before = database.open_connections
    try:
        for _ in range(4):
            scheduler.request_scan()
            scheduler.stop()
        assert database.open_connections == before
    finally:
        database.close()

    assert database.open_connections == 0


def test_stop_waits_for_a_running_scan(sample_settings: Settings) -> None:
    """The lifespan closes the database as soon as `stop()` returns."""
    release = threading.Event()

    class SlowWalk(Walk):
        def __iter__(self) -> Iterator[LibraryFile]:
            release.wait(timeout=WAIT)
            yield from super().__iter__()

    database = open_database(sample_settings.db_path)
    scheduler = ScanScheduler(sample_settings, database)
    with pytest.MonkeyPatch.context() as patch:
        patch.setattr("paperstand.scanner.scanner.Walk", SlowWalk)
        scheduler.request_scan()
        threading.Timer(0.3, release.set).start()
        started = time.monotonic()
        scheduler.stop()
        waited = time.monotonic() - started

    assert waited >= 0.3
    assert scheduler.running is False
    assert scheduler.last is not None
    assert scheduler.last.status == "ok"
    assert scheduler.last.added > 0
    database.close()
    assert database.open_connections == 0


def test_stop_waits_for_as_long_as_the_scan_takes() -> None:
    """No arbitrary deadline: an unbounded join is the default."""
    assert inspect.signature(ScanScheduler.stop).parameters["timeout"].default is None
