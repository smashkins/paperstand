"""Walking the library root.

The walk is the only part of Paperstand that touches the collection, and it is
read-only: it opens nothing, writes nothing and creates nothing under the root.
What it yields is the little the fast phase of a scan needs — the path relative
to the root, the size and the modification time in nanoseconds — because that
triple is what decides whether a file has to be parsed again.

What is skipped: folders whose name starts with ``@``, ``.`` or ``#``, files
whose name starts with ``.`` or ``#`` — a file starting with ``@`` carries a
handle prefix the parser strips, so it is kept — anything matching the
configured ``ignore`` list, anything that is not a PDF, and any symlink leading
outside the root.

A folder that cannot be read does not abort the walk, but it is not silently
forgotten either: :class:`Walk` records it, and :meth:`Walk.covers` then answers,
for any stored path, whether the walk actually looked where that file lives. That
is what stops a permission error or an unmounted volume from being read as "every
one of those issues was deleted".
"""

from __future__ import annotations

import os
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path

from paperstand.config import PaperstandConfig
from paperstand.logging import get_logger

log = get_logger(__name__)

#: The only extension a library file may have.
PDF_SUFFIX = ".pdf"

MAX_DEPTH = 32
"""Hard stop on recursion, so a pathological tree cannot exhaust the stack."""


@dataclass(frozen=True, slots=True)
class LibraryFile:
    """One PDF found under the library root."""

    rel_path: str
    size: int
    mtime_ns: int

    @property
    def filename(self) -> str:
        """The file's own name, without any folder."""
        return self.rel_path.rsplit("/", 1)[-1]


class Walk:
    """One pass over a library root, and what it could not see.

    Iterate it to get the PDFs. Afterwards, :attr:`root_ok` says whether the root
    itself could be listed, :attr:`unreadable` holds the prefixes the walk had to
    give up on, and :meth:`covers` says whether a given path was in territory the
    walk actually reached.
    """

    def __init__(self, root: Path, config: PaperstandConfig) -> None:
        self.root = root
        self.config = config
        self.root_ok = root.is_dir()
        self.unreadable: set[str] = set()

    def __iter__(self) -> Iterator[LibraryFile]:
        if not self.root_ok:
            log.warning("library root %s is not a directory; nothing to walk", self.root)
            return
        yield from self._walk(self.root, self.root.resolve(), "", 0)

    @property
    def complete(self) -> bool:
        """Whether every folder under the root was read."""
        return self.root_ok and not self.unreadable

    def covers(self, rel_path: str) -> bool:
        """Whether the walk looked where ``rel_path`` lives.

        ``False`` means "no idea": the file may still be there behind a folder
        that could not be listed, so its row must be left alone.
        """
        if not self.root_ok:
            return False
        return not any(rel_path.startswith(prefix) for prefix in self.unreadable)

    # ----------------------------------------------------------------- private

    def _give_up(self, prefix: str, reason: str) -> None:
        self.unreadable.add(prefix)
        log.warning("cannot read %s%s: %s", self.root, f"/{prefix}" if prefix else "", reason)

    def _walk(
        self, directory: Path, resolved_root: Path, prefix: str, depth: int
    ) -> Iterator[LibraryFile]:
        if depth > MAX_DEPTH:
            self._give_up(prefix, f"more than {MAX_DEPTH} levels deep")
            return
        try:
            with os.scandir(directory) as entries:
                listing = sorted(entries, key=lambda entry: entry.name)
        except OSError as error:
            self._give_up(prefix, str(error))
            return
        for entry in listing:
            directory_entry = _is_directory(entry)
            if self.config.is_ignored(entry.name, directory=directory_entry):
                continue
            rel_path = f"{prefix}{entry.name}"
            if directory_entry:
                if entry.is_symlink() and not _inside(entry.path, resolved_root):
                    log.debug("skipping %s: a symlink leading out of the library", rel_path)
                    continue
                yield from self._walk(Path(entry.path), resolved_root, f"{rel_path}/", depth + 1)
                continue
            if not entry.name.lower().endswith(PDF_SUFFIX):
                continue
            if entry.is_symlink() and not _inside(entry.path, resolved_root):
                log.debug("skipping %s: a symlink leading out of the library", rel_path)
                continue
            try:
                info = entry.stat(follow_symlinks=True)
            except OSError as error:
                self._give_up(rel_path, str(error))
                continue
            yield LibraryFile(rel_path=rel_path, size=info.st_size, mtime_ns=info.st_mtime_ns)


def top_level_folders(root: Path) -> list[str]:
    """Names of the directories directly under ``root``, sorted.

    An empty list means "nothing to discover" *and* "the root could not be read":
    a caller that acts on the difference must check the root itself first.
    """
    if not root.is_dir():
        return []
    try:
        return sorted(entry.name for entry in root.iterdir() if entry.is_dir())
    except OSError as error:  # pragma: no cover - depends on the filesystem
        log.warning("cannot list %s: %s", root, error)
        return []


def walk_library(root: Path, config: PaperstandConfig) -> Iterator[LibraryFile]:
    """Yield every PDF under ``root`` that the configuration does not skip."""
    return iter(Walk(root, config))


def iter_library_files(root: Path, config: PaperstandConfig) -> Iterator[str]:
    """Yield the relative path of every PDF under ``root``."""
    for found in walk_library(root, config):
        yield found.rel_path


def _is_directory(entry: os.DirEntry[str]) -> bool:
    try:
        return entry.is_dir(follow_symlinks=True)
    except OSError:  # pragma: no cover - a broken symlink races the walk
        return False


def _inside(path: str, resolved_root: Path) -> bool:
    """Whether ``path``, once every symlink is resolved, stays under the root."""
    try:
        target = Path(path).resolve(strict=True)
    except OSError:  # pragma: no cover - a dangling symlink
        return False
    return target == resolved_root or resolved_root in target.parents
