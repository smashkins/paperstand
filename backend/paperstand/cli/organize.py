"""``organize-plan``: a read-only, dry-run preview of the canonical layout.

Mirrors ``parse-report``: same walk, same configuration discovery, same parser.
The only difference is what gets printed for each file — the canonical path it
would get under `<Title>/<YYYY>/<Title> - <ISO date>[ - n<number>].pdf`, or why
it would stay put. Nothing is ever opened for writing: this command builds
names, it does not create, move, rename or delete a single file.
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
from paperstand.organizer import Unsorted, plan_issue
from paperstand.scanner.walker import iter_library_files

__all__ = ["organize_plan"]


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

    # Source paths grouped by the canonical path they resolve to, so that two
    # (or more) files landing on the same name can be reported together instead
    # of one silently winning.
    sources_by_canonical: dict[str, list[str]] = defaultdict(list)
    planned = 0
    unsorted = 0
    for rel_path in iter_library_files(root, config):
        issue = parse_file(rel_path, root, config)
        if issue is None:
            # Belongs to no configured library: parse-report skips it the same
            # way, since no profile ran on it at all.
            continue
        plan = plan_issue(issue)
        if isinstance(plan, Unsorted):
            unsorted += 1
            print(f"{rel_path} -> unsorted: {plan.reason}", file=stream)
            continue
        planned += 1
        sources_by_canonical[plan.rel_path].append(rel_path)
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
    print(f"{planned} planned, {unsorted} unsorted, {len(collisions)} collision(s)", file=stream)
    return 0
