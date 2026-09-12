"""Every read the API makes, as parameterised SQL.

The routers do not write SQL: they call the functions here and get schema
objects back. That is deliberate — the OPDS feed answers the same
questions the REST API does, and both have to answer them the same way, down to
which issues a duplicate hides and how ties are broken.

Four rules are applied consistently, and are the reason most of these functions
take a flag rather than being written twice:

* **Duplicates are hidden by default.** A row with ``duplicate_of`` set is the
  same issue as another one, so it does not appear in lists, is not counted and
  never wins a calendar day.
* **Issues sort by date, then by file name.** The date can be null and the file
  name never is, so the pair is a total order and ``prev``/``next`` can be
  resolved with one comparison.
* **The plain issue wins over a same-day supplement.** Where exactly one row may
  represent a title on a day — the calendar, the newspapers of ``/api/today``,
  each title's latest issue — a row with no ``variant`` is preferred first, so a
  Weekend edition never hides the daily it shares its date with.
* **A further tie is broken by what arrived last.** Once the plain-issue
  preference is applied, the most recently added non-duplicate wins.
"""

from __future__ import annotations

import datetime as dt
import json
import sqlite3
from collections.abc import Sequence
from typing import Any, NamedTuple
from urllib.parse import quote

from paperstand.cache import COVER_VERSION, PAGE_VERSION
from paperstand.db import utc_now
from paperstand.schemas import (
    Aspect,
    Calendar,
    Issue,
    IssueSort,
    Library,
    Progress,
    ProgressRecord,
    Title,
    TitleDetail,
    TitleSort,
    YearCount,
)

#: The columns every issue response is built from.
ISSUE_COLUMNS = """
    i.id AS id,
    i.library_id AS library_id,
    i.title_id AS title_id,
    t.name AS title_name,
    t.sort_name AS sort_name,
    t.kind AS kind,
    i.rel_path AS rel_path,
    i.filename AS filename,
    i.size AS size,
    i.content_hash AS content_hash,
    i.mtime_ns AS mtime_ns,
    i.issue_date AS issue_date,
    i.date_precision AS date_precision,
    i.date_source AS date_source,
    i.issue_number AS issue_number,
    i.volume AS volume,
    i.variant AS variant,
    i.derived_title AS derived_title,
    i.label AS label,
    i.matched_rule AS matched_rule,
    i.duplicate_of AS duplicate_of,
    i.missing_since AS missing_since,
    i.page_count AS page_count,
    i.page_w AS page_w,
    i.page_h AS page_h,
    i.added_at AS added_at,
    p.page AS progress_page,
    p.page_count AS progress_page_count,
    p.updated_at AS progress_updated_at
"""

ISSUE_FROM = """
FROM issues i
JOIN titles t ON t.id = i.title_id
LEFT JOIN reading_progress p ON p.issue_id = i.id
"""

ISSUE_SELECT = f"SELECT {ISSUE_COLUMNS} {ISSUE_FROM}"

#: ``sort`` values of ``GET /api/issues`` and the ``ORDER BY`` behind each.
ISSUE_ORDER: dict[IssueSort, str] = {
    "date_desc": "i.issue_date DESC, i.filename DESC",
    "date_asc": "i.issue_date ASC, i.filename ASC",
    "added_desc": "i.added_at DESC, i.id DESC",
}

#: Newest first, for the windowed "one row per title" queries. A supplement
#: sharing its date with the plain issue never wins the tie: `i.variant IS
#: NULL` sorts true (the plain issue) before false, so it is picked first.
LATEST_FIRST = "i.issue_date DESC, i.variant IS NULL DESC, i.filename DESC"

#: A title somebody would call a periodical, as opposed to the ``Unsorted``
#: bucket the parser uses for the files it could not place.
REAL_TITLE = "t.source <> 'unsorted'"

#: Whether an issue belongs on a shelf a reader browses: not a duplicate, and
#: not missing. Every list, count and calendar in this module applies it —
#: `issue_row`/`get_issue` are the deliberate exception, since a bookmarked
#: detail URL and the maintenance view (P1.6) both need to reach a row this
#: hides. Assumes the issues table is aliased ``i``.
VISIBLE = "i.duplicate_of IS NULL AND i.missing_since IS NULL"


# --------------------------------------------------------------------- urls


def cover_url(identifier: str) -> str:
    """Where an issue's 900 px cover lives, versioned by the rendering parameters."""
    return f"/api/issues/{quote(identifier)}/cover.jpg?v={COVER_VERSION}"


def thumb_url(identifier: str) -> str:
    """Where an issue's 300 px thumbnail lives."""
    return f"/api/issues/{quote(identifier)}/thumb.jpg?v={COVER_VERSION}"


def file_url(identifier: str) -> str:
    """Where an issue's PDF is streamed from."""
    return f"/api/issues/{quote(identifier)}/file"


def pages_url_template(identifier: str) -> str:
    """The template a reader fills in with a page number and a width.

    Versioned like the covers are, but by :data:`~paperstand.cache.PAGE_VERSION`
    rather than the file's modification time: an id names one set of bytes for
    good — a PDF replaced with different content is a new issue with a new id,
    never this one — and a touch that only moves the mtime must not change an
    address a browser has cached for a year. Bumping the version is what
    changes it, on purpose, for every issue at once.
    """
    return f"/api/issues/{quote(identifier)}/pages/{{n}}.webp?w={{w}}&v={PAGE_VERSION}"


# ------------------------------------------------------------------ mapping


def issue_from_row(row: sqlite3.Row) -> Issue:
    """Turn one joined row into the response model."""
    identifier = str(row["id"])
    aspect = None
    if row["page_w"] is not None and row["page_h"] is not None:
        aspect = Aspect(page_w=float(row["page_w"]), page_h=float(row["page_h"]))
    progress = None
    if row["progress_page"] is not None:
        progress = Progress(
            page=int(row["progress_page"]),
            page_count=(
                int(row["progress_page_count"]) if row["progress_page_count"] is not None else None
            ),
            updated_at=str(row["progress_updated_at"]),
        )
    return Issue(
        id=identifier,
        title_id=str(row["title_id"]),
        title_name=str(row["title_name"]),
        library_id=str(row["library_id"]),
        kind=row["kind"],
        issue_date=row["issue_date"],
        date_precision=row["date_precision"],
        date_source=row["date_source"],
        issue_number=row["issue_number"],
        volume=int(row["volume"]) if row["volume"] is not None else None,
        variant=row["variant"],
        label=str(row["label"]),
        filename=str(row["filename"]),
        rel_path=str(row["rel_path"]),
        size=int(row["size"]),
        content_hash=row["content_hash"],
        page_count=int(row["page_count"]) if row["page_count"] is not None else None,
        aspect=aspect,
        cover_url=cover_url(identifier),
        thumb_url=thumb_url(identifier),
        file_url=file_url(identifier),
        added_at=str(row["added_at"]),
        is_duplicate=row["duplicate_of"] is not None,
        missing_since=row["missing_since"],
        progress=progress,
    )


# ---------------------------------------------------------------- libraries


def list_libraries(connection: sqlite3.Connection) -> list[Library]:
    """Every configured library, with what the catalogue holds for it."""
    rows = connection.execute(
        f"""
        SELECT l.id, l.name, l.path, l.kind,
               (SELECT count(*) FROM titles t WHERE t.library_id = l.id) AS title_count,
               (SELECT count(*) FROM issues i
                  WHERE i.library_id = l.id AND {VISIBLE}) AS issue_count,
               (SELECT count(*) FROM issues i
                  JOIN titles t ON t.id = i.title_id
                  WHERE i.library_id = l.id AND {VISIBLE}
                    AND t.source = 'unsorted') AS unsorted_count
        FROM libraries l
        ORDER BY l.name COLLATE NOCASE
        """
    ).fetchall()
    return [
        Library(
            id=str(row["id"]),
            name=str(row["name"]),
            path=str(row["path"]),
            kind=row["kind"],
            title_count=int(row["title_count"]),
            issue_count=int(row["issue_count"]),
            unsorted_count=int(row["unsorted_count"]),
        )
        for row in rows
    ]


def catalogue_totals(connection: sqlite3.Connection) -> tuple[int, int, int, int]:
    """Titles, visible issues, duplicates and missing issues, across every library."""
    row = connection.execute(
        f"""
        SELECT (SELECT count(*) FROM titles) AS titles,
               (SELECT count(*) FROM issues i WHERE {VISIBLE}) AS issues,
               (SELECT count(*) FROM issues WHERE duplicate_of IS NOT NULL) AS duplicates,
               (SELECT count(*) FROM issues WHERE missing_since IS NOT NULL) AS missing
        """
    ).fetchone()
    return (
        int(row["titles"]),
        int(row["issues"]),
        int(row["duplicates"]),
        int(row["missing"]),
    )


# ------------------------------------------------------------------- titles

TITLE_SELECT = f"""
SELECT t.id, t.library_id, t.name, t.sort_name, t.kind, t.source,
       t.slug, t.frequency, t.language, t.issue_key, t.parent_slug, t.supplements,
       count(i.id) AS issue_count,
       min(i.issue_date) AS first_date,
       max(i.issue_date) AS last_date
FROM titles t
LEFT JOIN issues i ON i.title_id = t.id AND {VISIBLE}
"""


def _title_from_row(row: sqlite3.Row, latest: Issue | None) -> Title:
    return Title(
        id=str(row["id"]),
        name=str(row["name"]),
        library_id=str(row["library_id"]),
        kind=row["kind"],
        source=row["source"],
        slug=row["slug"],
        frequency=row["frequency"],
        language=row["language"],
        issue_key=row["issue_key"],
        parent_slug=row["parent_slug"],
        supplements=(json.loads(row["supplements"]) if row["supplements"] is not None else None),
        issue_count=int(row["issue_count"]),
        latest_issue=latest,
        first_date=row["first_date"],
        last_date=row["last_date"],
        cover_url=latest.cover_url if latest is not None else None,
        thumb_url=latest.thumb_url if latest is not None else None,
    )


def list_titles(
    connection: sqlite3.Connection,
    *,
    kind: str | None = None,
    library: str | None = None,
    sort: TitleSort = "name",
    include_unsorted: bool = False,
) -> list[Title]:
    """Every title, with its latest issue and the span its issues cover."""
    where: list[str] = []
    params: dict[str, Any] = {}
    if kind is not None:
        where.append("t.kind = :kind")
        params["kind"] = kind
    if library is not None:
        where.append("t.library_id = :library")
        params["library"] = library
    if not include_unsorted:
        where.append("t.source <> 'unsorted'")
    clause = f"WHERE {' AND '.join(where)}" if where else ""
    order = (
        "ORDER BY last_date IS NULL, last_date DESC, t.sort_name"
        if sort == "latest"
        else "ORDER BY t.sort_name, t.id"
    )
    # A title whose issues are all hidden — every one a duplicate, or missing
    # — has nothing to show on a shelf; `get_title` still answers for it by
    # id, since a bookmarked title page must not 404 just because its one
    # issue went missing.
    rows = connection.execute(
        f"{TITLE_SELECT} {clause} GROUP BY t.id HAVING count(i.id) > 0 {order}", params
    ).fetchall()
    latest = latest_issue_per_title(connection, [str(row["id"]) for row in rows])
    return [_title_from_row(row, latest.get(str(row["id"]))) for row in rows]


def get_title(connection: sqlite3.Connection, title_id: str) -> TitleDetail | None:
    """One title, with the years its issues fall in."""
    row = connection.execute(
        f"{TITLE_SELECT} WHERE t.id = :id GROUP BY t.id", {"id": title_id}
    ).fetchone()
    if row is None or row["id"] is None:
        return None
    latest = latest_issue_per_title(connection, [title_id]).get(title_id)
    title = _title_from_row(row, latest)
    return TitleDetail(**title.model_dump(), years=title_years(connection, title_id))


def title_years(connection: sqlite3.Connection, title_id: str) -> list[YearCount]:
    """How many issues of a title fall in each year, newest year first."""
    rows = connection.execute(
        f"""
        SELECT CAST(substr(i.issue_date, 1, 4) AS INTEGER) AS year, count(*) AS count
        FROM issues i
        WHERE i.title_id = :id AND {VISIBLE} AND i.issue_date IS NOT NULL
        GROUP BY year
        ORDER BY year DESC
        """,
        {"id": title_id},
    ).fetchall()
    return [YearCount(year=int(row["year"]), count=int(row["count"])) for row in rows]


def title_calendar(
    connection: sqlite3.Connection, title_id: str, year: int | None = None
) -> Calendar:
    """One year of a title as a ``YYYY-MM-DD`` → issue id map.

    With no year, the most recent one that has issues; with no issues at all, the
    current year and an empty map. Where two non-duplicate issues share a day, the
    plain issue always wins over a same-day supplement, and failing that the most
    recently added one wins, which is the same rule ``/api/today`` uses. The rows
    are read in the order the winner should be written in, last write wins, so
    the ``variant IS NULL`` rows come last.
    """
    years = title_years(connection, title_id)
    chosen = year if year is not None else (years[0].year if years else dt.date.today().year)
    rows = connection.execute(
        f"""
        SELECT i.id AS id, i.issue_date AS issue_date
        FROM issues i
        WHERE i.title_id = :id AND {VISIBLE} AND i.issue_date IS NOT NULL
          AND substr(i.issue_date, 1, 4) = :year
        ORDER BY i.variant IS NULL ASC, i.added_at ASC, i.id ASC
        """,
        {"id": title_id, "year": f"{chosen:04d}"},
    ).fetchall()
    days = {str(row["issue_date"]): str(row["id"]) for row in rows}
    return Calendar(year=chosen, years=years, days=days)


# ------------------------------------------------------------------- issues


def _issue_where(
    *,
    title: str | None,
    kind: str | None,
    library: str | None,
    date_from: str | None,
    date_to: str | None,
    year: int | None,
    include_duplicates: bool,
    missing: bool = False,
) -> tuple[str, dict[str, Any]]:
    where: list[str] = []
    params: dict[str, Any] = {}
    if missing:
        # The maintenance filter: only what is hidden for having gone
        # missing. The duplicate rule plays no part — a missing row's
        # `duplicate_of` is already cleared the moment it goes missing.
        where.append("i.missing_since IS NOT NULL")
    elif include_duplicates:
        where.append("i.missing_since IS NULL")
    else:
        where.append(VISIBLE)
    if title is not None:
        where.append("i.title_id = :title")
        params["title"] = title
    if kind is not None:
        where.append("t.kind = :kind")
        params["kind"] = kind
    if library is not None:
        where.append("i.library_id = :library")
        params["library"] = library
    if date_from is not None:
        where.append("i.issue_date >= :date_from")
        params["date_from"] = date_from
    if date_to is not None:
        where.append("i.issue_date <= :date_to")
        params["date_to"] = date_to
    if year is not None:
        where.append("substr(i.issue_date, 1, 4) = :year")
        params["year"] = f"{year:04d}"
    clause = f"WHERE {' AND '.join(where)}" if where else ""
    return clause, params


def list_issues(
    connection: sqlite3.Connection,
    *,
    title: str | None = None,
    kind: str | None = None,
    library: str | None = None,
    date_from: str | None = None,
    date_to: str | None = None,
    year: int | None = None,
    sort: IssueSort = "date_desc",
    limit: int = 50,
    offset: int = 0,
    include_duplicates: bool = False,
    missing: bool = False,
) -> tuple[list[Issue], int]:
    """A page of issues and the total the same filters match.

    ``missing`` is the maintenance filter (P1.6): with it, ``sort`` is
    ignored and the page comes back most-recently-gone first, since "how
    long has this been missing" is the only ordering that view needs.
    """
    clause, params = _issue_where(
        title=title,
        kind=kind,
        library=library,
        date_from=date_from,
        date_to=date_to,
        year=year,
        include_duplicates=include_duplicates,
        missing=missing,
    )
    total = int(
        connection.execute(
            f"SELECT count(*) AS total FROM issues i JOIN titles t ON t.id = i.title_id {clause}",
            params,
        ).fetchone()["total"]
    )
    order = "i.missing_since DESC, i.id DESC" if missing else ISSUE_ORDER[sort]
    rows = connection.execute(
        f"{ISSUE_SELECT} {clause} ORDER BY {order} LIMIT :limit OFFSET :offset",
        {**params, "limit": limit, "offset": offset},
    ).fetchall()
    return [issue_from_row(row) for row in rows], total


def issue_row(connection: sqlite3.Connection, issue_id: str) -> sqlite3.Row | None:
    """The raw ``issues`` row, for the endpoints that serve bytes."""
    row: sqlite3.Row | None = connection.execute(
        "SELECT * FROM issues WHERE id = :id", {"id": issue_id}
    ).fetchone()
    return row


def get_issue(connection: sqlite3.Connection, issue_id: str) -> sqlite3.Row | None:
    """One joined issue row, or ``None`` when the id is unknown."""
    row: sqlite3.Row | None = connection.execute(
        f"{ISSUE_SELECT} WHERE i.id = :id", {"id": issue_id}
    ).fetchone()
    return row


def neighbours(connection: sqlite3.Connection, row: sqlite3.Row) -> tuple[str | None, str | None]:
    """The ids of the issues either side of ``row`` within its title.

    Ordered by date and then file name, and duplicates are skipped: a duplicate
    is by definition the same issue as the one it points at, so stepping onto it
    would look like the reader had not moved.
    """
    params = {
        "title": str(row["title_id"]),
        "date": row["issue_date"] or "",
        "filename": str(row["filename"]),
    }
    order = "ifnull(issue_date, '') {0}, filename {0}"
    previous = connection.execute(
        f"SELECT id FROM issues i WHERE i.title_id = :title AND {VISIBLE} AND "
        "(ifnull(issue_date, '') < :date OR "
        " (ifnull(issue_date, '') = :date AND filename < :filename)) "
        f"ORDER BY {order.format('DESC')} LIMIT 1",
        params,
    ).fetchone()
    following = connection.execute(
        f"SELECT id FROM issues i WHERE i.title_id = :title AND {VISIBLE} AND "
        "(ifnull(issue_date, '') > :date OR "
        " (ifnull(issue_date, '') = :date AND filename > :filename)) "
        f"ORDER BY {order.format('ASC')} LIMIT 1",
        params,
    ).fetchone()
    return (
        str(previous["id"]) if previous is not None else None,
        str(following["id"]) if following is not None else None,
    )


# ------------------------------------------------------- one row per title


def _windowed(inner_where: str, partition_order: str, outer_order: str) -> str:
    """One issue per title: the first row of each partition, then re-sorted."""
    return (
        f"SELECT * FROM ("
        f"  SELECT {ISSUE_COLUMNS}, row_number() OVER ("
        f"    PARTITION BY i.title_id ORDER BY {partition_order}"
        f"  ) AS rn {ISSUE_FROM} {inner_where}"
        f") WHERE rn = 1 {outer_order}"
    )


def latest_issue_per_title(
    connection: sqlite3.Connection, title_ids: list[str]
) -> dict[str, Issue]:
    """The newest non-duplicate issue of each of ``title_ids``."""
    if not title_ids:
        return {}
    placeholders = ", ".join("?" * len(title_ids))
    sql = _windowed(
        f"WHERE {VISIBLE} AND i.title_id IN ({placeholders})",
        LATEST_FIRST,
        "",
    )
    rows = connection.execute(sql, title_ids).fetchall()
    return {str(row["title_id"]): issue_from_row(row) for row in rows}


def latest_per_kind(connection: sqlite3.Connection, kind: str) -> list[Issue]:
    """The newest issue of every real title of one kind, newest first.

    ``Unsorted`` is left out: it is the bucket the parser drops a file into when
    it cannot tell what the file is, and a shelf of periodicals is not where a
    user should meet it. It is still reachable through ``/api/titles`` with
    ``include_unsorted``, through ``/api/issues`` and through what was added
    last.
    """
    sql = _windowed(
        f"WHERE {VISIBLE} AND t.kind = :kind AND {REAL_TITLE}",
        LATEST_FIRST,
        "ORDER BY issue_date DESC, sort_name ASC",
    )
    rows = connection.execute(sql, {"kind": kind}).fetchall()
    return [issue_from_row(row) for row in rows]


def issues_on_date(connection: sqlite3.Connection, kind: str, date: str) -> list[Issue]:
    """One issue per real title of ``kind`` carrying exactly ``date``.

    A supplement filed on the same day as the plain issue never wins this
    window: ``i.variant IS NULL`` is the first tiebreaker, so a Weekend edition
    never hides the daily on its own day.
    """
    sql = _windowed(
        f"WHERE {VISIBLE} AND t.kind = :kind AND i.issue_date = :date AND {REAL_TITLE}",
        "i.variant IS NULL DESC, i.added_at DESC, i.filename ASC",
        "ORDER BY sort_name ASC",
    )
    rows = connection.execute(sql, {"kind": kind, "date": date}).fetchall()
    return [issue_from_row(row) for row in rows]


def latest_date_on_or_before(connection: sqlite3.Connection, kind: str, date: str) -> str | None:
    """The most recent day at or before ``date`` that has an issue of ``kind``."""
    row = connection.execute(
        f"""
        SELECT max(i.issue_date) AS latest
        FROM issues i JOIN titles t ON t.id = i.title_id
        WHERE {VISIBLE} AND t.kind = :kind
          AND i.issue_date IS NOT NULL AND i.issue_date <= :date
          AND t.source <> 'unsorted'
        """,
        {"kind": kind, "date": date},
    ).fetchone()
    return str(row["latest"]) if row is not None and row["latest"] is not None else None


def recently_added(connection: sqlite3.Connection, limit: int) -> list[Issue]:
    """The issues that joined the catalogue last."""
    rows = connection.execute(
        f"{ISSUE_SELECT} WHERE {VISIBLE} ORDER BY i.added_at DESC, i.id DESC LIMIT :limit",
        {"limit": limit},
    ).fetchall()
    return [issue_from_row(row) for row in rows]


# --------------------------------------------------------- unsorted, search

#: The bucket the parser drops a file into when it cannot tell what it is.
UNSORTED_WHERE = f"WHERE {VISIBLE} AND NOT ({REAL_TITLE})"

#: Where a search looks. A file whose name says what it is but whose title was
#: never configured is found by its file name, which is the point.
SEARCH_FIELDS = ("t.name", "i.filename", "i.derived_title")


def count_unsorted(connection: sqlite3.Connection) -> int:
    """How many issues the parser could not place."""
    row = connection.execute(
        f"SELECT count(*) AS total FROM issues i JOIN titles t ON t.id = i.title_id "
        f"{UNSORTED_WHERE}"
    ).fetchone()
    return int(row["total"])


def unsorted_issues(
    connection: sqlite3.Connection, *, limit: int = 50, offset: int = 0
) -> tuple[list[Issue], int]:
    """A page of the unplaced issues, newest first, and how many there are."""
    rows = connection.execute(
        f"{ISSUE_SELECT} {UNSORTED_WHERE} ORDER BY {ISSUE_ORDER['date_desc']} "
        "LIMIT :limit OFFSET :offset",
        {"limit": limit, "offset": offset},
    ).fetchall()
    return [issue_from_row(row) for row in rows], count_unsorted(connection)


def like_pattern(text: str) -> str:
    """``text`` as a ``LIKE`` pattern matching anywhere, with its wildcards off.

    A reader searching for ``100%`` means the three characters, not "anything";
    the escape character has to be escaped first or escaping the wildcards would
    introduce ones of its own.
    """
    escaped = text.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    return f"%{escaped}%"


def search_issues(
    connection: sqlite3.Connection, text: str, *, limit: int = 50, offset: int = 0
) -> tuple[list[Issue], int]:
    """Issues whose title, file name or derived title contains ``text``.

    A substring match, case-insensitive the way SQLite's ``LIKE`` is — which is
    to say for ASCII only, so ``almanacco`` finds *L'Almanacco* but ``citta`` does
    not find *Città*. Unsorted issues are searched too: a file nobody has
    configured a title for is exactly what somebody would go looking for by
    name.
    """
    condition = " OR ".join(f"{field} LIKE :q ESCAPE '\\'" for field in SEARCH_FIELDS)
    clause = f"WHERE {VISIBLE} AND ({condition})"
    params = {"q": like_pattern(text)}
    total = int(
        connection.execute(
            f"SELECT count(*) AS total FROM issues i JOIN titles t ON t.id = i.title_id {clause}",
            params,
        ).fetchone()["total"]
    )
    rows = connection.execute(
        f"{ISSUE_SELECT} {clause} ORDER BY {ISSUE_ORDER['date_desc']} LIMIT :limit OFFSET :offset",
        {**params, "limit": limit, "offset": offset},
    ).fetchall()
    return [issue_from_row(row) for row in rows], total


# ------------------------------------------------------------- feed entries


class EntryFacts(NamedTuple):
    """What an OPDS entry needs that no REST response carries.

    ``updated_at`` is Atom's ``<updated>``, which a client polls on. It has to be
    the instant the *entry* last changed rather than the instant the issue was
    added: a PDF replaced in place keeps its id and its date, and a feed that
    never says so is a feed nobody refetches.

    ``display_name`` is ``None`` for an issue filed under a real title, whose
    name is the right name for it, and the file's own derived title for one in
    the ``Unsorted`` bucket. There, the title's name is literally "Unsorted", and
    an app showing a shelf of entries all called *Unsorted — March 2026* is a
    shelf nobody can read; the parser worked out something from the file name
    even when it could not place it, and that is what to show.

    ``language`` is the issue's title's declared language tag, or ``None`` when
    the title is not a declared publication or declares none.

    None of the three is on :class:`~paperstand.schemas.Issue`: the web
    interface has no use for them, and the generated TypeScript client should
    not grow fields for a feed it never reads.
    """

    updated_at: str
    display_name: str | None
    language: str | None


def file_stem(filename: str) -> str:
    """A file name without its extension, and never empty."""
    stem = filename.rsplit(".", 1)[0].strip()
    return stem or filename


def entry_facts(connection: sqlite3.Connection, issue_ids: Sequence[str]) -> dict[str, EntryFacts]:
    """What the OPDS feed needs about each of ``issue_ids``."""
    if not issue_ids:
        return {}
    placeholders = ", ".join("?" * len(issue_ids))
    rows = connection.execute(
        "SELECT i.id AS id, i.updated_at AS updated_at, i.derived_title AS derived_title, "
        "i.filename AS filename, t.source AS source, t.language AS language "
        f"FROM issues i JOIN titles t ON t.id = i.title_id WHERE i.id IN ({placeholders})",
        tuple(issue_ids),
    ).fetchall()
    facts = {}
    for row in rows:
        name: str | None = None
        if row["source"] == "unsorted":
            name = str(row["derived_title"]).strip() or file_stem(str(row["filename"]))
        facts[str(row["id"])] = EntryFacts(str(row["updated_at"]), name, row["language"])
    return facts


def last_scan_finished(connection: sqlite3.Connection) -> str | None:
    """When the last scan finished, or ``None`` if none ever has."""
    row = connection.execute("SELECT max(finished_at) AS finished FROM scans").fetchone()
    return str(row["finished"]) if row is not None and row["finished"] is not None else None


# ----------------------------------------------------------------- progress


def continue_reading(connection: sqlite3.Connection, limit: int) -> list[Issue]:
    """Issues started and not finished, most recently read first.

    "Started and not finished" needs a page count to mean anything, so an issue
    the scanner has not opened yet never appears here however far into it the
    progress row claims to be. Duplicates are left out like everywhere else: a
    copy that turned out to be one hands its reader's position to the issue that
    won, in the scan that noticed.
    """
    rows = connection.execute(
        f"{ISSUE_SELECT} WHERE {VISIBLE} AND p.page > 1 "
        "AND coalesce(i.page_count, p.page_count) IS NOT NULL "
        "AND p.page < coalesce(i.page_count, p.page_count) "
        "ORDER BY p.updated_at DESC, i.id DESC LIMIT :limit",
        {"limit": limit},
    ).fetchall()
    return [issue_from_row(row) for row in rows]


def issues_with_progress(connection: sqlite3.Connection, limit: int) -> list[Issue]:
    """Every issue the reader has a position in, most recent first.

    Duplicates and missing issues are excluded rather than made optional:
    this list is "where you were reading", and two rows for the same issue —
    or a row nobody can open right now — is never the answer to that.
    """
    rows = connection.execute(
        f"{ISSUE_SELECT} WHERE p.issue_id IS NOT NULL AND {VISIBLE} "
        "ORDER BY p.updated_at DESC, i.id DESC LIMIT :limit",
        {"limit": limit},
    ).fetchall()
    return [issue_from_row(row) for row in rows]


def get_progress(connection: sqlite3.Connection, issue_id: str) -> ProgressRecord | None:
    """The stored position in one issue."""
    row = connection.execute(
        "SELECT issue_id, page, page_count, updated_at FROM reading_progress WHERE issue_id = :id",
        {"id": issue_id},
    ).fetchone()
    if row is None:
        return None
    return ProgressRecord(
        issue_id=str(row["issue_id"]),
        page=int(row["page"]),
        page_count=int(row["page_count"]) if row["page_count"] is not None else None,
        updated_at=str(row["updated_at"]),
    )


def set_progress(
    connection: sqlite3.Connection, issue_id: str, page: int, page_count: int | None
) -> ProgressRecord:
    """Write where the reader got to, replacing whatever was there."""
    now = utc_now()
    with connection:
        connection.execute(
            "INSERT INTO reading_progress (issue_id, page, page_count, updated_at) "
            "VALUES (?, ?, ?, ?) ON CONFLICT (issue_id) DO UPDATE SET "
            "page = excluded.page, page_count = excluded.page_count, "
            "updated_at = excluded.updated_at",
            (issue_id, page, page_count, now),
        )
    return ProgressRecord(issue_id=issue_id, page=page, page_count=page_count, updated_at=now)


def delete_progress(connection: sqlite3.Connection, issue_id: str) -> None:
    """Forget where the reader got to."""
    with connection:
        connection.execute("DELETE FROM reading_progress WHERE issue_id = ?", (issue_id,))
