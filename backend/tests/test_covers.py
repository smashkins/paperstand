"""Cover and thumbnail rendering, and the cache they live in."""

from __future__ import annotations

from pathlib import Path

from PIL import Image

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
    page_cache_dir,
    render_cover,
)
from tests.conftest import SampleLibrary

A_NEWSPAPER = "Newspapers/2026/03/17/Corriere_del_Ponte_17_Marzo_2026.pdf"


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
