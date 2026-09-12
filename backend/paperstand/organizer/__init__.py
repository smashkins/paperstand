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
:mod:`paperstand.organizer.migration` plans and performs the same kind of
move for files already inside the library, into ``paperstand migrate``.

:mod:`~paperstand.organizer.migration` has its own ``Moved``, ``Duplicate``
and ``Failed`` outcomes — same idea as this package's, but keyed by a
library-relative ``rel_path`` rather than an inbox-relative ``source`` — so
they are not re-exported here under the same bare names; import them from
that module directly.
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
from paperstand.organizer.migration import (
    Collided,
    Collision,
    InPlace,
    LibraryPlan,
    MigrationReport,
    Move,
    Unplaced,
    migrate_once,
    plan_library,
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
from paperstand.organizer.report import inventory, read_run, write_run
from paperstand.organizer.resolve import Resolved, Resolver

__all__ = [
    "CanonicalPath",
    "Collided",
    "Collision",
    "DestinationOccupied",
    "Duplicate",
    "Failed",
    "InPlace",
    "LibraryPlan",
    "MigrationReport",
    "Move",
    "Moved",
    "OrganizePlan",
    "OrganizeReport",
    "Outcome",
    "Parked",
    "Resolved",
    "Resolver",
    "Skipped",
    "Unplaced",
    "Unsorted",
    "canonical_filename",
    "canonical_folder",
    "inventory",
    "iso_date",
    "migrate_once",
    "move_file",
    "organize_forever",
    "organize_once",
    "park",
    "plan_issue",
    "plan_library",
    "read_run",
    "remove_sidecar",
    "sidecar_path",
    "write_run",
]
