"""Tests for the SPA fallback mount."""

from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from paperstand.config import Settings
from paperstand.main import create_app

INDEX_HTML = "<!doctype html><title>Paperstand</title><div id='app'></div>"


def make_build_dir(tmp_path: Path) -> Path:
    build = tmp_path / "build"
    (build / "_app").mkdir(parents=True)
    (build / "index.html").write_text(INDEX_HTML, encoding="utf-8")
    (build / "_app" / "app.js").write_text("console.log('paperstand');\n", encoding="utf-8")
    return build


def make_client(tmp_path: Path, *, with_build: bool) -> TestClient:
    build = make_build_dir(tmp_path) if with_build else None
    settings = Settings(library=tmp_path, data=tmp_path, static=build)
    return TestClient(create_app(settings))


def test_root_serves_index(tmp_path: Path) -> None:
    with make_client(tmp_path, with_build=True) as client:
        response = client.get("/")

    assert response.status_code == 200
    assert "Paperstand" in response.text


def test_unknown_path_falls_back_to_index(tmp_path: Path) -> None:
    with make_client(tmp_path, with_build=True) as client:
        response = client.get("/title/la-gazzetta-del-lago")

    assert response.status_code == 200
    assert response.text == INDEX_HTML


def test_existing_asset_is_served(tmp_path: Path) -> None:
    with make_client(tmp_path, with_build=True) as client:
        response = client.get("/_app/app.js")

    assert response.status_code == 200
    assert "paperstand" in response.text


def test_unknown_api_path_returns_json_404(tmp_path: Path) -> None:
    with make_client(tmp_path, with_build=True) as client:
        response = client.get("/api/nope")

    assert response.status_code == 404
    assert response.headers["content-type"].startswith("application/json")
    assert response.json() == {"detail": "Not Found"}


def test_missing_build_dir_is_tolerated(tmp_path: Path) -> None:
    with make_client(tmp_path, with_build=False) as client:
        response = client.get("/")
        api_response = client.get("/api/nope")

    assert response.status_code == 503
    assert "not built" in response.text
    assert api_response.status_code == 404
