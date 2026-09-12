"""The layout of ``<data>/cache``.

Every cover, thumbnail and rendered page lives under a directory named for
the *version* of the parameters it was rendered with, not just the id of the
issue it came from::

    covers/v<N>/<id[:2]>/<id>.cover.jpg
    covers/v<N>/<id[:2]>/<id>.thumb.jpg
    pages/v<N>/<id>/<n>-<w>.webp

Bumping :data:`COVER_VERSION` or :data:`PAGE_VERSION` after a change to how an
image is produced is the whole of a cache invalidation: :func:`ensure_layout`,
called at the top of every scan and once at start-up, deletes every stale
version's directory outright — the next render picks up the new parameters —
and moves anything left over from an even older, unversioned layout into
``v1`` instead of re-rendering it, since the bytes already on disk are
exactly what version 1's own parameters would still produce; only their
address was wrong. Never into whatever version happens to be current — a
later bump may render differently, and an installation upgrading straight
from the unversioned layout must not have those older bytes served under a
newer, immutable URL. A ``v1`` this migration produces after the version has
since moved past it is then pruned by the same pass, exactly like any other
stale version.

This module owns the *paths*; the rendering itself stays in
:mod:`paperstand.scanner.covers` and :mod:`paperstand.render.pages`, which
import these names and re-export them so that every existing ``from
paperstand.scanner.covers import …`` and ``from paperstand.render.pages
import …`` keeps working unchanged.
"""

from __future__ import annotations

import os
import re
import shutil
from pathlib import Path

from paperstand.logging import get_logger

log = get_logger(__name__)

#: What this version of a cover and a thumbnail are rendered with:
#: `COVER_WIDTH`, `THUMB_WIDTH`, `JPEG_QUALITY`, `MAX_ZOOM` and the
#: rasteriser itself, all in `scanner/covers.py`. Bump this when any of them
#: changes what the image looks like; a PyMuPDF major upgrade is a bump of
#: both this and `PAGE_VERSION`.
COVER_VERSION = 1

#: What this version of a rendered page is rendered with: `PAGE_WIDTHS`,
#: `WEBP_QUALITY`, `MAX_ZOOM` and the rasteriser itself, all in
#: `render/pages.py`. Bump this when any of them changes what the image
#: looks like.
PAGE_VERSION = 1

_VERSION_DIR = re.compile(r"^v(\d+)$")


def covers_root(cache_root: Path) -> Path:
    """The directory holding every cover and thumbnail, at the current version."""
    return cache_root / "covers" / f"v{COVER_VERSION}"


def pages_root(cache_root: Path) -> Path:
    """The directory holding every rendered page, at the current version."""
    return cache_root / "pages" / f"v{PAGE_VERSION}"


def cover_paths(cache_root: Path, identifier: str) -> tuple[Path, Path]:
    """Where the cover and the thumbnail of an issue live."""
    folder = covers_root(cache_root) / identifier[:2]
    return folder / f"{identifier}.cover.jpg", folder / f"{identifier}.thumb.jpg"


def page_cache_dir(cache_root: Path, identifier: str) -> Path:
    """Where the rendered pages of an issue live."""
    return pages_root(cache_root) / identifier


def page_path(cache_root: Path, identifier: str, page: int, width: int) -> Path:
    """Where one rendered page lives."""
    return page_cache_dir(cache_root, identifier) / f"{page}-{width}.webp"


def clear_cache(cache_root: Path, identifier: str) -> None:
    """Delete everything cached for an issue: its images and its pages.

    Called when a file changes and when it disappears, so that a stale cover can
    never outlive the bytes it was rendered from.
    """
    cover, thumbnail = cover_paths(cache_root, identifier)
    for path in (cover, thumbnail):
        path.unlink(missing_ok=True)
    shutil.rmtree(page_cache_dir(cache_root, identifier), ignore_errors=True)


def move_cache(cache_root: Path, old: str, new: str) -> None:
    """Move everything cached for ``old`` onto ``new``, without re-rendering.

    Called once, when a row's id changes to the content hash it always had —
    the one-time backfill of a legacy row, or a rename that took the id along
    with it in an older build. The bytes never changed, so the cover, the
    thumbnail and every already-rendered page are still correct; only the name
    they are filed under is wrong. A source that was never rendered is skipped,
    not an error, and a target that already exists — rendered fresh under the
    new id before this ran — wins: the source is discarded rather than
    overwriting it.
    """
    old_cover, old_thumbnail = cover_paths(cache_root, old)
    new_cover, new_thumbnail = cover_paths(cache_root, new)
    new_cover.parent.mkdir(parents=True, exist_ok=True)
    for source, target in ((old_cover, new_cover), (old_thumbnail, new_thumbnail)):
        _move_file(source, target)

    old_pages = page_cache_dir(cache_root, old)
    new_pages = page_cache_dir(cache_root, new)
    if not old_pages.is_dir():
        return
    if new_pages.exists():
        shutil.rmtree(old_pages, ignore_errors=True)
        return
    new_pages.parent.mkdir(parents=True, exist_ok=True)
    shutil.move(str(old_pages), str(new_pages))


def _move_file(source: Path, target: Path) -> None:
    """Move ``source`` onto ``target``, keeping whatever is already there."""
    if not source.is_file():
        return
    if target.exists():
        source.unlink(missing_ok=True)
        return
    os.replace(source, target)


def has_cover(cache_root: Path, identifier: str) -> bool:
    """Whether both images of an issue are on disk."""
    cover, thumbnail = cover_paths(cache_root, identifier)
    return cover.is_file() and thumbnail.is_file()


# ------------------------------------------------------------- version layout


def ensure_layout(cache_root: Path) -> None:
    """Bring ``covers/`` and ``pages/`` up to their current version.

    Called at the top of every scan (``Scanner.begin``, two cheap
    ``listdir``s) and once at start-up by ``ScanScheduler.start`` — so a
    deployment with automatic scanning turned off still gets the migration
    the very first request would otherwise need. Best effort throughout: an
    I/O error is logged and never raised, so one unreadable entry costs
    that entry, not the scan.
    """
    _ensure_one(cache_root / "covers", COVER_VERSION)
    _ensure_one(cache_root / "pages", PAGE_VERSION)


def _ensure_one(root: Path, current: int) -> None:
    """Migrate anything legacy under ``root`` into ``v1``, then prune every
    stale ``v<M>``, in that order.

    A legacy, unversioned entry predates rendering version 1, not whatever
    version happens to be current: it is version 1's own output, and only
    ever migrates there, never into a later version it was not rendered
    with. The prune runs afterwards, over a fresh listing, so a ``v1`` this
    migration just produced is itself removed like any other stale version
    when the current version has since moved past it.
    """
    try:
        children = list(root.iterdir())
    except FileNotFoundError:
        return
    except OSError as error:
        log.warning("cannot list %s: %s", root, error)
        return

    legacy = [child for child in children if _VERSION_DIR.match(child.name) is None]
    if legacy:
        _migrate_legacy(root / "v1", legacy)

    try:
        children = list(root.iterdir())
    except OSError as error:
        log.warning("cannot list %s: %s", root, error)
        return
    for child in children:
        match = _VERSION_DIR.match(child.name)
        if match is not None and int(match.group(1)) != current:
            _remove_stale(child)


def _remove_stale(path: Path) -> None:
    """Delete a ``v<M>`` directory that is not the current version."""
    try:
        shutil.rmtree(path)
    except OSError as error:
        log.warning("cannot remove the stale cache directory %s: %s", path, error)
        return
    log.info("removed the stale cache directory %s", path)


def _migrate_legacy(target_root: Path, legacy: list[Path]) -> None:
    """Move every entry of a pre-versioning layout under ``target_root``.

    A legacy entry is either a two-character cover folder or a per-issue
    page directory — never a file directly, in today's layout, but a
    stray one is still moved rather than ignored. Merged file by file
    against whatever already lives at the target, so a cover already
    rendered fresh under the current version — by a scan that ran between
    an upgrade and this migration — is never overwritten by the legacy
    copy: :func:`_move_file`'s rule applies at the leaf, not the folder.
    """
    try:
        target_root.mkdir(parents=True, exist_ok=True)
    except OSError as error:
        log.warning("cannot create %s: %s", target_root, error)
        return
    moved = 0
    for child in legacy:
        try:
            if child.is_dir() and not child.is_symlink():
                moved += _merge_directory(child, target_root / child.name)
            else:
                target = target_root / child.name
                target.parent.mkdir(parents=True, exist_ok=True)
                _move_file(child, target)
                moved += 1
        except OSError as error:
            log.warning("cannot migrate %s to %s: %s", child, target_root, error)
    log.info("migrated %d legacy cache director(y/ies) into %s", moved, target_root)


def _merge_directory(source_dir: Path, target_dir: Path) -> int:
    """Move every file under ``source_dir`` onto the same relative path under
    ``target_dir``, then remove ``source_dir``. Returns how many files moved."""
    moved = 0
    for entry in sorted(source_dir.rglob("*")):
        if entry.is_dir():
            continue
        target = target_dir / entry.relative_to(source_dir)
        target.parent.mkdir(parents=True, exist_ok=True)
        _move_file(entry, target)
        moved += 1
    shutil.rmtree(source_dir, ignore_errors=True)
    return moved
