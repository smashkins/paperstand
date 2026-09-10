"""Working out the canonical name of an issue, read-only.

This package never writes, moves, renames or deletes anything: it turns a
:class:`~paperstand.parsing.ParsedIssue` into the path it would get under the
canonical library layout. See :mod:`paperstand.organizer.naming`.
"""

from paperstand.organizer.naming import (
    CanonicalPath,
    OrganizePlan,
    Unsorted,
    canonical_filename,
    canonical_folder,
    iso_date,
    plan_issue,
)

__all__ = [
    "CanonicalPath",
    "OrganizePlan",
    "Unsorted",
    "canonical_filename",
    "canonical_folder",
    "iso_date",
    "plan_issue",
]
