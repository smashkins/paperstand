"""``organize-plan``: a read-only, dry-run preview of the canonical layout.

Mirrors ``parse-report``: same walk, same configuration discovery, same parser.
The only difference is what gets printed for each file — the canonical path it
would get under `<Title>/<YYYY>/<Title> - <ISO date>[ - n<number>].pdf`, or
inside its own declared publication folder, or why it would stay put. Nothing
is ever opened for writing: this command builds names, it does not create,
move, rename or delete a single file.
"""

from __future__ import annotations

import sys
from collections import defaultdict
from pathlib import Path
from typing import TextIO

from paperstand.cli.parse import (
    default_config_path,
    load_cli_config,
    missing_explicit_config,
    parse_file,
)
from paperstand.logging import get_logger
from paperstand.organizer import Unsorted, plan_issue
from paperstand.publication import PublicationIndex
from paperstand.scanner.walker import Walk

log = get_logger(__name__)

__all__ = ["organize_plan"]


def _declared_title_folders(index: PublicationIndex, folders: dict[str, str]) -> dict[str, str]:
    """Every declared title's own folder, keyed by the title it declares.

    Lets a file that is *not itself* inside a declared folder — a date-folder
    file whose configured title a `publication.yml` elsewhere also declares —
    plan into that folder too, the same way the scanner joins it to the same
    title row. Two folders declaring the same title is a mistake worth a
    warning, not a silent pick: the first one found while walking is kept.
    """
    mapping: dict[str, str] = {}
    for folder in folders:
        declared = index.get(folder)
        if declared is None:
            continue
        existing = mapping.get(declared.title)
        if existing is None:
            mapping[declared.title] = declared.folder
        elif existing != declared.folder:
            log.warning(
                "%r is declared by both %s and %s; %s wins",
                declared.title,
                existing,
                declared.folder,
                existing,
            )
    return mapping


def organize_plan(
    directory: Path,
    config_path: Path | None = None,
    out: TextIO | None = None,
) -> int:
    """Print where every PDF under ``directory`` would live under the canonical layout."""
    stream = out or sys.stdout
    root = directory.resolve()
    if not root.is_dir():
        print(f"organize-plan: {directory} is not a directory", file=sys.stderr)
        return 2
    if missing_explicit_config("organize-plan", config_path):
        return 2
    resolved_config = config_path if config_path is not None else default_config_path()
    config = load_cli_config(resolved_config, root)

    print(f"library root:  {root}", file=stream)
    print(f"configuration: {resolved_config or 'auto-discovered'}", file=stream)
    print(file=stream)

    walk = Walk(root, config)
    buffered = list(walk)
    index = PublicationIndex(root)
    index.load_all(walk.publications)
    title_folders = _declared_title_folders(index, walk.publications)

    # Source paths grouped by the canonical path they resolve to, so that two
    # (or more) files landing on the same name can be reported together instead
    # of one silently winning.
    sources_by_canonical: dict[str, list[str]] = defaultdict(list)
    planned = 0
    in_place = 0
    unsorted = 0
    for found in buffered:
        rel_path = found.rel_path
        publication = index.get(found.publication_dir) if found.publication_dir else None
        issue = parse_file(rel_path, root, config, publication)
        if issue is None:
            # Belongs to no configured library: parse-report skips it the same
            # way, since no profile ran on it at all.
            continue
        plan = plan_issue(issue, publication_folder=title_folders.get(issue.title_name))
        if isinstance(plan, Unsorted):
            unsorted += 1
            print(f"{rel_path} -> unsorted: {plan.reason}", file=stream)
            continue
        sources_by_canonical[plan.rel_path].append(rel_path)
        if plan.rel_path == rel_path:
            in_place += 1
            print(f"{rel_path} -> in place", file=stream)
        else:
            planned += 1
            print(f"{rel_path} -> {plan.rel_path}", file=stream)

    collisions = {
        canonical: sources
        for canonical, sources in sources_by_canonical.items()
        if len(sources) > 1
    }
    if collisions:
        print(file=stream)
        for canonical, sources in sorted(collisions.items()):
            print(f"COLLISION {canonical}", file=stream)
            for source in sorted(sources):
                print(f"  {source}", file=stream)

    print(file=stream)
    print(
        f"{planned} planned, {in_place} in place, {unsorted} unsorted, "
        f"{len(collisions)} collision(s)",
        file=stream,
    )
    return 0
