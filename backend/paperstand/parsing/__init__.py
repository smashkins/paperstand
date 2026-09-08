"""Turning a path into a catalogued issue.

No naming rule lives in this package's code: the modules here are a generic
engine — cleaning, dates, numbers, titles — that executes a **profile**, a
declarative YAML description of the naming shapes a library uses. Paperstand
ships one profile, ``default``; a library can extend it or replace it.
"""

from paperstand.parsing.engine import UNSORTED_TITLE, ParsedIssue, Parser, Trace
from paperstand.parsing.parse import explain_path, get_parser, parse_path
from paperstand.parsing.profile import (
    DEFAULT_PROFILE,
    Profile,
    ProfileError,
    load_profile,
    load_profiles,
)

__all__ = [
    "DEFAULT_PROFILE",
    "UNSORTED_TITLE",
    "ParsedIssue",
    "Parser",
    "Profile",
    "ProfileError",
    "Trace",
    "explain_path",
    "get_parser",
    "load_profile",
    "load_profiles",
    "parse_path",
]
