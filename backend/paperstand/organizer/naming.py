"""Building the canonical name of an already-parsed issue.

This is naming *construction*, the mirror image of the naming *interpretation*
that lives in ``paperstand.parsing``: it takes the :class:`~paperstand.parsing.ParsedIssue`
the parser already produced and works out the folder and file name the issue
would get under the canonical layout, `<Title>/<YYYY>/<Title> - <ISO date>[ -
n<number>].pdf`. It never reads a file name apart, so it does not touch the "no
naming rule lives in Python" rule, which is about interpreting names, not
building them.

Every function here is pure: no filesystem access, nothing that depends on the
current time. A :class:`ParsedIssue` the parser cannot place with confidence is
reported as :class:`Unsorted`, with a reason, rather than guessed at.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass

from paperstand.parsing import ParsedIssue

__all__ = [
    "CanonicalPath",
    "OrganizePlan",
    "Unsorted",
    "canonical_filename",
    "canonical_folder",
    "iso_date",
    "plan_issue",
]


@dataclass(frozen=True, slots=True)
class CanonicalPath:
    """Where an issue would live under the canonical layout, relative to the library root."""

    folder: str
    filename: str

    @property
    def rel_path(self) -> str:
        """The folder and the file name, joined as a POSIX relative path."""
        return f"{self.folder}/{self.filename}"


@dataclass(frozen=True, slots=True)
class Unsorted:
    """An issue the organizer would leave exactly where it is, and why."""

    reason: str


#: Either a canonical destination, or why the issue is left where it is.
OrganizePlan = CanonicalPath | Unsorted


def iso_date(issue_date: dt.date, precision: str) -> str:
    """ISO 8601 rendering of ``issue_date`` at its parsed precision.

    ``day`` keeps the full date, ``month`` drops the day, ``year`` drops the
    month too — the precision is exactly what the name is allowed to claim.
    """
    if precision == "day":
        return issue_date.isoformat()
    if precision == "month":
        return issue_date.isoformat()[:7]
    if precision == "year":
        return f"{issue_date.year:04d}"
    raise ValueError(f"a date of precision {precision!r} cannot be rendered")


def canonical_folder(title: str, year: int) -> str:
    """The `<Title>/<YYYY>` folder an issue's files are grouped under."""
    return f"{title}/{year:04d}"


def canonical_filename(title: str, date: str, issue_number: int | None) -> str:
    """The `<Title> - <ISO date>[ - n<number>].pdf` file name of an issue."""
    name = f"{title} - {date}"
    if issue_number is not None:
        name += f" - n{issue_number}"
    return f"{name}.pdf"


def _is_safe_component(name: str) -> bool:
    """Whether ``name`` is usable, on its own, as one folder or file name.

    Rejects an empty or whitespace-only string, a name carrying a path
    separator (which would smuggle in extra folders, or escape the library
    root), and the two special components `.` and `..`. A *configured* title
    is a free string (see ``TitleConfig.name``), so this cannot be assumed —
    a title derived from a file name is not checked, since it can never
    contain a separator in the first place.
    """
    return bool(name.strip()) and "/" not in name and name not in (".", "..")


def plan_issue(issue: ParsedIssue) -> OrganizePlan:
    """Where ``issue`` would live under the canonical layout, or why it would not.

    Checked in this order, the first that applies wins:

    1. no configured title matched the name at all;
    2. the parser found no date anywhere in the name or the folder;
    3. the only date found came from the file's modification time, a guess the
       canonical name must never bake in as if it were read off the file;
    4. the title itself is not a single, safe folder and file name component —
       empty or blank, `.` or `..`, or containing a path separator.
    """
    if issue.title_source == "unsorted":
        return Unsorted(reason=f'no configured title matches "{issue.derived_title}"')
    if issue.date_precision == "none" or issue.issue_date is None:
        return Unsorted(reason="no date in the name or the folder")
    if issue.date_source == "mtime":
        return Unsorted(reason="date would come from the file's modification time")
    if not _is_safe_component(issue.title_name):
        return Unsorted(reason=f'title "{issue.title_name}" is not a valid folder name')
    date = iso_date(issue.issue_date, issue.date_precision)
    folder = canonical_folder(issue.title_name, issue.issue_date.year)
    filename = canonical_filename(issue.title_name, date, issue.issue_number)
    return CanonicalPath(folder=folder, filename=filename)
