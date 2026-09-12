"""Planning an in-library migration to the canonical layout.

Where :mod:`paperstand.organizer.inbox` imports PDFs *into* the library from a
writable inbox, this module plans moving files already *inside* it, in place,
to the canonical `<Title>/<YYYY>/<Title> - <ISO date>[ - n<number>].pdf`
layout that a fresh naming rule, or a newly declared publication, may have
made possible. :func:`plan_library` is the pure planner — the computation
``organize-plan`` used to do inline, factored out here so a future writer can
share exactly one walk of the library and one set of naming decisions.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path

from paperstand.cli.parse import parse_file
from paperstand.config import PaperstandConfig
from paperstand.organizer.naming import Unsorted, plan_issue
from paperstand.organizer.resolve import declared_title_folders
from paperstand.publication import PublicationIndex
from paperstand.scanner.walker import Walk

__all__ = [
    "Collision",
    "InPlace",
    "LibraryPlan",
    "Move",
    "Unplaced",
    "plan_library",
]


@dataclass(frozen=True, slots=True)
class InPlace:
    """``rel_path`` is already named and placed the way the canonical layout wants it."""

    rel_path: str


@dataclass(frozen=True, slots=True)
class Move:
    """``rel_path`` would move to ``destination``, both library-relative."""

    rel_path: str
    destination: str


@dataclass(frozen=True, slots=True)
class Unplaced:
    """``rel_path`` would stay exactly where it is, for ``reason``."""

    rel_path: str
    reason: str


@dataclass(frozen=True, slots=True)
class Collision:
    """Two or more sources whose plan resolves to the same ``destination``.

    ``sources`` is sorted, has at least two entries, and includes a source
    already in place at ``destination`` when one is a member of the group —
    the group is what makes that in-place file unsafe to treat as settled.
    """

    destination: str
    sources: tuple[str, ...]


@dataclass(slots=True)
class LibraryPlan:
    """One walk of a library, planned against the canonical layout.

    ``entries`` is exactly one per file the walk found in a configured
    library, in walk order; a file outside every configured library is
    skipped silently, the same as ``parse-report``. ``walk_complete`` is
    always ``True`` in this build — the walk is always fully buffered before
    anything else runs — kept as its own field for a future caller that
    might plan against a partial listing.
    """

    entries: list[InPlace | Move | Unplaced]
    collisions: list[Collision]
    walk_complete: bool

    def movable(self) -> list[Move]:
        """This plan's ``Move`` entries whose destination is in no collision."""
        contested = {group.destination for group in self.collisions}
        return [
            entry
            for entry in self.entries
            if isinstance(entry, Move) and entry.destination not in contested
        ]


def plan_library(root: Path, config: PaperstandConfig) -> LibraryPlan:
    """Where every PDF under ``root`` would live under the canonical layout.

    Exactly the computation ``organize-plan`` used to do inline: one buffered
    walk, one :class:`~paperstand.publication.PublicationIndex`, one call to
    :func:`~paperstand.organizer.resolve.declared_title_folders`, so that a
    misconfiguration warning (two folders declaring the same title) is
    logged once per call, not once per consumer.
    """
    walk = Walk(root, config)
    buffered = list(walk)
    index = PublicationIndex(root)
    index.load_all(walk.publications)
    title_folders = declared_title_folders(index, walk.publications, config)

    entries: list[InPlace | Move | Unplaced] = []
    sources_by_canonical: dict[str, list[str]] = defaultdict(list)

    for found in buffered:
        rel_path = found.rel_path
        library = config.library_for(rel_path)
        if library is None:
            continue
        publication = index.resolve(found.publication_dir, library, config)
        issue = parse_file(rel_path, root, config, publication)
        if issue is None:
            continue
        planned = plan_issue(
            issue,
            publication_folder=title_folders.get((library.name, issue.title_name)),
            library_path=library.path,
        )
        if isinstance(planned, Unsorted):
            entries.append(Unplaced(rel_path, planned.reason))
            continue
        sources_by_canonical[planned.rel_path].append(rel_path)
        if planned.rel_path == rel_path:
            entries.append(InPlace(rel_path))
        else:
            entries.append(Move(rel_path, planned.rel_path))

    collisions = [
        Collision(destination, tuple(sorted(sources)))
        for destination, sources in sorted(sources_by_canonical.items())
        if len(sources) > 1
    ]

    return LibraryPlan(entries=entries, collisions=collisions, walk_complete=True)
