"""Planning, resolving and now performing an issue's move into the canonical layout.

:mod:`paperstand.organizer.naming` is pure and read-only: it turns a
:class:`~paperstand.parsing.ParsedIssue` into the path it would get under the
canonical library layout, nothing else. :mod:`paperstand.organizer.resolve`
is read-only too: it matches an inbox file's own name against the library's
configuration. :mod:`paperstand.organizer.mover` is the first code in this
package that touches the filesystem for anything but reading — one atomic,
never-overwriting move, reused for the library, for `<inbox>/unsorted/` and
for `<inbox>/duplicates/` alike. :mod:`paperstand.organizer.inbox` is the
pipeline that ties all three together into ``paperstand organize``.
"""

from paperstand.organizer.inbox import (
    Duplicate,
    Failed,
    Moved,
    OrganizeReport,
    Outcome,
    Parked,
    Skipped,
    organize_forever,
    organize_once,
)
from paperstand.organizer.mover import (
    DestinationOccupied,
    move_file,
    park,
    remove_sidecar,
    sidecar_path,
)
from paperstand.organizer.naming import (
    CanonicalPath,
    OrganizePlan,
    Unsorted,
    canonical_filename,
    canonical_folder,
    iso_date,
    plan_issue,
)
from paperstand.organizer.resolve import Resolved, Resolver

__all__ = [
    "CanonicalPath",
    "DestinationOccupied",
    "Duplicate",
    "Failed",
    "Moved",
    "OrganizePlan",
    "OrganizeReport",
    "Outcome",
    "Parked",
    "Resolved",
    "Resolver",
    "Skipped",
    "Unsorted",
    "canonical_filename",
    "canonical_folder",
    "iso_date",
    "move_file",
    "organize_forever",
    "organize_once",
    "park",
    "plan_issue",
    "remove_sidecar",
    "sidecar_path",
]
