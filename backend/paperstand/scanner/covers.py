"""Opening a PDF: page count, page size, first page text, cover and thumbnail.

This is the slow half of a scan, and the only half that opens a file. It is kept
apart from the scanner so that the expensive work can run on a worker pool, and
so that a PDF nobody can read produces a value — a :class:`CoverError` — instead
of an exception that would take the whole scan down with it.

The cache layout, under ``<data>/cache``::

    covers/<id[:2]>/<id>.cover.jpg    900 px wide, JPEG quality 85
    covers/<id[:2]>/<id>.thumb.jpg    300 px wide, JPEG quality 85
    pages/<id>/<n>-<w>.webp           rendered pages

Both images are produced from a single render, then resized to an exact width,
so that a caller can rely on the widths without knowing the page's aspect ratio.
"""

from __future__ import annotations

import os
import shutil
import threading
from dataclasses import dataclass
from pathlib import Path

import pymupdf
from PIL import Image

from paperstand.logging import get_logger

log = get_logger(__name__)

#: Width, in pixels, of the two generated images.
COVER_WIDTH = 900
THUMB_WIDTH = 300

#: JPEG quality of both images.
JPEG_QUALITY = 85

#: First page text is a search aid, not a transcript.
TEXT_LIMIT = 20_000

#: Rendering wider than this makes no difference to a 900 px image.
MAX_ZOOM = 8.0


@dataclass(frozen=True, slots=True)
class CoverResult:
    """What opening one PDF produced."""

    page_count: int
    page_w: float
    page_h: float
    first_page_text: str
    cover: Path
    thumbnail: Path


@dataclass(frozen=True, slots=True)
class CoverError:
    """Why one PDF could not be read."""

    message: str


def covers_root(cache_root: Path) -> Path:
    """The directory holding every cover and thumbnail."""
    return cache_root / "covers"


def pages_root(cache_root: Path) -> Path:
    """The directory holding every rendered page."""
    return cache_root / "pages"


def cover_paths(cache_root: Path, identifier: str) -> tuple[Path, Path]:
    """Where the cover and the thumbnail of an issue live."""
    folder = covers_root(cache_root) / identifier[:2]
    return folder / f"{identifier}.cover.jpg", folder / f"{identifier}.thumb.jpg"


def page_cache_dir(cache_root: Path, identifier: str) -> Path:
    """Where the rendered pages of an issue live."""
    return pages_root(cache_root) / identifier


def clear_cache(cache_root: Path, identifier: str) -> None:
    """Delete everything cached for an issue: its images and its pages.

    Called when a file changes and when it disappears, so that a stale cover can
    never outlive the bytes it was rendered from.
    """
    cover, thumbnail = cover_paths(cache_root, identifier)
    for path in (cover, thumbnail):
        path.unlink(missing_ok=True)
    shutil.rmtree(page_cache_dir(cache_root, identifier), ignore_errors=True)


def has_cover(cache_root: Path, identifier: str) -> bool:
    """Whether both images of an issue are on disk."""
    cover, thumbnail = cover_paths(cache_root, identifier)
    return cover.is_file() and thumbnail.is_file()


def render_cover(pdf_path: Path, identifier: str, cache_root: Path) -> CoverResult | CoverError:
    """Read a PDF and write its cover and thumbnail into the cache.

    Never raises: a file that is not a PDF, is truncated, is encrypted or has no
    pages comes back as a :class:`CoverError` carrying the reason, which the
    scanner stores in ``cover_error``.
    """
    try:
        return _render(pdf_path, identifier, cache_root)
    except Exception as error:  # one unreadable file must not stop a scan
        log.warning("cannot read %s: %s", pdf_path, error)
        return CoverError(message=f"{type(error).__name__}: {error}")


def _render(pdf_path: Path, identifier: str, cache_root: Path) -> CoverResult | CoverError:
    with pymupdf.open(pdf_path) as document:
        if document.needs_pass:
            return CoverError(message="the document is encrypted")
        page_count = document.page_count
        if page_count < 1:
            return CoverError(message="the document has no pages")
        page = document.load_page(0)
        rect = page.rect
        text = page.get_text("text")[:TEXT_LIMIT]
        image = _page_image(page, rect.width)

    cover, thumbnail = cover_paths(cache_root, identifier)
    cover.parent.mkdir(parents=True, exist_ok=True)
    _save(image, cover, COVER_WIDTH)
    _save(image, thumbnail, THUMB_WIDTH)
    image.close()
    return CoverResult(
        page_count=page_count,
        page_w=float(rect.width),
        page_h=float(rect.height),
        first_page_text=text,
        cover=cover,
        thumbnail=thumbnail,
    )


def _page_image(page: pymupdf.Page, width: float) -> Image.Image:
    """Rasterise a page once, big enough for the widest image wanted."""
    zoom = min(COVER_WIDTH / width, MAX_ZOOM) if width > 0 else 1.0
    pixmap = page.get_pixmap(matrix=pymupdf.Matrix(zoom, zoom), alpha=False)
    image = Image.frombytes("RGB", (pixmap.width, pixmap.height), pixmap.samples)
    return image


def _save(image: Image.Image, path: Path, width: int) -> None:
    """Write ``image`` as a JPEG exactly ``width`` pixels wide.

    Written under a temporary name in the same directory and moved into place,
    so that ``path`` is either the previous complete image or the new complete
    one and never the half of one that has been written so far. A request
    serving the cover while a scan re-renders it must not see a truncated file.
    """
    height = max(1, round(image.height * width / image.width))
    resized = image if image.size == (width, height) else image.resize((width, height))
    temporary = path.with_name(f"{path.name}.{os.getpid()}-{threading.get_ident()}.tmp")
    try:
        resized.save(temporary, format="JPEG", quality=JPEG_QUALITY, optimize=True)
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)
        if resized is not image:
            resized.close()
