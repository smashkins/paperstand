"""Moving a file into the canonical layout: atomic, and never overwriting.

The first code in :mod:`paperstand.organizer` that touches the filesystem for
anything but reading. :func:`move_file` is the one operation the inbox
pipeline uses to place a file, whether that destination is a spot in the
library or a `<inbox>/unsorted/` or `<inbox>/duplicates/` folder — the same
primitive either way, because on the same filesystem it is always the same
hard-link-then-unlink step.

A crash between steps can leave one dot-prefixed ``.part`` file next to a
destination: invisible to a walk (which skips dot-prefixed files), and safe to
delete by hand. Nothing else is ever left behind, and a file already at the
destination is never overwritten — :class:`DestinationOccupied` is raised
instead, with the source left exactly as it was.
"""

from __future__ import annotations

import errno
import os
import shutil
import tempfile
from pathlib import Path

from paperstand.logging import get_logger

log = get_logger(__name__)

__all__ = ["DestinationOccupied", "move_file", "park", "remove_sidecar", "sidecar_path"]

#: errno values meaning a filesystem does not support hard links at all, as
#: opposed to EEXIST (something is already there) or EXDEV (cross-device,
#: the expected case in the container: the inbox and the library are two
#: separate bind mounts).
_LINK_UNSUPPORTED: tuple[int, ...] = (errno.EPERM, errno.ENOTSUP, errno.EOPNOTSUPP)

#: The sidecar's own suffix, appended to the parked file's full name
#: (`<name>.pdf` -> `<name>.pdf.txt`) so it never collides with a PDF.
SIDECAR_SUFFIX = ".txt"


class DestinationOccupied(OSError):
    """``destination`` already exists; the move never overwrites it."""


def move_file(source: Path, destination: Path) -> None:
    """Move ``source`` to ``destination``, creating its parents, never overwriting.

    1. ``os.link(source, destination)`` then ``os.unlink(source)`` — the
       common, same-filesystem case: atomic, because the file exists at both
       names for no observable instant and at neither for none either.
    2. Cross-device (``EXDEV``), or the source filesystem does not support
       hard links here (``EPERM``, ``ENOTSUP``, ``EOPNOTSUPP``): copy the
       bytes into a dot-prefixed temporary file next to the destination, so a
       crash partway through never exposes a partial PDF; see
       :func:`_copy_and_link` for the rest.

    Raises :class:`DestinationOccupied` when ``destination`` already exists —
    at either the first or the final link attempt — always leaving ``source``
    untouched. Any other :class:`OSError` propagates.
    """
    destination.parent.mkdir(parents=True, exist_ok=True)
    try:
        os.link(source, destination)
    except OSError as error:
        if error.errno == errno.EEXIST:
            raise DestinationOccupied(f"{destination} already exists") from error
        if error.errno == errno.EXDEV or error.errno in _LINK_UNSUPPORTED:
            _copy_and_link(source, destination)
        else:
            raise
    os.unlink(source)


def _copy_and_link(source: Path, destination: Path) -> None:
    """Steps 2 and 3 of :func:`move_file`: copy, stamp, then link into place.

    Written under a dot-prefixed ``.part`` name — invisible to a walk, which
    skips dot-prefixed files — fsynced, and stamped with the source's own
    atime and mtime before it is ever linked to ``destination``, so a copy
    never looks like a fresh touch. Only ``os.link`` (or, when the
    destination filesystem does not support hard links either,
    ``os.replace``) makes the bytes visible at ``destination``.
    """
    info = source.stat()
    with tempfile.NamedTemporaryFile(
        dir=destination.parent, prefix=".", suffix=".part", delete=False
    ) as writer:
        tmp_path = Path(writer.name)
        with source.open("rb") as reader:
            shutil.copyfileobj(reader, writer)
            writer.flush()
            os.fsync(writer.fileno())
    consumed = False
    try:
        os.utime(tmp_path, ns=(info.st_atime_ns, info.st_mtime_ns))
        try:
            os.link(tmp_path, destination)
        except OSError as error:
            if error.errno == errno.EEXIST:
                raise DestinationOccupied(f"{destination} already exists") from error
            if error.errno in _LINK_UNSUPPORTED:
                if destination.exists():
                    raise DestinationOccupied(f"{destination} already exists") from error
                log.warning(
                    "%s does not support hard links; falling back to a non-atomic replace",
                    destination.parent,
                )
                os.replace(tmp_path, destination)
                consumed = True
            else:
                raise
        else:
            tmp_path.unlink()
            consumed = True
    finally:
        if not consumed:
            tmp_path.unlink(missing_ok=True)
    _fsync_directory(destination.parent)


def _fsync_directory(directory: Path) -> None:
    """Best-effort fsync of a directory, so a link just made survives a crash.

    Errors are ignored: some filesystems refuse to open a directory this way.
    The *source* directory is deliberately never fsynced by this module —
    losing the final unlink in a crash re-presents the file as a duplicate on
    the next run, which is the safe side to fail on.
    """
    try:
        fd = os.open(directory, os.O_RDONLY)
    except OSError:
        return
    try:
        os.fsync(fd)
    except OSError:
        pass
    finally:
        os.close(fd)


def sidecar_path(file: Path) -> Path:
    """The one-line reason file that travels next to a parked PDF."""
    return file.with_name(file.name + SIDECAR_SUFFIX)


def remove_sidecar(file: Path) -> None:
    """Delete ``file``'s sidecar, if it has one.

    The only delete the organizer performs anywhere.
    """
    sidecar_path(file).unlink(missing_ok=True)


def park(source: Path, folder: Path, reason: str) -> Path:
    """Move ``source`` into ``folder`` under its own name, recording ``reason``.

    Used for both ``<inbox>/unsorted/`` and ``<inbox>/duplicates/``: always the
    same filesystem as the inbox, so :func:`move_file` takes the hard-link
    path. A name already taken in ``folder`` by a *different* file is never
    overwritten: `` (2)``, `` (3)``… is tried, before the ``.pdf`` suffix,
    until one is free.

    ``source`` may already sit at ``folder / source.name`` — a file parked on
    an earlier run, re-evaluated in place — in which case nothing is moved and
    only the sidecar is touched. Either way, the sidecar `<name>.pdf.txt` ends
    up holding exactly ``reason`` and a trailing newline, rewritten only when
    that is not already what it holds.
    """
    destination = folder / source.name
    if source.resolve() == destination.resolve():
        _write_sidecar(destination, reason)
        return destination
    suffix = 1
    while True:
        try:
            move_file(source, destination)
        except DestinationOccupied:
            suffix += 1
            destination = folder / f"{source.stem} ({suffix}){source.suffix}"
        else:
            break
    _write_sidecar(destination, reason)
    return destination


def _write_sidecar(file: Path, reason: str) -> None:
    """Write ``file``'s sidecar, only when ``reason`` is not what it already holds."""
    sidecar = sidecar_path(file)
    content = reason + "\n"
    try:
        current: str | None = sidecar.read_text("utf-8")
    except OSError:
        current = None
    if current != content:
        sidecar.write_text(content, encoding="utf-8")
