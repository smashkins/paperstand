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
from pathlib import PurePosixPath

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


def canonical_filename(
    title: str,
    date: str,
    issue_number: int | None,
    volume: int | None = None,
    variant: str | None = None,
) -> str:
    """The `<Title> - <ISO date>[ - [v<volume> ]n<number>][ - <variant>].pdf` file name.

    ``volume`` is written only alongside a number — a volume with no issue to
    go with it is not something the canonical grammar can read back, so it is
    silently dropped rather than written where it could not be parsed again.
    """
    name = f"{title} - {date}"
    if issue_number is not None:
        if volume is not None:
            name += f" - v{volume} n{issue_number}"
        else:
            name += f" - n{issue_number}"
    if variant:
        name += f" - {variant}"
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


def plan_issue(
    issue: ParsedIssue,
    *,
    publication_folder: str | None = None,
    library_path: str | None = None,
) -> OrganizePlan:
    """Where ``issue`` would live under the canonical layout, or why it would not.

    ``publication_folder``, when given, is the folder — relative to the library
    root — of the declared publication ``issue``'s title belongs to. The issue
    then plans into ``<publication_folder>/<YYYY>/``, and the *folder's own*
    basename, not ``issue.title_name`` (which may be the yml's display title),
    goes into the file name: a file already inside its declared folder, named
    after the folder the way the grammar requires, plans onto itself.

    ``library_path``, when given and there is no ``publication_folder``, is the
    path — relative to the library root — of the library ``issue``'s title is
    configured in: an *undeclared* configured title then plans into
    ``<library_path>/<Title>/<YYYY>/`` rather than at the library root, which a
    configured title is never inside. Ignored once ``publication_folder`` is
    given, since a declared folder already says exactly where the issue goes.
    ``None`` (the default for both) keeps today's plain ``<Title>/<YYYY>/``.

    Checked in this order, the first that applies wins:

    1. no configured title matched the name at all;
    2. the parser found no date anywhere in the name or the folder;
    3. the only date found came from the file's modification time, a guess the
       canonical name must never bake in as if it were read off the file;
    4. the title itself is not a single, safe folder and file name component —
       empty or blank, `.` or `..`, or containing a path separator;
    5. the variant itself contains ` - `, which the canonical grammar reads as
       a field separator: writing it verbatim would produce a name the parser
       could never read back to the same variant.
    """
    if issue.title_source == "unsorted":
        return Unsorted(reason=f'no configured title matches "{issue.derived_title}"')
    if issue.date_precision == "none" or issue.issue_date is None:
        return Unsorted(reason="no date in the name or the folder")
    if issue.date_source == "mtime":
        return Unsorted(reason="date would come from the file's modification time")
    title = (
        PurePosixPath(publication_folder).name
        if publication_folder is not None
        else issue.title_name
    )
    if not _is_safe_component(title):
        return Unsorted(reason=f'title "{title}" is not a valid folder name')
    if issue.variant is not None and " - " in issue.variant:
        return Unsorted(
            reason=f'variant "{issue.variant}" contains " - ", which a canonical name '
            "could not read back"
        )
    date = iso_date(issue.issue_date, issue.date_precision)
    if publication_folder is not None:
        folder = f"{publication_folder}/{issue.issue_date.year:04d}"
    else:
        folder = canonical_folder(title, issue.issue_date.year)
        if library_path:
            folder = f"{library_path.strip('/')}/{folder}"
    filename = canonical_filename(title, date, issue.issue_number, issue.volume, issue.variant)
    return CanonicalPath(folder=folder, filename=filename)
