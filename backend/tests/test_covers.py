"""Cover and thumbnail rendering, and the cache they live in."""

from __future__ import annotations

from pathlib import Path

import pytest
from PIL import Image

import paperstand.cache as cache_module
from paperstand.cache import covers_root, ensure_layout, pages_root
from paperstand.scanner.covers import (
    COVER_WIDTH,
    JPEG_QUALITY,
    TEXT_LIMIT,
    THUMB_WIDTH,
    CoverError,
    CoverResult,
    clear_cache,
    cover_paths,
    has_cover,
    move_cache,
    page_cache_dir,
    render_cover,
)
from tests.conftest import SampleLibrary

A_NEWSPAPER = "Newspapers/2026/03/17/Corriere_del_Ponte_17_Marzo_2026.pdf"

#: Every test in this module calls `render_cover` itself: the rasteriser is
#: what is under test, so the fast stub in `conftest.py` never belongs here.
pytestmark = pytest.mark.real_covers


def test_rendering_produces_two_jpegs_of_the_expected_widths(
    sample_library: SampleLibrary, tmp_path: Path
) -> None:
    cache = tmp_path / "cache"
    outcome = render_cover(sample_library.path(A_NEWSPAPER), "abcdef0123456789", cache)

    assert isinstance(outcome, CoverResult)
    cover, thumbnail = cover_paths(cache, "abcdef0123456789")
    assert cover.parent.name == "ab"
    for path, width in ((cover, COVER_WIDTH), (thumbnail, THUMB_WIDTH)):
        with Image.open(path) as image:
            assert image.format == "JPEG"
            assert image.width == width
            assert image.height > 0
    assert has_cover(cache, "abcdef0123456789")


def test_rendering_reports_the_page_count_size_and_text(
    sample_library: SampleLibrary, tmp_path: Path
) -> None:
    outcome = render_cover(sample_library.path(A_NEWSPAPER), "0011223344556677", tmp_path)

    assert isinstance(outcome, CoverResult)
    assert 4 <= outcome.page_count <= 8
    assert (round(outcome.page_w), round(outcome.page_h)) == (1000, 1400)
    assert "Corriere del Ponte" in outcome.first_page_text
    assert len(outcome.first_page_text) <= TEXT_LIMIT


def test_an_unreadable_file_is_an_error_not_an_exception(tmp_path: Path) -> None:
    broken = tmp_path / "broken.pdf"
    broken.write_bytes(b"this is not a PDF at all\n" * 10)

    outcome = render_cover(broken, "deadbeefdeadbeef", tmp_path / "cache")

    assert isinstance(outcome, CoverError)
    assert outcome.message
    assert not has_cover(tmp_path / "cache", "deadbeefdeadbeef")


def test_a_missing_file_is_an_error_not_an_exception(tmp_path: Path) -> None:
    outcome = render_cover(tmp_path / "absent.pdf", "0000000000000000", tmp_path / "cache")

    assert isinstance(outcome, CoverError)


def test_clearing_the_cache_removes_the_images_and_the_pages(
    sample_library: SampleLibrary, tmp_path: Path
) -> None:
    cache = tmp_path / "cache"
    render_cover(sample_library.path(A_NEWSPAPER), "ffeeddccbbaa9988", cache)
    pages = page_cache_dir(cache, "ffeeddccbbaa9988")
    pages.mkdir(parents=True)
    (pages / "1-900.webp").write_bytes(b"x")

    clear_cache(cache, "ffeeddccbbaa9988")

    assert not has_cover(cache, "ffeeddccbbaa9988")
    assert not pages.exists()
    # Clearing what is not there is not an error.
    clear_cache(cache, "ffeeddccbbaa9988")


def test_the_quality_is_the_documented_one() -> None:
    assert JPEG_QUALITY == 85


# ------------------------------------------------------------------ move_cache


def test_move_cache_moves_the_images_and_the_pages(
    sample_library: SampleLibrary, tmp_path: Path
) -> None:
    cache = tmp_path / "cache"
    render_cover(sample_library.path(A_NEWSPAPER), "1111111111111111", cache)
    pages = page_cache_dir(cache, "1111111111111111")
    pages.mkdir(parents=True)
    (pages / "1-900.webp").write_bytes(b"page one")
    cover, thumbnail = cover_paths(cache, "1111111111111111")
    cover_bytes = cover.read_bytes()
    thumbnail_bytes = thumbnail.read_bytes()

    move_cache(cache, "1111111111111111", "2222222222222222")

    assert not has_cover(cache, "1111111111111111")
    assert not pages.exists()
    assert has_cover(cache, "2222222222222222")
    new_cover, new_thumbnail = cover_paths(cache, "2222222222222222")
    assert new_cover.read_bytes() == cover_bytes
    assert new_thumbnail.read_bytes() == thumbnail_bytes
    new_pages = page_cache_dir(cache, "2222222222222222")
    assert (new_pages / "1-900.webp").read_bytes() == b"page one"


def test_move_cache_tolerates_a_missing_source(tmp_path: Path) -> None:
    cache = tmp_path / "cache"
    # Nothing was ever rendered for this id: moving it is not an error.
    move_cache(cache, "0000000000000000", "1111111111111111")

    assert not has_cover(cache, "0000000000000000")
    assert not has_cover(cache, "1111111111111111")


def test_move_cache_never_touches_another_ids_files(
    sample_library: SampleLibrary, tmp_path: Path
) -> None:
    cache = tmp_path / "cache"
    render_cover(sample_library.path(A_NEWSPAPER), "aaaaaaaaaaaaaaaa", cache)
    render_cover(sample_library.path(A_NEWSPAPER), "bbbbbbbbbbbbbbbb", cache)
    untouched_cover, untouched_thumb = cover_paths(cache, "bbbbbbbbbbbbbbbb")
    untouched_cover_bytes = untouched_cover.read_bytes()
    untouched_thumb_bytes = untouched_thumb.read_bytes()

    move_cache(cache, "aaaaaaaaaaaaaaaa", "cccccccccccccccc")

    assert has_cover(cache, "cccccccccccccccc")
    assert has_cover(cache, "bbbbbbbbbbbbbbbb")
    assert untouched_cover.read_bytes() == untouched_cover_bytes
    assert untouched_thumb.read_bytes() == untouched_thumb_bytes


def test_move_cache_lets_an_existing_target_win(
    sample_library: SampleLibrary, tmp_path: Path
) -> None:
    """A target already rendered fresh under the new id is never clobbered."""
    cache = tmp_path / "cache"
    render_cover(sample_library.path(A_NEWSPAPER), "dddddddddddddddd", cache)
    render_cover(sample_library.path(A_NEWSPAPER), "eeeeeeeeeeeeeeee", cache)
    target_cover, target_thumb = cover_paths(cache, "eeeeeeeeeeeeeeee")
    target_cover_bytes = target_cover.read_bytes()
    target_thumb_bytes = target_thumb.read_bytes()
    source_pages = page_cache_dir(cache, "dddddddddddddddd")
    source_pages.mkdir(parents=True)
    (source_pages / "1-900.webp").write_bytes(b"from the source")
    target_pages = page_cache_dir(cache, "eeeeeeeeeeeeeeee")
    target_pages.mkdir(parents=True)
    (target_pages / "1-900.webp").write_bytes(b"already there")

    move_cache(cache, "dddddddddddddddd", "eeeeeeeeeeeeeeee")

    assert not has_cover(cache, "dddddddddddddddd")
    assert not source_pages.exists()
    assert target_cover.read_bytes() == target_cover_bytes
    assert target_thumb.read_bytes() == target_thumb_bytes
    assert (target_pages / "1-900.webp").read_bytes() == b"already there"


# ------------------------------------------------------------- ensure_layout


def test_paths_land_under_the_current_version(tmp_path: Path) -> None:
    cache = tmp_path / "cache"
    assert covers_root(cache) == cache / "covers" / "v1"
    assert pages_root(cache) == cache / "pages" / "v1"
    cover, _ = cover_paths(cache, "abcdef0123456789")
    assert cover.is_relative_to(cache / "covers" / "v1")
    assert page_cache_dir(cache, "abcdef0123456789").is_relative_to(cache / "pages" / "v1")


def test_ensure_layout_tolerates_an_absent_cache(tmp_path: Path) -> None:
    ensure_layout(tmp_path / "absent")  # must not raise


def test_ensure_layout_is_a_no_op_on_an_already_current_cache(tmp_path: Path) -> None:
    cache = tmp_path / "cache"
    ensure_layout(cache)
    ensure_layout(cache)  # nothing to migrate or remove; must not raise


def test_ensure_layout_migrates_a_legacy_layout_with_every_file_intact(tmp_path: Path) -> None:
    cache = tmp_path / "cache"
    legacy_cover_dir = cache / "covers" / "ab"
    legacy_cover_dir.mkdir(parents=True)
    (legacy_cover_dir / "abcdef0123456789.cover.jpg").write_bytes(b"cover bytes")
    (legacy_cover_dir / "abcdef0123456789.thumb.jpg").write_bytes(b"thumb bytes")
    legacy_pages_dir = cache / "pages" / "abcdef0123456789"
    legacy_pages_dir.mkdir(parents=True)
    (legacy_pages_dir / "1-900.webp").write_bytes(b"page bytes")

    ensure_layout(cache)

    assert has_cover(cache, "abcdef0123456789")
    cover, thumbnail = cover_paths(cache, "abcdef0123456789")
    assert cover.read_bytes() == b"cover bytes"
    assert thumbnail.read_bytes() == b"thumb bytes"
    page = page_cache_dir(cache, "abcdef0123456789") / "1-900.webp"
    assert page.read_bytes() == b"page bytes"
    # The legacy directories are gone, not just superseded.
    assert not legacy_cover_dir.exists()
    assert not legacy_pages_dir.exists()


def test_ensure_layout_removes_a_stale_version_directory(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    cache = tmp_path / "cache"
    stale = cache / "covers" / "v1" / "ab"
    stale.mkdir(parents=True)
    (stale / "abcdef0123456789.cover.jpg").write_bytes(b"x")
    (stale / "abcdef0123456789.thumb.jpg").write_bytes(b"y")

    monkeypatch.setattr(cache_module, "COVER_VERSION", 2)
    ensure_layout(cache)

    assert not (cache / "covers" / "v1").exists()


def test_ensure_layout_migrates_legacy_into_v1_not_the_current_version(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A legacy, unversioned cache is rendering-version 1's own output,
    never whatever version happens to be current: an installation upgrading
    straight from the unversioned layout past a bumped version must not have
    those older bytes served under the new, immutable URLs."""
    cache = tmp_path / "cache"
    legacy_cover_dir = cache / "covers" / "ab"
    legacy_cover_dir.mkdir(parents=True)
    (legacy_cover_dir / "abcdef0123456789.cover.jpg").write_bytes(b"cover bytes")
    (legacy_cover_dir / "abcdef0123456789.thumb.jpg").write_bytes(b"thumb bytes")

    monkeypatch.setattr(cache_module, "COVER_VERSION", 2)
    ensure_layout(cache)

    assert not (cache / "covers" / "v2" / "ab").exists()
    assert not (cache / "covers" / "v1").exists()


def test_ensure_layout_keeps_an_existing_target_over_a_legacy_source(tmp_path: Path) -> None:
    cache = tmp_path / "cache"
    legacy = cache / "covers" / "ab" / "abcdef0123456789.cover.jpg"
    legacy.parent.mkdir(parents=True)
    legacy.write_bytes(b"legacy bytes")
    fresh = cache / "covers" / "v1" / "ab" / "abcdef0123456789.cover.jpg"
    fresh.parent.mkdir(parents=True)
    fresh.write_bytes(b"fresh bytes")

    ensure_layout(cache)

    assert fresh.read_bytes() == b"fresh bytes"
    assert not legacy.exists()
    assert not (cache / "covers" / "ab").exists()
