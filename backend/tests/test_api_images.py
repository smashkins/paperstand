"""Covers, thumbnails, rendered pages and the eviction that bounds them."""

from __future__ import annotations

import contextlib
import io
import os
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import cast

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from PIL import Image

from paperstand.api import images as images_api
from paperstand.config import Settings
from paperstand.render import pages as render_pages
from paperstand.render.locks import KeyedLock
from paperstand.render.pages import (
    DEFAULT_WIDTH,
    EVICT_MIN_AGE,
    PAGE_WIDTHS,
    PageRenderer,
    cache_size,
    evict,
    render_page,
    snap_width,
)
from paperstand.scanner.covers import cover_paths, page_cache_dir, pages_root, render_cover
from tests.conftest import quiet_settings, sample_issue_id

A_NEWSPAPER = "Newspapers/2026/03/17/Corriere_del_Ponte_17_Marzo_2026.pdf"


@pytest.fixture
def issue_id_(catalogue_settings: Settings) -> str:
    """The id every test in this file exercises, hashed from this test's own copy."""
    return sample_issue_id(catalogue_settings.library, A_NEWSPAPER)


def image_of(content: bytes) -> Image.Image:
    return Image.open(io.BytesIO(content))


# ------------------------------------------------------------------- covers


@pytest.mark.parametrize(("name", "width"), [("cover", 900), ("thumb", 300)])
def test_the_cached_images_are_served(
    catalogue_client: TestClient, issue_id_: str, name: str, width: int
) -> None:
    response = catalogue_client.get(f"/api/issues/{issue_id_}/{name}.jpg")

    assert response.status_code == 200
    assert response.headers["content-type"] == "image/jpeg"
    assert response.headers["cache-control"] == "no-cache"
    assert response.headers["etag"].startswith(f'"{issue_id_}-{name}-')
    assert response.headers["etag"].endswith('"')
    assert image_of(response.content).width == width


def test_a_versioned_url_is_immutable(catalogue_client: TestClient, issue_id_: str) -> None:
    issue = catalogue_client.get(f"/api/issues/{issue_id_}").json()

    response = catalogue_client.get(issue["cover_url"])

    assert response.status_code == 200
    assert response.headers["cache-control"] == "public, max-age=31536000, immutable"


@pytest.mark.parametrize("suffix", ["cover.jpg", "thumb.jpg", "pages/1.webp"])
def test_head_answers_an_image_with_its_headers_and_no_body(
    catalogue_client: TestClient, issue_id_: str, suffix: str
) -> None:
    """A reader probing a stored catalogue asks `HEAD` before it fetches."""
    path = f"/api/issues/{issue_id_}/{suffix}"
    get = catalogue_client.get(path)
    head = catalogue_client.head(path)

    assert head.status_code == 200
    assert head.headers["content-type"] == get.headers["content-type"]
    assert head.headers["etag"] == get.headers["etag"]
    assert head.headers["content-length"] == str(len(get.content))
    assert head.content == b""


def test_head_on_an_unknown_issue_is_a_404(catalogue_client: TestClient) -> None:
    assert catalogue_client.head("/api/issues/nope/cover.jpg").status_code == 404


def test_a_matching_validator_is_a_304(catalogue_client: TestClient, issue_id_: str) -> None:
    etag = catalogue_client.get(f"/api/issues/{issue_id_}/thumb.jpg").headers["etag"]

    response = catalogue_client.get(
        f"/api/issues/{issue_id_}/thumb.jpg", headers={"If-None-Match": etag}
    )

    assert response.status_code == 304
    assert response.content == b""


def test_a_lost_cover_is_rendered_on_demand(
    catalogue_client: TestClient, catalogue_settings: Settings, issue_id_: str
) -> None:
    cover, thumb = cover_paths(catalogue_settings.cache_path, issue_id_)
    cover.unlink()
    thumb.unlink()

    response = catalogue_client.get(f"/api/issues/{issue_id_}/cover.jpg")

    assert response.status_code == 200
    assert image_of(response.content).width == 900
    assert cover.is_file() and thumb.is_file()


def test_a_cover_that_cannot_be_rendered_is_a_503(
    catalogue_client: TestClient, catalogue_settings: Settings, issue_id_: str
) -> None:
    cover, thumb = cover_paths(catalogue_settings.cache_path, issue_id_)
    cover.unlink()
    thumb.unlink()
    (catalogue_settings.library / A_NEWSPAPER).write_bytes(b"not a pdf at all")

    response = catalogue_client.get(f"/api/issues/{issue_id_}/cover.jpg")

    assert response.status_code == 503
    assert "cover" in response.json()["detail"]


def test_an_unknown_issue_has_no_images(catalogue_client: TestClient) -> None:
    assert catalogue_client.get("/api/issues/nope/cover.jpg").status_code == 404
    assert catalogue_client.get("/api/issues/nope/thumb.jpg").status_code == 404
    assert catalogue_client.get("/api/issues/nope/pages/1.webp").status_code == 404


# -------------------------------------------------------------------- pages


def test_a_page_is_rendered_at_the_width_asked_for(
    catalogue_client: TestClient, catalogue_settings: Settings, issue_id_: str
) -> None:
    response = catalogue_client.get(f"/api/issues/{issue_id_}/pages/1.webp", params={"w": 800})

    assert response.status_code == 200
    assert response.headers["content-type"] == "image/webp"
    assert response.headers["cache-control"] == "no-cache"  # no ?v= on this URL
    assert response.headers["etag"].startswith(f'"{issue_id_}-')
    assert response.headers["etag"].endswith('-1-800"')
    image = image_of(response.content)
    assert image.format == "WEBP"
    assert image.width == 800
    assert (page_cache_dir(catalogue_settings.cache_path, issue_id_) / "1-800.webp").is_file()


def test_the_second_request_never_touches_the_renderer(
    catalogue_client: TestClient, monkeypatch: pytest.MonkeyPatch, issue_id_: str
) -> None:
    first = catalogue_client.get(f"/api/issues/{issue_id_}/pages/2.webp", params={"w": 400})
    assert first.status_code == 200

    def explode(*_: object, **__: object) -> None:
        raise AssertionError("the cached page was rendered again")

    monkeypatch.setattr(render_pages, "render_page", explode)
    second = catalogue_client.get(f"/api/issues/{issue_id_}/pages/2.webp", params={"w": 400})

    assert second.status_code == 200
    assert second.content == first.content
    # And the patch really would have fired: a width that is not cached does
    # reach the renderer, so the assertion above is about the cache, not luck.
    with pytest.raises(AssertionError, match="rendered again"):
        catalogue_client.get(f"/api/issues/{issue_id_}/pages/2.webp", params={"w": 1600})


@pytest.mark.parametrize(("asked", "served"), [(999, 1200), (5000, 2000), (1, 200), (800, 800)])
def test_a_width_is_snapped_to_the_ladder(
    catalogue_client: TestClient, issue_id_: str, asked: int, served: int
) -> None:
    response = catalogue_client.get(f"/api/issues/{issue_id_}/pages/1.webp", params={"w": asked})

    assert response.status_code == 200
    assert image_of(response.content).width == served
    assert response.headers["etag"].endswith(f'-1-{served}"')


def test_no_width_means_the_default(catalogue_client: TestClient, issue_id_: str) -> None:
    response = catalogue_client.get(f"/api/issues/{issue_id_}/pages/1.webp")

    assert image_of(response.content).width == DEFAULT_WIDTH


@pytest.mark.parametrize("width", [0, -1, 10**9])
def test_a_nonsense_width_is_refused(
    catalogue_client: TestClient, issue_id_: str, width: int
) -> None:
    response = catalogue_client.get(f"/api/issues/{issue_id_}/pages/1.webp", params={"w": width})

    assert response.status_code == 422


def test_a_page_outside_the_document_is_a_404(catalogue_client: TestClient, issue_id_: str) -> None:
    page_count = catalogue_client.get(f"/api/issues/{issue_id_}").json()["page_count"]

    assert catalogue_client.get(f"/api/issues/{issue_id_}/pages/0.webp").status_code == 404
    assert (
        catalogue_client.get(f"/api/issues/{issue_id_}/pages/{page_count + 1}.webp").status_code
        == 404
    )
    assert (
        catalogue_client.get(f"/api/issues/{issue_id_}/pages/{page_count}.webp").status_code == 200
    )


def test_a_page_of_an_unreadable_document_is_a_503(
    catalogue_client: TestClient, catalogue_settings: Settings, issue_id_: str
) -> None:
    (catalogue_settings.library / A_NEWSPAPER).write_bytes(b"not a pdf at all")

    response = catalogue_client.get(f"/api/issues/{issue_id_}/pages/1.webp")

    assert response.status_code == 503


def test_the_page_count_is_checked_against_the_document_too(
    catalogue_settings: Settings, tmp_path: Path
) -> None:
    """A row with no page count still cannot render a page that is not there."""
    pdf = catalogue_settings.library / A_NEWSPAPER

    with pytest.raises(render_pages.PageOutOfRange):
        render_page(pdf, 999, 400, tmp_path / "out.webp")


@pytest.mark.parametrize(
    ("asked", "expected"),
    [(None, DEFAULT_WIDTH), (1, 200), (200, 200), (201, 400), (999, 1200), (2001, 2000)],
)
def test_snap_width(asked: int | None, expected: int) -> None:
    assert snap_width(asked) == expected
    assert expected in PAGE_WIDTHS


# ----------------------------------------------------------------- eviction


def stuff_cache(cache_root: Path, count: int, size: int) -> list[Path]:
    """Write ``count`` fake pages, oldest use first and all old enough to go."""
    written = []
    now = time.time()
    for index in range(count):
        path = page_cache_dir(cache_root, f"issue{index:02d}") / "1-800.webp"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"\0" * size)
        used = now - (count - index) * 60 - EVICT_MIN_AGE
        os.utime(path, (used, used))
        written.append(path)
    return written


def test_eviction_keeps_the_cache_under_its_limit(tmp_path: Path) -> None:
    cache = tmp_path / "cache"
    written = stuff_cache(cache, count=10, size=300_000)  # ~2.9 MiB
    limit = 1_048_576

    assert cache_size(cache) > limit
    result = evict(cache, limit)

    assert result.after <= limit
    assert cache_size(cache) <= limit
    assert result.removed == 7
    assert result.freed > 0
    # The least recently used went first; the most recent survived.
    assert not written[0].exists()
    assert written[-1].exists()
    # And the directories they left behind are gone too.
    assert not written[0].parent.exists()


def test_a_cache_already_within_its_limit_is_left_alone(tmp_path: Path) -> None:
    cache = tmp_path / "cache"
    stuff_cache(cache, count=2, size=1000)

    result = evict(cache, 1_048_576)

    assert result.removed == 0
    assert result.before == result.after == cache_size(cache)


def test_a_limit_of_zero_means_no_limit(tmp_path: Path) -> None:
    cache = tmp_path / "cache"
    stuff_cache(cache, count=3, size=1000)

    assert evict(cache, 0).removed == 0
    assert cache_size(cache) == 3000


def test_an_empty_cache_costs_nothing(tmp_path: Path) -> None:
    assert cache_size(tmp_path / "cache") == 0
    assert evict(tmp_path / "cache", 10).removed == 0


def test_a_cache_hit_records_the_access(
    catalogue_client: TestClient, catalogue_settings: Settings, issue_id_: str
) -> None:
    catalogue_client.get(f"/api/issues/{issue_id_}/pages/1.webp", params={"w": 200})
    cached = page_cache_dir(catalogue_settings.cache_path, issue_id_) / "1-200.webp"
    os.utime(cached, (0, 0))

    catalogue_client.get(f"/api/issues/{issue_id_}/pages/1.webp", params={"w": 200})

    assert cached.stat().st_atime > 0


def test_the_renderer_sweeps_by_itself_every_so_many_renders(
    settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    renderer = PageRenderer(quiet_settings(settings.library, settings.data, page_cache_max_mb=1))
    swept: list[int] = []
    monkeypatch.setattr(renderer, "evict", lambda **_: swept.append(1))
    monkeypatch.setattr(render_pages, "EVICT_EVERY", 3)

    for _ in range(7):
        renderer._count_render()  # the counter has no public trigger

    assert len(swept) == 2


def test_a_scan_sweeps_the_page_cache(settings: Settings) -> None:
    """The scheduler's hook: a scan is when orphaned pages appear."""
    from paperstand.db import open_database
    from paperstand.scanner.scheduler import ScanScheduler

    tight = quiet_settings(settings.library, settings.data, page_cache_max_mb=1)
    stuff_cache(tight.cache_path, count=10, size=300_000)
    database = open_database(tight.db_path)
    try:
        ScanScheduler(tight, database).scan_now()
    finally:
        database.close()

    assert cache_size(tight.cache_path) <= 1_048_576
    assert pages_root(tight.cache_path).is_dir()


# ------------------------------------------------- versioned page addresses


def test_the_page_template_carries_the_version(
    catalogue_client: TestClient, issue_id_: str
) -> None:
    """A URL is only safe to keep for a year if it names what it points at."""
    issue = catalogue_client.get(f"/api/issues/{issue_id_}").json()
    version = issue["cover_url"].split("v=")[1]

    template = issue["pages_url_template"]

    assert template == f"/api/issues/{issue_id_}/pages/{{n}}.webp?w={{w}}&v={version}"
    filled = template.replace("{n}", "1").replace("{w}", "800")
    response = catalogue_client.get(filled)
    assert response.status_code == 200
    assert response.headers["cache-control"] == "public, max-age=31536000, immutable"
    assert version in response.headers["etag"]


def test_a_stale_version_is_served_but_never_immutable(
    catalogue_client: TestClient, issue_id_: str
) -> None:
    """A replaced PDF keeps its id and its URLs; the version is what moves."""
    current = catalogue_client.get(
        f"/api/issues/{issue_id_}/pages/1.webp",
        params={"w": 400, "v": _version(catalogue_client, issue_id_)},
    )
    stale = catalogue_client.get(
        f"/api/issues/{issue_id_}/pages/1.webp", params={"w": 400, "v": "1"}
    )
    missing = catalogue_client.get(f"/api/issues/{issue_id_}/pages/1.webp", params={"w": 400})

    assert current.headers["cache-control"] == "public, max-age=31536000, immutable"
    assert stale.headers["cache-control"] == "no-cache"
    assert missing.headers["cache-control"] == "no-cache"
    assert stale.status_code == missing.status_code == 200
    assert stale.content == current.content


def test_a_covers_version_is_checked_too(catalogue_client: TestClient, issue_id_: str) -> None:
    matching = catalogue_client.get(
        f"/api/issues/{issue_id_}/cover.jpg", params={"v": _version(catalogue_client, issue_id_)}
    )
    stale = catalogue_client.get(f"/api/issues/{issue_id_}/cover.jpg", params={"v": "1"})

    assert matching.headers["cache-control"] == "public, max-age=31536000, immutable"
    assert stale.headers["cache-control"] == "no-cache"


def test_the_page_validator_moves_with_the_document(
    catalogue_client: TestClient, issue_id_: str
) -> None:
    etag = catalogue_client.get(f"/api/issues/{issue_id_}/pages/1.webp", params={"w": 400}).headers[
        "etag"
    ]

    assert _version(catalogue_client, issue_id_) in etag


def _version(client: TestClient, issue_id_: str) -> str:
    issue = client.get(f"/api/issues/{issue_id_}").json()
    version: str = issue["cover_url"].split("v=")[1]
    return version


# ---------------------------------------------- one render, however many ask


def test_two_requests_for_a_cold_cover_render_it_once(
    catalogue_client: TestClient,
    catalogue_settings: Settings,
    monkeypatch: pytest.MonkeyPatch,
    issue_id_: str,
) -> None:
    """The cover and the thumbnail come from one rasterisation; so must these."""
    cover, thumb = cover_paths(catalogue_settings.cache_path, issue_id_)
    cover.unlink()
    thumb.unlink()

    calls: list[str] = []
    real = render_cover

    def slow(*args: object, **kwargs: object) -> object:
        calls.append("render")
        time.sleep(0.2)  # long enough for the other request to arrive and wait
        return real(*args, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(images_api, "render_cover", slow)

    with ThreadPoolExecutor(max_workers=2) as pool:
        responses = list(
            pool.map(
                catalogue_client.get,
                [f"/api/issues/{issue_id_}/cover.jpg", f"/api/issues/{issue_id_}/thumb.jpg"],
            )
        )

    assert [response.status_code for response in responses] == [200, 200]
    assert len(calls) == 1
    assert image_of(responses[0].content).width == 900
    assert image_of(responses[1].content).width == 300


def test_a_cover_is_written_atomically(catalogue_settings: Settings, issue_id_: str) -> None:
    """The path holds a whole image or the previous one, never half of one."""
    cover, _ = cover_paths(catalogue_settings.cache_path, issue_id_)
    before = cover.read_bytes()
    seen: list[bytes] = []
    stop = threading.Event()

    def watch() -> None:
        while not stop.is_set():
            with contextlib.suppress(OSError):  # pragma: no cover - being replaced
                seen.append(cover.read_bytes())

    watcher = threading.Thread(target=watch, daemon=True)
    watcher.start()
    try:
        for _ in range(5):
            render_cover(
                catalogue_settings.library / A_NEWSPAPER, issue_id_, catalogue_settings.cache_path
            )
    finally:
        stop.set()
        watcher.join(timeout=5)

    after = cover.read_bytes()
    assert seen, "the watcher never managed to read the file"
    # Every snapshot is one of the complete images, never a prefix of one.
    assert set(seen) <= {before, after}


def test_the_keyed_lock_serialises_one_key_and_not_two() -> None:
    locks = KeyedLock()
    order: list[str] = []
    started = threading.Event()

    def hold(key: str, label: str) -> None:
        with locks(key):
            order.append(f"{label}-in")
            started.set()
            time.sleep(0.1)
            order.append(f"{label}-out")

    same = [threading.Thread(target=hold, args=("a", str(index))) for index in range(2)]
    for thread in same:
        thread.start()
    for thread in same:
        thread.join()

    assert order in (
        ["0-in", "0-out", "1-in", "1-out"],
        ["1-in", "1-out", "0-in", "0-out"],
    )
    assert locks.held == 0  # nothing is left behind once a key goes idle


# ------------------------------------------- eviction never eats its own work


def test_a_sweep_never_takes_the_page_it_was_triggered_by(
    catalogue_client: TestClient, monkeypatch: pytest.MonkeyPatch, issue_id_: str
) -> None:
    """A limit smaller than one page must not make every render a 500."""
    application = cast(FastAPI, catalogue_client.app)
    renderer: PageRenderer = application.state.renderer
    monkeypatch.setattr(render_pages, "EVICT_EVERY", 1)
    monkeypatch.setattr(renderer, "max_bytes", 1)

    for page in (1, 2, 3):
        response = catalogue_client.get(
            f"/api/issues/{issue_id_}/pages/{page}.webp", params={"w": 800}
        )
        assert response.status_code == 200, response.text
        assert image_of(response.content).width == 800


def test_eviction_keeps_a_named_file_and_the_young_ones(tmp_path: Path) -> None:
    cache = tmp_path / "cache"
    old = stuff_cache(cache, count=6, size=300_000)
    fresh = page_cache_dir(cache, "brandnew") / "1-800.webp"
    fresh.parent.mkdir(parents=True, exist_ok=True)
    fresh.write_bytes(b"\0" * 300_000)  # written now, so it counts as in use

    result = evict(cache, 1, keep=[old[-1]])

    assert fresh.exists(), "a page used seconds ago is on its way to a response"
    assert old[-1].exists(), "a page named in `keep` is never taken"
    assert result.removed == 5


def test_the_limit_is_floored_at_the_largest_file(tmp_path: Path) -> None:
    """ "Make it fit" can never mean "empty the cache"."""
    cache = tmp_path / "cache"
    stuff_cache(cache, count=3, size=300_000)

    result = evict(cache, 1)

    assert result.after == 300_000
    assert result.removed == 2
