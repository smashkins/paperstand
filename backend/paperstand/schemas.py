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

    ``hashed`` counts the legacy rows the fast phase has read a file for, to
    learn the content hash they never had — the only feedback while the
    one-time backfill after an upgrade works through a whole library.
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
    db_ok: bool
    last_scan: ScanRecord | None
    scanning: bool
    issue_count: int
