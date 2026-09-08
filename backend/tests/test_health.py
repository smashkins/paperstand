"""Tests for ``GET /api/health``."""

from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from paperstand import __version__
from paperstand.config import Settings
from paperstand.main import create_app
from paperstand.scanner.scanner import scan_once
from tests.conftest import SampleLibrary, quiet_settings


def test_health_returns_expected_shape(client: TestClient, settings: Settings) -> None:
    response = client.get("/api/health")

    assert response.status_code == 200
    assert response.json() == {
        "status": "ok",
        "version": __version__,
        "library_path": str(settings.library),
        "library_ok": True,
        "db_ok": True,
        "last_scan": None,
        "scanning": False,
        "issue_count": 0,
    }


def test_health_reports_a_missing_library_and_an_unusable_data_directory() -> None:
    """Neither a missing mount nor an unwritable ``/data`` may stop the service."""
    missing = Path("/paperstand/does/not/exist")
    settings = quiet_settings(missing, missing)

    with TestClient(create_app(settings)) as client:
        response = client.get("/api/health")

    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "ok"
    assert payload["library_ok"] is False
    assert payload["db_ok"] is False
    assert payload["issue_count"] == 0


def test_health_reports_the_catalogue_and_the_last_scan(
    sample_settings: Settings, sample_library: SampleLibrary
) -> None:
    result = scan_once(sample_settings)

    with TestClient(create_app(sample_settings)) as client:
        payload = client.get("/api/health").json()

    assert payload["db_ok"] is True
    assert payload["issue_count"] == sample_library.catalogued_files
    assert payload["last_scan"]["id"] == result.scan_id
    assert payload["last_scan"]["status"] == "ok"
    assert payload["last_scan"]["files_seen"] == sample_library.catalogued_files
    assert payload["scanning"] is False
