"""Opening a PDF: page count, page size, first page text, cover and thumbnail.

This is the slow half of a scan, and the only half that opens a file. It is kept
apart from the scanner so that the expensive work can run on a worker pool, and
so that a PDF nobody can read produces a value — a :class:`CoverError` — instead
of an exception that would take the whole scan down with it.

The cache layout itself — versioned directories under ``<data>/cache/covers``,
what a version bump does — lives in :mod:`paperstand.cache`; the names below
are imported from there and re-exported so that every existing import of this
module keeps working. What follows is what version 1 renders::

    covers/v1/<id[:2]>/<id>.cover.jpg    900 px wide, JPEG quality 85
    covers/v1/<id[:2]>/<id>.thumb.jpg    300 px wide, JPEG quality 85

Both images are produced from a single render, then resized to an exact width,
so that a caller can rely on the widths without knowing the page's aspect ratio.
"""

from __future__ import annotations

import os
import threading
from dataclasses import dataclass
from pathlib import Path

import pymupdf
from PIL import Image

from paperstand.cache import (
    clear_cache as clear_cache,
)
from paperstand.cache import (
    cover_paths as cover_paths,
)
from paperstand.cache import (
    covers_root as covers_root,
)
from paperstand.cache import (
    has_cover as has_cover,
)
from paperstand.cache import (
    move_cache as move_cache,
)
from paperstand.cache import (
    page_cache_dir as page_cache_dir,
)
from paperstand.cache import (
    pages_root as pages_root,
)
from paperstand.logging import get_logger

log = get_logger(__name__)

#: Width, in pixels, of the two generated images.
#: A change here changes the layout `COVER_VERSION` names — bump it.
COVER_WIDTH = 900
THUMB_WIDTH = 300

#: JPEG quality of both images. A change here changes the layout
#: `COVER_VERSION` names — bump it.
JPEG_QUALITY = 85

#: First page text is a search aid, not a transcript.
TEXT_LIMIT = 20_000

#: Rendering wider than this makes no difference to a 900 px image.
#: A change here changes the layout `COVER_VERSION` names — bump it.
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
    """Why one PDF could not be read.

    ``retryable`` is the fact ``probe`` and the render each let ``OSError``
    settle: the environment (permissions, a vanished mount, a full cache), not
    the bytes, so trying again costs nothing and may simply work. Everything
    else — not a PDF, truncated, encrypted, no pages — is the bytes, and stays
    ``False``: a human replaces the file, which is a new hash and a fresh row.
    """

    message: str
    retryable: bool = False


def probe(path: Path) -> None:
    """Prove ``path`` can actually be read, at the cost of two small reads.

    Opens the file, reads the first KiB, seeks near the end and reads the
    last KiB. Lets ``OSError`` propagate — on a healthy mount this never
    raises, so paying for it before every render is what turns "permission
    denied" or "the mount just vanished" into a fact recorded at the moment
    it happens, rather than the very same ``FileDataError`` message PyMuPDF
    gives for a truncated PDF.
    """
    with open(path, "rb") as handle:
        handle.read(1024)
        handle.seek(0, os.SEEK_END)
        size = handle.tell()
        handle.seek(max(0, size - 1024))
        handle.read(1024)


def render_cover(pdf_path: Path, identifier: str, cache_root: Path) -> CoverResult | CoverError:
    """Read a PDF and write its cover and thumbnail into the cache.

    Never raises. ``probe`` runs first: an ``OSError`` there is retryable —
    the environment, not the bytes. Past that point an ``OSError`` (writing
    into the cache: ``mkdir``, ``_save``) is retryable for the same reason;
    every other exception — the file is not a PDF, is truncated, is encrypted
    or has no pages — comes back as a durable :class:`CoverError`, which the
    scanner stores in ``cover_error``.
    """
    try:
        probe(pdf_path)
    except OSError as error:
        log.warning("cannot read %s: %s", pdf_path, error)
        return CoverError(message=f"{type(error).__name__}: {error}", retryable=True)
    try:
        return _render(pdf_path, identifier, cache_root)
    except OSError as error:
        log.warning("cannot read %s: %s", pdf_path, error)
        return CoverError(message=f"{type(error).__name__}: {error}", retryable=True)
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
