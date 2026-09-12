"""Rendering PDF pages to WebP, and keeping the cache that holds them bounded.

A page is rendered once and then served from
``<data>/cache/pages/v<PAGE_VERSION>/<issue id>/<n>-<w>.webp`` for ever: the
file name carries everything that identifies the image, and the scanner
deletes the whole directory when the PDF underneath changes, so a cached page
can never be stale. That is what lets the endpoint answer ``Cache-Control:
immutable``. The version directory is :mod:`paperstand.cache`'s doing — see
there for what a version bump costs and when to make one.

Two things bound the cost.

**Concurrency.** Rasterising is CPU-bound and memory-hungry, so however many
requests arrive at once, only ``PAPERSTAND_RENDER_WORKERS`` of them rasterise at
a time. The work happens off the event loop, in ``asyncio.to_thread``, holding a
plain :class:`threading.Semaphore`.

**Disk.** The cache is swept back under ``PAPERSTAND_PAGE_CACHE_MAX_MB``, oldest
access first. Access time is what a page cache should be evicted by, and since
``relatime`` mounts only refresh it lazily, a cache *hit* touches the file
itself rather than trusting the filesystem to have noticed.

A sweep never takes the page a request is about to serve. Three rules make that
true even when the configured limit is smaller than a single rendered page: the
file just produced is named explicitly and skipped, anything used in the last
:data:`EVICT_MIN_AGE` seconds is skipped, and the limit is floored at the size
of the largest file in the cache, so "make it fit" can never mean "empty it".

Widths are snapped to a fixed ladder so that the cache holds a handful of sizes
rather than one per device pixel ratio in the world. A request lands on the
smallest width that is at least as wide as it asked for — never on a narrower
one, which would show as a blurry page — and nothing above the top of the ladder
is rendered at all.
"""

from __future__ import annotations

import asyncio
import os
import shutil
import threading
import time
from collections.abc import Collection
from dataclasses import dataclass
from pathlib import Path

import pymupdf
from PIL import Image

from paperstand.cache import (
    page_cache_dir as page_cache_dir,
)
from paperstand.cache import (
    page_path as page_path,
)
from paperstand.cache import (
    pages_root as pages_root,
)
from paperstand.config import Settings
from paperstand.logging import get_logger
from paperstand.render.locks import KeyedLock

log = get_logger(__name__)

#: The widths, in pixels, a page is ever rendered at.
#: A change here changes the layout `PAGE_VERSION` names — bump it.
PAGE_WIDTHS: tuple[int, ...] = (200, 400, 800, 1200, 1600, 2000)

#: The width used when the request does not ask for one.
DEFAULT_WIDTH = 1200

#: The largest ``?w=`` a request may carry before it is refused outright.
#: Anything between the top of the ladder and this is served at the top; the
#: bound exists so that a nonsense value is a ``422`` rather than a big integer
#: travelling through the renderer.
MAX_WIDTH_QUERY = 100_000

#: WebP quality. 80 is where the artefacts stop being visible on a page of
#: text. A change here changes the layout `PAGE_VERSION` names — bump it.
WEBP_QUALITY = 80

#: Rendering wider than this per point of page width is pointless.
#: A change here changes the layout `PAGE_VERSION` names — bump it.
MAX_ZOOM = 8.0

#: How many renders go by before the cache is swept without being asked to.
EVICT_EVERY = 50

#: A page used more recently than this is never evicted, whatever the limit.
#: It is almost certainly on its way to a response, or part of the spread the
#: reader is looking at right now.
EVICT_MIN_AGE = 30.0


class RenderError(RuntimeError):
    """A page could not be rendered."""


class PageOutOfRange(RenderError):
    """The document has no such page."""


@dataclass(frozen=True, slots=True)
class Eviction:
    """What one sweep of the page cache did."""

    before: int
    after: int
    removed: int

    @property
    def freed(self) -> int:
        """Bytes the sweep gave back."""
        return self.before - self.after


def snap_width(width: int | None) -> int:
    """The width a request is actually served at.

    The smallest entry of :data:`PAGE_WIDTHS` that is at least ``width``, so a
    page is never upscaled by the browser; anything wider than the ladder is
    served at its top, and no width at all means :data:`DEFAULT_WIDTH`.
    """
    if width is None:
        return DEFAULT_WIDTH
    for candidate in PAGE_WIDTHS:
        if width <= candidate:
            return candidate
    return PAGE_WIDTHS[-1]


def render_page(pdf_path: Path, page: int, width: int, target: Path) -> Path:
    """Rasterise page ``page`` (1-based) of ``pdf_path`` into ``target``.

    Raises :class:`PageOutOfRange` when the document is shorter than asked for,
    and :class:`RenderError` when it cannot be read at all — the caller turns
    the first into a 404 and the second into a 503.
    """
    try:
        with pymupdf.open(pdf_path) as document:
            if document.needs_pass:
                raise RenderError("the document is encrypted")
            if page < 1 or page > document.page_count:
                raise PageOutOfRange(f"page {page} of {document.page_count}")
            loaded = document.load_page(page - 1)
            box = loaded.rect
            zoom = min(width / box.width, MAX_ZOOM) if box.width > 0 else 1.0
            pixmap = loaded.get_pixmap(matrix=pymupdf.Matrix(zoom, zoom), alpha=False)
            image = Image.frombytes("RGB", (pixmap.width, pixmap.height), pixmap.samples)
    except (PageOutOfRange, RenderError):
        raise
    except Exception as error:  # a broken PDF is an answer, not a crash
        raise RenderError(f"{type(error).__name__}: {error}") from error

    height = max(1, round(image.height * width / image.width))
    if image.size != (width, height):
        resized = image.resize((width, height))
        image.close()
        image = resized
    target.parent.mkdir(parents=True, exist_ok=True)
    # Written beside the target and moved into place, so that two requests for
    # the same page never hand a half-written file to the second one.
    temporary = target.with_name(f"{target.name}.{os.getpid()}-{threading.get_ident()}.tmp")
    try:
        image.save(temporary, format="WEBP", quality=WEBP_QUALITY, method=4)
        os.replace(temporary, target)
    finally:
        image.close()
        temporary.unlink(missing_ok=True)
    return target


def cache_size(cache_root: Path) -> int:
    """How many bytes the page cache is using."""
    return sum(size for _, size, _ in _entries(pages_root(cache_root)))


def evict(
    cache_root: Path,
    max_bytes: int,
    *,
    keep: Collection[Path] = (),
    min_age: float = EVICT_MIN_AGE,
) -> Eviction:
    """Delete the least recently used pages until the cache fits.

    A limit of zero or less means "no limit", which is how a deployment turns
    eviction off; a limit that is met already costs one ``stat`` per file and
    nothing else.

    Three things are never deleted, so that a sweep cannot destroy the answer a
    request is holding: a path named in ``keep``, anything used less than
    ``min_age`` seconds ago, and — via the floor below — enough of the cache to
    hold its largest single file. A limit smaller than one rendered page is a
    misconfiguration, and the right response to it is a cache of one page, not a
    render that deletes itself before it can be served.
    """
    root = pages_root(cache_root)
    entries = _entries(root)
    total = sum(size for _, size, _ in entries)
    if max_bytes <= 0 or total <= max_bytes:
        return Eviction(before=total, after=total, removed=0)

    limit = max(max_bytes, max((size for _, size, _ in entries), default=0))
    protected = {path.resolve() for path in keep}
    youngest = time.time() - min_age

    removed = 0
    after = total
    for used, size, path in sorted(entries):
        if after <= limit:
            break
        if path.resolve() in protected or used / 1_000_000_000 > youngest:
            continue
        try:
            path.unlink()
        except OSError as error:  # pragma: no cover - a race with another sweep
            log.debug("cannot evict %s: %s", path, error)
            continue
        after -= size
        removed += 1
    _drop_empty_dirs(root)
    if removed:
        log.info(
            "page cache: %d file(s) evicted, %.1f MiB freed, %.1f MiB left",
            removed,
            (total - after) / 1_048_576,
            after / 1_048_576,
        )
    return Eviction(before=total, after=after, removed=removed)


def _entries(root: Path) -> list[tuple[int, int, Path]]:
    """Every cached page as ``(last used in ns, size, path)``.

    "Last used" is the later of the access and modification times: a page that
    has just been rendered has never been read, and must still count as fresh.
    """
    found: list[tuple[int, int, Path]] = []
    if not root.is_dir():
        return found
    for path in root.rglob("*.webp"):
        try:
            stat = path.stat()
        except OSError:  # pragma: no cover - evicted from under us
            continue
        found.append((max(stat.st_atime_ns, stat.st_mtime_ns), stat.st_size, path))
    return found


def _drop_empty_dirs(root: Path) -> None:
    """Remove the per-issue directories a sweep emptied."""
    if not root.is_dir():
        return
    for child in root.iterdir():
        if child.is_dir() and not any(child.iterdir()):
            shutil.rmtree(child, ignore_errors=True)


def touch(path: Path) -> None:
    """Record that a cached page was used, for the eviction order.

    Best effort on purpose: a read-only or full ``/data`` must not turn a cache
    hit into an error.
    """
    try:
        os.utime(path, None)
    except OSError as error:  # pragma: no cover - unwritable cache
        log.debug("cannot touch %s: %s", path, error)


class PageRenderer:
    """The page cache of one running application.

    Owns the semaphore that bounds concurrent rasterising and the counter that
    decides when the cache is swept without a scan having asked for it.
    """

    def __init__(self, settings: Settings) -> None:
        self.cache_root = settings.cache_path
        self.max_bytes = max(0, settings.page_cache_max_mb) * 1_048_576
        self.semaphore = threading.Semaphore(max(1, settings.render_workers))
        self.locks = KeyedLock()
        self._lock = threading.Lock()
        self._since_sweep = 0

    async def page(self, pdf_path: Path, identifier: str, page: int, width: int) -> Path:
        """The cached image of one page, rendering it first if need be."""
        target = page_path(self.cache_root, identifier, page, width)
        if target.is_file():
            touch(target)
            return target
        await asyncio.to_thread(self._render, pdf_path, page, width, target)
        return target

    def _render(self, pdf_path: Path, page: int, width: int, target: Path) -> None:
        with self.semaphore:
            if target.is_file():  # another request got there while we waited
                return
            render_page(pdf_path, page, width, target)
        # Swept only once the file exists and is about to be returned, and with
        # that file named as untouchable, so the sweep can never delete the very
        # page it was triggered by.
        self._count_render(target)

    def _count_render(self, target: Path | None = None) -> None:
        """Sweep the cache every :data:`EVICT_EVERY` renders."""
        with self._lock:
            self._since_sweep += 1
            if self._since_sweep < EVICT_EVERY:
                return
            self._since_sweep = 0
        self.evict(keep=() if target is None else (target,))

    def evict(self, *, keep: Collection[Path] = ()) -> Eviction:
        """Sweep the cache back under its limit."""
        return evict(self.cache_root, self.max_bytes, keep=keep)
