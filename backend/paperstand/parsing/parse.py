"""The public entry point of the parser.

``parse_path`` is pure: it takes a path relative to the library root, the library
it belongs to, the profile to execute and the file's modification time, and it
returns a :class:`~paperstand.parsing.engine.ParsedIssue`. It reads nothing from
disk, which is what makes the whole fixture table testable without a library.
"""

from __future__ import annotations

import datetime as dt
from collections import OrderedDict
from typing import TYPE_CHECKING

from paperstand.parsing.engine import ParsedIssue, Parser, Trace
from paperstand.parsing.profile import Profile

if TYPE_CHECKING:  # pragma: no cover - import cycle only matters to type checkers
    from paperstand.config import LibraryConfig
    from paperstand.publication import DeclaredPublication

__all__ = ["ParsedIssue", "Parser", "explain_path", "get_parser", "parse_path"]

#: Compiled parsers, keyed by the identity of the profile and library they serve.
_PARSERS: OrderedDict[tuple[int, int], Parser] = OrderedDict()
_MAX_PARSERS = 32


def get_parser(profile: Profile, library: LibraryConfig) -> Parser:
    """Return a compiled parser for a profile and a library, building it once.

    Compiling the regexes of a profile costs far more than parsing a name, so
    scanning a library of thousands of files must not pay for it every time.
    """
    key = (id(profile), id(library))
    parser = _PARSERS.get(key)
    if parser is not None and parser.profile is profile and parser.library is library:
        _PARSERS.move_to_end(key)
        return parser
    parser = Parser(profile, library)
    _PARSERS[key] = parser
    _PARSERS.move_to_end(key)
    while len(_PARSERS) > _MAX_PARSERS:
        _PARSERS.popitem(last=False)
    return parser


def parse_path(
    rel_path: str,
    library: LibraryConfig,
    profile: Profile,
    mtime: dt.datetime | dt.date,
    publication: DeclaredPublication | None = None,
) -> ParsedIssue:
    """Parse ``rel_path`` — relative to the library root — into an issue.

    ``publication`` is the declaration whose folder is the nearest ancestor
    of ``rel_path``, when there is one — a per-call argument, never part of
    the compiled parser itself. See :meth:`paperstand.parsing.engine.Parser.parse`.
    """
    return get_parser(profile, library).parse(rel_path, mtime, publication=publication)


def explain_path(
    rel_path: str,
    library: LibraryConfig,
    profile: Profile,
    mtime: dt.datetime | dt.date,
    publication: DeclaredPublication | None = None,
) -> tuple[ParsedIssue, Trace]:
    """Parse ``rel_path`` and return every step that was taken, in order."""
    trace: Trace = []
    issue = get_parser(profile, library).parse(
        rel_path, mtime, publication=publication, trace=trace
    )
    return issue, trace
