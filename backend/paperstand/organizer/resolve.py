"""Resolving an inbox file's own name against the library's configuration.

Built once per organizer run, from the configuration and every folder a
stat-only walk of the *library* already finds declaring a publication:
:class:`Resolver` augments each configured library's ``titles`` with the
titles declared under it that ``paperstand.yml`` does not already name,
builds a parser for each of those augmented copies exactly once, and then
resolves every inbox file by its own name alone against every library in
turn — never against a folder of the inbox, which may hold anything, and
never by opening the file itself.

A name resolves when exactly one library reads it as belonging to a
*configured* title. None, or more than one, is
:class:`~paperstand.organizer.naming.Unsorted`, with a reason naming the
derived title or every library that claims it.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

from paperstand.config import LibraryConfig, PaperstandConfig, TitleConfig
from paperstand.logging import get_logger
from paperstand.organizer.naming import CanonicalPath, Unsorted, plan_issue
from paperstand.parsing import ParsedIssue, parse_path
from paperstand.publication import PublicationIndex
from paperstand.scanner.walker import Walk

log = get_logger(__name__)

__all__ = ["Resolved", "Resolver", "declared_title_folders"]


@dataclass(frozen=True, slots=True)
class Resolved:
    """An inbox file name, matched to exactly one configured library."""

    library: LibraryConfig
    """The library as configured — never the resolver's augmented copy."""

    issue: ParsedIssue

    destination: CanonicalPath


def declared_title_folders(
    index: PublicationIndex, folders: dict[str, str], config: PaperstandConfig
) -> dict[tuple[str, str], str]:
    """Every declared title's own folder, keyed by (library name, title).

    Lets a file that is *not itself* inside a declared folder — a date-folder
    file whose configured title a `publication.yml` elsewhere also declares —
    plan into that folder too, the same way the scanner joins it to the same
    title row. Keyed by the declaring library as well as the title, so two
    libraries that happen to declare the same title never plan across a
    library boundary; a folder belonging to no configured library declares
    nothing here. Two folders in the *same* library declaring the same title
    is a mistake worth a warning, not a silent pick: the first one found
    while walking is kept.

    Shared by ``organize-plan`` (:mod:`paperstand.cli.organize`) and
    :class:`Resolver`, so there is exactly one copy of this map.
    """
    mapping: dict[tuple[str, str], str] = {}
    for folder in folders:
        declared = index.get(folder)
        if declared is None:
            continue
        library = config.library_for(folder)
        if library is None:
            continue
        key = (library.name, declared.title)
        existing = mapping.get(key)
        if existing is None:
            mapping[key] = declared.folder
        elif existing != declared.folder:
            log.warning(
                "%r is declared by both %s and %s in library %r; %s wins",
                declared.title,
                existing,
                declared.folder,
                library.name,
                existing,
            )
    return mapping


def _augmented(library: LibraryConfig, title_folders: dict[tuple[str, str], str]) -> LibraryConfig:
    """A copy of ``library`` whose titles also cover its declared folders.

    Every publication declared somewhere under this library that
    ``paperstand.yml`` does not already name becomes a configured title of
    its own, aliased to its folder's basename when that differs from the
    declared display title. A folder whose title is already configured
    instead has its basename merged into that title's own aliases — either
    way, the canonical file name is spelled after the folder, so the parser
    must recognise both spellings, even for a title ``paperstand.yml``
    already names.
    """
    titles = list(library.titles)
    by_name = {title.name: index for index, title in enumerate(titles)}
    for (owner, title), folder in title_folders.items():
        if owner != library.name:
            continue
        basename = PurePosixPath(folder).name
        index = by_name.get(title)
        if index is None:
            titles.append(TitleConfig(name=title, aliases=[basename] if basename != title else []))
            continue
        if basename == title or basename in titles[index].aliases:
            continue
        titles[index] = titles[index].model_copy(
            update={"aliases": [*titles[index].aliases, basename]}
        )
    return library.model_copy(update={"titles": titles})


class Resolver:
    """Resolves inbox file names against one run's configuration, once.

    ``library_root`` is walked once, at construction, purely to discover
    which folders declare a publication — the same stat-only walk the
    scanner and ``organize-plan`` already do; no PDF is ever opened by this
    class. Nothing under the *inbox* reaches it: :meth:`resolve` takes a
    bare file name and tries it, in turn, as if it sat directly under each
    configured library.
    """

    def __init__(self, library_root: Path, config: PaperstandConfig) -> None:
        self._config = config
        walk = Walk(library_root, config)
        list(walk)  # drain it: `.publications` only fills as the walk runs
        index = PublicationIndex(library_root)
        index.load_all(walk.publications)
        self._title_folders = declared_title_folders(index, walk.publications, config)
        # Built once, and reused for every call to `resolve`: `parse.get_parser`
        # caches a compiled parser by `(id(profile), id(library))`, so a fresh
        # copy per file would recompile the profile every time.
        self._augmented: dict[str, LibraryConfig] = {
            library.name: _augmented(library, self._title_folders) for library in config.libraries
        }

    def resolve(self, filename: str, mtime: dt.datetime) -> Resolved | Unsorted:
        """Where ``filename`` — an inbox file's own name, nothing else — would
        live under the canonical layout, or why it cannot be placed there."""
        matches: list[tuple[LibraryConfig, ParsedIssue]] = []
        derived_title: str | None = None
        for library in self._config.libraries:
            rel_path = f"{library.path.strip('/')}/{filename}"
            issue = parse_path(
                rel_path,
                self._augmented[library.name],
                self._config.profile_for(library),
                mtime,
            )
            if derived_title is None:
                derived_title = issue.derived_title
            if issue.title_source == "config":
                matches.append((library, issue))

        if not matches:
            hint = derived_title if derived_title is not None else Path(filename).stem
            return Unsorted(
                reason=(
                    f'no declared title matches "{hint}" '
                    "(declare it in paperstand.yml or in a publication.yml)"
                )
            )
        if len(matches) > 1:
            title = matches[0][1].title_name
            names = ", ".join(library.name for library, _ in matches)
            return Unsorted(reason=f'"{title}" is declared in more than one library ({names})')

        library, issue = matches[0]
        plan = plan_issue(
            issue,
            publication_folder=self._title_folders.get((library.name, issue.title_name)),
            library_path=library.path,
        )
        if isinstance(plan, Unsorted):
            return plan
        return Resolved(library=library, issue=issue, destination=plan)
