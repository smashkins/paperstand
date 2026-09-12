"""The shapes the API answers with.

Every response model lives here, in one file, because they are the contract three
things depend on at once: the routers, the OPDS feed and the TypeScript client
generated from the OpenAPI document. A field added here shows up in
``frontend/src/lib/api/types.gen.ts`` the next time ``make gen-api`` runs.

Two conventions run through the models:

* **URLs are root-relative.** ``/api/issues/<id>/file``, never an absolute one.
  The frontend is served from the same origin, and OPDS — which does need
  absolute URLs — makes them from ``PAPERSTAND_BASE_URL``.
* **Dates are strings.** ``issue_date`` is ``YYYY-MM-DD`` when the precision is a
  day, and the first day of the month or of the year when it is not; the
  ``date_precision`` field says which. Keeping them as strings is what the
  database stores and what a calendar keyed by day wants.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

Kind = Literal["newspaper", "magazine"]
DatePrecision = Literal["day", "month", "year", "none"]
DateSource = Literal["filename", "mixed", "folder", "mtime", "none"]
TitleSource = Literal["config", "folder", "filename", "pattern", "unsorted", "publication"]
Frequency = Literal["daily", "weekly", "monthly", "irregular"]
IssueKey = Literal["date", "number", "date+number"]
IssueSort = Literal["date_desc", "date_asc", "added_desc"]
TitleSort = Literal["name", "latest"]
ScanPhase = Literal["catalogue", "covers"]


class Aspect(BaseModel):
    """The page box of an issue, in PDF points.

    Present as soon as the scanner has opened the file; ``null`` until then, so a
    caller that wants to reserve the right amount of space for a cover has to
    cope with not knowing it yet.
    """

    page_w: float
    page_h: float


class Progress(BaseModel):
    """Where the reader got to in an issue."""

    page: int
    page_count: int | None = None
    updated_at: str


class ProgressRecord(BaseModel):
    """One row of the progress table, as its own endpoints return it."""

    issue_id: str
    page: int
    page_count: int | None = None
    updated_at: str


class ProgressUpdate(BaseModel):
    """The body of ``PUT /api/issues/{id}/progress``."""

    page: int = Field(ge=1, description="1-based page number; clamped to the issue's page count")


class Issue(BaseModel):
    """One issue: a PDF in the library, as the catalogue understands it."""

    id: str
    title_id: str
    title_name: str
    library_id: str
    kind: Kind
    issue_date: str | None = None
    date_precision: DatePrecision
    date_source: DateSource
    issue_number: str | None = None
    volume: int | None = None
    variant: str | None = None
    label: str
    filename: str
    rel_path: str
    size: int
    content_hash: str | None = None
    page_count: int | None = None
    aspect: Aspect | None = None
    cover_url: str
    thumb_url: str
    file_url: str
    added_at: str
    is_duplicate: bool
    missing_since: str | None = None
    """When a scan first failed to find this issue's file; ``null`` while it
    is present. Set, the issue is hidden everywhere a reader looks — the
    storefront, the calendar, search, OPDS — until the file returns or the
    grace period (``PAPERSTAND_MISSING_GRACE_DAYS``) elapses and the row is
    forgotten. Reachable through ``GET /api/issues?missing=true`` and at its
    own detail URL throughout."""
    cover_error: str | None = None
    """The renderer's own message when it could not open this file at all;
    ``null`` while ``cover_status`` is ``pending`` or ``ok``. May name the
    file's path under the library root. Reachable through
    ``GET /api/issues?unreadable=true``."""
    progress: Progress | None = None


class IssueDetail(Issue):
    """An issue, plus everything only its own page needs."""

    derived_title: str
    matched_rule: str
    prev_issue_id: str | None = None
    next_issue_id: str | None = None
    pages_url_template: str


class IssuePage(BaseModel):
    """A page of issues, with the total the filters matched."""

    items: list[Issue]
    total: int


class Title(BaseModel):
    """A periodical: the issues of one name inside one library."""

    id: str
    name: str
    library_id: str
    kind: Kind
    source: TitleSource
    slug: str | None = None
    frequency: Frequency | None = None
    language: str | None = None
    issue_key: IssueKey | None = None
    parent_slug: str | None = None
    supplements: list[str] | None = None
    issue_count: int
    latest_issue: Issue | None = None
    first_date: str | None = None
    last_date: str | None = None
    cover_url: str | None = None
    thumb_url: str | None = None


class YearCount(BaseModel):
    """How many issues of a title carry a date in one year."""

    year: int
    count: int


class TitleDetail(Title):
    """A title, plus the years its issues fall in, newest first."""

    years: list[YearCount]


class Calendar(BaseModel):
    """One year of a title, as a day → issue map."""

    year: int
    years: list[YearCount]
    days: dict[str, str]


class Library(BaseModel):
    """One configured library and what the catalogue holds for it."""

    id: str
    name: str
    path: str
    kind: Kind
    title_count: int
    issue_count: int
    unsorted_count: int


class Stats(BaseModel):
    """What the catalogue holds and what it costs on disk."""

    libraries: list[Library]
    title_count: int
    issue_count: int
    duplicate_count: int
    missing_count: int
    covers_bytes: int
    pages_bytes: int
    db_bytes: int


class Today(BaseModel):
    """The storefront: one day of newspapers, and what to read next."""

    date: str
    is_today: bool
    newspapers_date: str | None = None
    newspapers: list[Issue]
    magazines: list[Issue]
    continue_reading: list[Issue]
    recently_added: list[Issue]


class ScanAccepted(BaseModel):
    """What ``POST /api/scan`` answers with when it accepts."""

    scan_id: int


class ScanProgress(BaseModel):
    """A scan already in progress, as ``GET /api/scan/status`` reports it.

    ``covers_total`` is ``null`` during the ``catalogue`` phase — the fast
    phase never counts the files it will find before it has walked them —
    and known from the first ``covers`` snapshot onward. It has no default:
    every snapshot carries it explicitly, ``null`` or not, so the generated
    client sees a field that is always there rather than one that might be
    missing.

    ``covers_done`` counts successes only; a cover the pool could not render
    is ``covers_failed`` instead, so a bar or an "N of M" line wants
    ``covers_done + covers_failed`` to reach ``covers_total`` even when some
    of that N were failures.

    ``hashed`` counts every file this scan has read in full to compute a
    content hash — a brand new file, a legacy row backfilled once, a touch
    and a replacement alike. Right after an upgrade this is nearly every
    file in the library, which is the only feedback while that one-time
    backfill works through it; a later scan hashes only what actually
    changed.

    ``missing`` counts rows whose file this scan did not find but did not
    remove either, because they are within
    ``PAPERSTAND_MISSING_GRACE_DAYS`` of the scan that first noticed —
    known only once the fast phase is nearly done, so it reads ``0`` on
    every snapshot before that.
    """

    scan_id: int
    phase: ScanPhase
    started_at: str
    elapsed: float
    files_seen: int
    added: int
    updated: int
    removed: int
    errors: int
    hashed: int
    missing: int
    covers_done: int
    covers_failed: int
    covers_total: int | None


class ScanSummary(BaseModel):
    """A finished scan, as the scheduler remembers it in memory."""

    scan_id: int
    status: str
    files_seen: int
    added: int
    updated: int
    removed: int
    covers_done: int
    errors: int
    missing: int
    message: str | None
    duration: float


class ScanStatus(BaseModel):
    """What ``GET /api/scan/status`` answers with.

    ``current`` is ``null`` when the scheduler is idle. Between a scan being
    accepted and its first snapshot it is a ``catalogue`` snapshot with every
    counter at zero, never ``null`` while ``running`` is ``true``. Neither
    field carries a default — every caller passes both explicitly — so the
    client sees them as always-present-but-nullable, not possibly missing.
    """

    running: bool
    current: ScanProgress | None
    last: ScanSummary | None


class ScanRecord(BaseModel):
    """One row of the ``scans`` table, the only place the timestamps live."""

    id: int
    started_at: str | None = None
    finished_at: str | None = None
    status: str | None = None
    files_seen: int | None = None
    added: int | None = None
    updated: int | None = None
    removed: int | None = None
    covers_done: int | None = None
    errors: int | None = None
    missing: int | None = None
    message: str | None = None


class HealthResponse(BaseModel):
    """What ``GET /api/health`` answers with.

    ``last_scan`` has no default: the endpoint always passes it explicitly,
    ``null`` or a record, so the client sees a field that is always present.
    """

    status: str
    version: str
    library_path: str
    library_ok: bool
    library_marker: bool | None
    """The root marker: ``null`` when no scan has ever seen one (protection
    not set up), ``true`` when the file is present, ``false`` when a scan
    remembered it and it is gone now — the case that makes ``library_ok``
    false even though the root itself is a directory."""
    db_ok: bool
    last_scan: ScanRecord | None
    scanning: bool
    issue_count: int


class OrganizerMove(BaseModel):
    """One file the organizer moved, or would move, in one run."""

    source: str
    """Inbox-relative path the file was found at."""
    destination: str
    """Library-relative path it was moved to."""


class OrganizerParked(BaseModel):
    """One file sitting under ``unsorted/`` or ``duplicates/`` at the end of a run.

    A listing, not a diff: every ``*.pdf`` under either folder, recursively,
    whether this run put it there or an earlier one did.
    """

    folder: Literal["unsorted", "duplicates"]
    name: str
    """Path relative to ``folder``."""
    reason: str | None = None
    """The first line of the sidecar, stripped; ``null`` when there is none."""
    size: int
    modified: str


class OrganizerRun(BaseModel):
    """What one organizer run did, or would do, written to ``<data>/organizer/last-run.json``.

    Read back by the server on every ``GET /api/maintenance`` — a few
    kilobytes, no caching, no watching. ``version`` guards the shape: a file
    from a future Paperstand is ignored rather than misread.
    """

    version: Literal[1] = 1
    started_at: str
    finished_at: str
    mode: Literal["apply", "dry-run"]
    inbox: str
    """The inbox's absolute path, as printed in the run's own header."""
    refused: bool
    """The library's root marker was remembered but not there: nothing walked."""
    moved: int
    duplicate: int
    unsorted: int
    skipped: int
    failed: int
    moves: list[OrganizerMove]
    """This run's ``Moved`` outcomes."""
    parked: list[OrganizerParked]
    """The inventory of ``unsorted/`` and ``duplicates/`` after the run."""
    scan_requested: bool
    """Whether the trigger file was touched at the end of this run."""


class MigrationCollision(BaseModel):
    """Two or more files that would migrate onto the same canonical path.

    A group is never moved, not even its first member: half-migrated groups
    would hide the very conflict this block reports. ``sources`` is sorted,
    at least two entries, and includes a source already in place at
    ``destination`` when one is a member of the group.
    """

    destination: str
    sources: list[str]


class MigrationLeftInPlace(BaseModel):
    """One file ``migrate`` left exactly where it was, and why.

    Covers every outcome but ``moved`` and ``in place``: unsorted, duplicate,
    collided and failed each contribute one entry here, in the reason's own
    words.
    """

    rel_path: str
    reason: str


class MigrationRun(BaseModel):
    """What one ``migrate`` run did, written under ``<data>/organizer/migrations/``.

    One file per applied run that moved at least a file — never overwritten,
    unlike :class:`OrganizerRun`. ``version`` guards the shape the same way.
    """

    version: Literal[1] = 1
    started_at: str
    finished_at: str
    library: str
    """The library root's absolute path, as printed in the run's own header."""
    moved: int
    in_place: int
    unsorted: int
    collision: int
    duplicate: int
    failed: int
    moves: list[OrganizerMove]
    """This run's ``Moved`` outcomes, both paths library-relative."""
    collisions: list[MigrationCollision]
    left_in_place: list[MigrationLeftInPlace]
    removed_folders: list[str]
    """Folders a move left empty, and this run removed, deepest first."""
    scan_requested: bool
    """Whether the trigger file was touched at the end of this run."""


class NumberGap(BaseModel):
    """A hole between two consecutive catalogued issue numbers of one title."""

    volume: int | None
    first: int
    last: int


class TitleGaps(BaseModel):
    """One title's holes: in its numbering, in its declared cadence, or both."""

    title_id: str
    title_name: str
    kind: Kind
    frequency: Frequency | None
    issue_key: IssueKey | None
    first_date: str | None
    last_date: str | None
    issue_count: int
    overdue_days: int | None
    """Days since the last qualifying issue, past one period; ``null`` when
    the title is not declared with a cadence, or is not overdue."""
    date_gaps: list[str]
    """Missing periods, newest first, capped at ``gaps.GAP_LIST_LIMIT``."""
    date_gap_count: int
    """How many periods are missing in total; always complete, even capped."""
    number_gaps: list[NumberGap]
    """Ranges of missing numbers, capped at ``gaps.GAP_LIST_LIMIT``."""
    number_gap_count: int
    """How many numbers are missing in total; always complete, even capped."""


class UnsortedBucket(BaseModel):
    """How many files the parser could not place, in one library.

    A different thing from the organizer's own ``unsorted/`` folder: this is
    the catalogue's own ``Unsorted`` title, per library.
    """

    title_id: str
    library_id: str
    library_name: str
    count: int


class MaintenanceSummary(BaseModel):
    """What ``GET /api/maintenance`` answers with: the top-bar badge and the
    page's own header."""

    missing_count: int
    missing_grace_days: int
    unreadable_count: int
    unsorted: list[UnsortedBucket]
    unsorted_count: int
    organizer: OrganizerRun | None
    """The organizer's last run, or ``null`` when it has never run here."""
    attention: int
    """``missing_count + unreadable_count + len(organizer.parked)``: what a
    person can act on now. Gaps are not counted — they are information, not
    a backlog."""
