"""The OPDS feed tree, at ``/opds``.

The same catalogue the REST API answers from, shaped for a reading app instead
of for a browser. Every read goes through :mod:`paperstand.queries`, so a
duplicate hidden on the *Today* screen is hidden in the feed too, the newspapers
of a day fall back the same way, and *Unsorted* is kept out of the shelves in
both — one behaviour, described once.

Three things are true of every feed here and of no REST endpoint:

* **The URLs are absolute.** A client stores the catalogue's address and comes
  back to it days later, from a different network; a relative href is only
  meaningful to something that remembers what it asked. They are built from
  ``PAPERSTAND_BASE_URL`` when it is set, and otherwise from the request, which
  :mod:`paperstand.proxy` has already corrected with the proxy's headers.
* **The media type says what the feed is for.** ``kind=navigation`` for a menu,
  ``kind=acquisition`` for a shelf of things to download. A client draws the two
  differently, and gets it wrong when the type is wrong.
* **Nothing is in the OpenAPI document.** The router is registered with
  ``include_in_schema=False``: the feed is XML, its shape is the OPDS
  specification rather than Paperstand's, and the generated TypeScript client
  has no use for it.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Callable, Sequence
from typing import Annotated, NamedTuple
from urllib.parse import quote, urlencode

from fastapi import APIRouter, HTTPException, Query, Request, Response

from paperstand import queries
from paperstand.api.common import UNKNOWN_TITLE, catalogue
from paperstand.api.today import local_today
from paperstand.config import Settings
from paperstand.db import Database, utc_now
from paperstand.opds import atom
from paperstand.opds.atom import (
    ACQUISITION,
    ACQUISITION_REL,
    IMAGE_REL,
    JPEG,
    NAVIGATION,
    OPENSEARCH_TYPE,
    PDF,
    THUMBNAIL_REL,
    Entry,
    Feed,
    Link,
)
from paperstand.parsing.engine import LABEL_MONTHS
from paperstand.schemas import Issue, Kind, Title

#: Issues per page of a paginated feed. Injectable so that the pagination can be
#: tested at both edges without generating a library of thousands.
PAGE_SIZE = 50

#: How far back "recently added" goes.
RECENT_LIMIT = 50

#: The root of every identifier in the feed. A ``urn:`` because an Atom id is an
#: identity, not an address: it must not change when the deployment moves to a
#: different host, and a client uses it to recognise an entry it already has.
URN = "urn:paperstand"

CATALOGUE_TITLE = "Paperstand"
SEARCH_DESCRIPTION = "Search the Paperstand catalogue by title, file name or date"
EMPTY_QUERY = "a search needs something to search for"

KIND_LABELS: dict[str, str] = {"newspaper": "Newspaper", "magazine": "Magazine"}


class Section(NamedTuple):
    """One line of a navigation feed: what it is called and where it goes."""

    key: str
    name: str
    description: str
    path: str
    media_type: str


#: The root feed, in the order a reader meets it.
ROOT_SECTIONS: tuple[Section, ...] = (
    Section(
        "today",
        "Today",
        "Today's newspapers and the latest of every magazine",
        "/opds/today",
        ACQUISITION,
    ),
    Section(
        "newspaper",
        "Newspapers",
        "Every newspaper, by title",
        "/opds/kind/newspaper",
        NAVIGATION,
    ),
    Section(
        "magazine",
        "Magazines",
        "Every magazine, by title",
        "/opds/kind/magazine",
        NAVIGATION,
    ),
    Section(
        "recent",
        "Recently added",
        "The issues that joined the catalogue last",
        "/opds/recent",
        ACQUISITION,
    ),
)

#: Only offered when there is something in it: an empty diagnostic bucket is not
#: a shelf anybody wants on the front page of their reading app.
UNSORTED_SECTION = Section(
    "unsorted",
    "Unsorted",
    "Files the parser could not place under a title",
    "/opds/unsorted",
    ACQUISITION,
)


# ----------------------------------------------------------------- wording


def human_size(size: int) -> str:
    """A file size somebody can read, in the units a download dialog uses."""
    for unit, step in (("GB", 1_000_000_000), ("MB", 1_000_000), ("kB", 1_000)):
        if size >= step:
            return f"{size / step:.1f} {unit}"
    return f"{size} B"


def human_date(value: str) -> str:
    """``2026-03-17`` as ``17 March 2026``; anything else, unchanged."""
    try:
        year, month, day = (int(part) for part in value.split("-", 2))
        return f"{day} {LABEL_MONTHS[month - 1]} {year}"
    except (ValueError, IndexError):
        return value


def plural(count: int, noun: str) -> str:
    """``1 issue``, ``4 issues``."""
    return f"{count} {noun}" if count == 1 else f"{count} {noun}s"


# ------------------------------------------------------------------ entries


def issue_urn(issue_id: str) -> str:
    """The stable identity of an issue, wherever it is being served from."""
    return f"{URN}:issue:{issue_id}"


def entry_name(issue: Issue, facts: queries.EntryFacts) -> str:
    """What to call an issue in a reading app.

    The title's name, except in the ``Unsorted`` bucket, where every issue's
    title is literally *Unsorted* and a shelf of entries all called
    "Unsorted — March 2026" tells a reader nothing. There the file's own derived
    title stands in — see :class:`~paperstand.queries.EntryFacts`. The bucket is
    still where the issue is *filed*: it is reached through ``/opds/unsorted``
    and nowhere else, and only what is written on it changes.
    """
    return facts.display_name or issue.title_name


def entry_title(name: str, label: str) -> str:
    """``Corriere del Ponte — 17 March 2026``.

    The label is the API's, so the feed and the web interface name an issue the
    same way. It is empty for a file nothing could be worked out about, and then
    the name is all there is to say.
    """
    return f"{name} — {label}" if label else name


def issue_summary(name: str, issue: Issue) -> str:
    """What the entry says under its cover, leaving out what is not known."""
    parts = [name]
    if issue.label:
        parts.append(issue.label)
    if issue.page_count is not None:
        parts.append(plural(issue.page_count, "page"))
    parts.append(human_size(issue.size))
    return " · ".join(parts)


def issue_entry(issue: Issue, facts: queries.EntryFacts, absolute: Callable[[str], str]) -> Entry:
    """One issue, with its cover, its thumbnail and its PDF."""
    urn = issue_urn(issue.id)
    name = entry_name(issue, facts)
    return Entry(
        id=urn,
        title=entry_title(name, issue.label),
        updated=facts.updated_at,
        author=name,
        summary=issue_summary(name, issue),
        category=(issue.kind, KIND_LABELS[issue.kind]),
        dc_date=issue.issue_date,
        dc_identifier=urn,
        dc_language=facts.language,
        links=(
            Link(IMAGE_REL, absolute(issue.cover_url), JPEG),
            Link(THUMBNAIL_REL, absolute(issue.thumb_url), JPEG),
            Link(ACQUISITION_REL, absolute(issue.file_url), PDF, "Download PDF"),
        ),
    )


def title_content(title: Title) -> str:
    """``23 issues, latest 17 March 2026``."""
    count = plural(title.issue_count, "issue")
    latest = title.latest_issue
    if latest is not None and latest.label:
        return f"{count}, latest {latest.label}"
    if title.last_date:
        return f"{count}, latest {human_date(title.last_date)}"
    return count


def title_entry(title: Title, updated: str, absolute: Callable[[str], str]) -> Entry:
    """One title, leading to the feed of its issues."""
    links = [Link(atom.SUBSECTION, absolute(f"/opds/titles/{quote(title.id)}"), ACQUISITION)]
    if title.cover_url is not None and title.thumb_url is not None:
        links.append(Link(IMAGE_REL, absolute(title.cover_url), JPEG))
        links.append(Link(THUMBNAIL_REL, absolute(title.thumb_url), JPEG))
    return Entry(
        id=f"{URN}:title:{title.id}",
        title=title.name,
        updated=updated,
        content=title_content(title),
        dc_language=title.language,
        links=tuple(links),
    )


def section_entry(section: Section, updated: str, absolute: Callable[[str], str]) -> Entry:
    """One line of a navigation feed, as an entry."""
    return Entry(
        id=f"{URN}:feed:{section.key}",
        title=section.name,
        updated=updated,
        content=section.description,
        links=(Link(atom.SUBSECTION, absolute(section.path), section.media_type),),
    )


# -------------------------------------------------------------------- URLs


def base_url(request: Request, settings: Settings) -> str:
    """The address the outside world reaches this server at, without a slash.

    ``PAPERSTAND_BASE_URL`` wins when it is set, because only the person who
    deployed it knows that the catalogue is published at
    ``https://paperstand.example/reading`` when the container only ever sees
    ``/``. Without it the request is believed: the scheme and the host, already
    corrected from the proxy's headers by :mod:`paperstand.proxy`.
    """
    configured = (settings.base_url or "").strip()
    if configured:
        return configured.rstrip("/")
    return str(request.base_url).rstrip("/")


def paged(path: str, page: int, **params: str) -> str:
    """A feed's own URL, carrying its page and whatever else it needs."""
    query = {**params}
    if page > 1:
        query["page"] = str(page)
    return f"{path}?{urlencode(query)}" if query else path


class Frame:
    """The links every feed carries, for one request.

    ``self``, ``start``, ``up`` and ``search`` are the same four questions in
    every feed — where am I, where does this catalogue begin, what contains me,
    how do I search — and getting one of them wrong is only ever noticed on a
    phone. They are built in one place, from the path of the feed and the path
    of its parent.
    """

    def __init__(self, request: Request, settings: Settings) -> None:
        self.base = base_url(request, settings)

    def absolute(self, path: str) -> str:
        """``/api/issues/x/file`` as a URL a client can store and come back to."""
        return f"{self.base}{path}"

    def links(
        self,
        self_path: str,
        media_type: str,
        parent: tuple[str, str] | None,
        extra: Sequence[Link] = (),
    ) -> list[Link]:
        links = [
            Link(atom.SELF, self.absolute(self_path), media_type),
            Link(atom.START, self.absolute("/opds"), NAVIGATION),
            Link(atom.SEARCH, self.absolute("/opds/opensearch.xml"), OPENSEARCH_TYPE),
        ]
        if parent is not None:
            parent_path, parent_type = parent
            links.append(Link(atom.UP, self.absolute(parent_path), parent_type))
        links.extend(extra)
        return links


def xml(payload: bytes, media_type: str) -> Response:
    """A rendered feed, typed so that the client knows what it is holding."""
    return Response(content=payload, media_type=atom.served(media_type))


# ------------------------------------------------------------------ routes


def create_router(
    settings: Settings, database: Database | None, page_size: int | None = None
) -> APIRouter:
    """Build the OPDS router.

    ``page_size`` is read here rather than defaulted in the signature so that a
    test can set :data:`PAGE_SIZE` to something small and exercise the first, a
    middle and the last page of a feed without a library of thousands.

    Every feed answers ``HEAD`` as well as ``GET``: a client probes a stored
    catalogue before opening it, and the two methods have to agree about what
    exists.
    """
    size = PAGE_SIZE if page_size is None else page_size
    router = APIRouter(prefix="/opds", tags=["opds"], include_in_schema=False)

    def freshness(connection: sqlite3.Connection) -> str:
        """What a feed's ``<updated>`` says: when the catalogue last changed.

        The end of the last scan, which is the only moment anything in the
        library can have entered the catalogue. Before the first scan there is
        no such instant and the current one is used, so that a feed served from
        an empty database is still a valid Atom document.
        """
        return queries.last_scan_finished(connection) or utc_now()

    def issue_entries(
        connection: sqlite3.Connection,
        issues: Sequence[Issue],
        frame: Frame,
        updated: str,
    ) -> tuple[Entry, ...]:
        """One entry per issue, with the facts only the feed needs.

        One query for the lot, rather than one per entry: a page of fifty issues
        is one round trip either way, and the feed is what a phone waits on.
        """
        facts = queries.entry_facts(connection, [issue.id for issue in issues])
        fallback = queries.EntryFacts(updated, None, None)
        return tuple(
            issue_entry(issue, facts.get(issue.id, fallback), frame.absolute) for issue in issues
        )

    def acquisition_feed(
        *,
        connection: sqlite3.Connection,
        request: Request,
        feed_id: str,
        title: str,
        subtitle: str | None,
        self_path: str,
        parent: tuple[str, str] | None,
        issues: Sequence[Issue],
        extra: Sequence[Link] = (),
    ) -> Response:
        frame = Frame(request, settings)
        updated = freshness(connection)
        feed = Feed(
            id=feed_id,
            title=title,
            subtitle=subtitle,
            updated=updated,
            links=frame.links(self_path, ACQUISITION, parent, extra),
            entries=issue_entries(connection, issues, frame, updated),
        )
        return xml(atom.feed_bytes(feed), ACQUISITION)

    def paginated(
        *,
        connection: sqlite3.Connection,
        request: Request,
        feed_id: str,
        title: str,
        subtitle: str | None,
        path: str,
        parent: tuple[str, str] | None,
        page: int,
        total: int,
        issues: Sequence[Issue],
        params: dict[str, str] | None = None,
    ) -> Response:
        query = params or {}

        def href(number: int) -> str:
            return paged(path, number, **query)

        pages = atom.page_count(total, size)
        return acquisition_feed(
            connection=connection,
            request=request,
            feed_id=feed_id,
            title=title,
            subtitle=subtitle,
            self_path=href(page),
            parent=parent,
            issues=issues,
            extra=atom.page_links(href, page, pages, ACQUISITION),
        )

    @router.api_route("", methods=["GET", "HEAD"])
    def root(request: Request) -> Response:
        """The catalogue's front page: four shelves, and Unsorted when it exists."""
        connection = catalogue(database)
        frame = Frame(request, settings)
        updated = freshness(connection)
        sections = list(ROOT_SECTIONS)
        if queries.count_unsorted(connection) > 0:
            sections.append(UNSORTED_SECTION)
        feed = Feed(
            id=f"{URN}:feed:root",
            title=CATALOGUE_TITLE,
            subtitle="Newspapers and magazines",
            updated=updated,
            links=frame.links("/opds", NAVIGATION, None),
            entries=tuple(section_entry(section, updated, frame.absolute) for section in sections),
        )
        return xml(atom.feed_bytes(feed), NAVIGATION)

    @router.api_route("/kind/{kind}", methods=["GET", "HEAD"])
    def by_kind(request: Request, kind: Kind) -> Response:
        """Every title of one kind, each leading to the feed of its issues."""
        connection = catalogue(database)
        frame = Frame(request, settings)
        updated = freshness(connection)
        titles = queries.list_titles(connection, kind=kind, sort="name")
        latest = queries.entry_facts(
            connection,
            [title.latest_issue.id for title in titles if title.latest_issue is not None],
        )

        def title_updated(title: Title) -> str:
            """A title is as fresh as its newest issue."""
            if title.latest_issue is None:
                return updated
            facts = latest.get(title.latest_issue.id)
            return facts.updated_at if facts is not None else updated

        feed = Feed(
            id=f"{URN}:feed:kind:{kind}",
            title=KIND_LABELS[kind] + "s",
            updated=updated,
            links=frame.links(f"/opds/kind/{kind}", NAVIGATION, ("/opds", NAVIGATION)),
            entries=tuple(
                title_entry(title, title_updated(title), frame.absolute) for title in titles
            ),
        )
        return xml(atom.feed_bytes(feed), NAVIGATION)

    @router.api_route("/titles/{title_id}", methods=["GET", "HEAD"])
    def title_feed(
        request: Request,
        title_id: str,
        page: Annotated[int, Query(ge=1)] = 1,
    ) -> Response:
        """One title's issues, newest first, fifty to a page."""
        connection = catalogue(database)
        title = queries.get_title(connection, title_id)
        if title is None:
            raise HTTPException(status_code=404, detail=UNKNOWN_TITLE)
        issues, total = queries.list_issues(
            connection,
            title=title_id,
            sort="date_desc",
            limit=size,
            offset=(page - 1) * size,
        )
        return paginated(
            connection=connection,
            request=request,
            feed_id=f"{URN}:title:{title.id}",
            title=title.name,
            subtitle=title_content(title),
            path=f"/opds/titles/{quote(title_id)}",
            parent=(f"/opds/kind/{title.kind}", NAVIGATION),
            page=page,
            total=total,
            issues=issues,
        )

    @router.api_route("/today", methods=["GET", "HEAD"])
    def today(request: Request) -> Response:
        """The day's newspapers, then the latest issue of every magazine.

        The same fallback ``/api/today`` uses: a day with nothing on it shows
        the most recent day that has something, because a reader opening this
        before the morning's files have arrived wants yesterday's paper, not an
        empty shelf.
        """
        connection = catalogue(database)
        wanted = local_today(settings).isoformat()
        shown: str | None = wanted
        newspapers = queries.issues_on_date(connection, "newspaper", wanted)
        if not newspapers:
            shown = queries.latest_date_on_or_before(connection, "newspaper", wanted)
            if shown is not None:
                newspapers = queries.issues_on_date(connection, "newspaper", shown)
        magazines = queries.latest_per_kind(connection, "magazine")
        # The subtitle names the day actually on the shelf, not the day that was
        # asked for: a reader looking at Saturday's paper on a Sunday should be
        # told so rather than left to wonder why the date on the cover is wrong.
        day = f"Newspapers for {human_date(shown)}" if shown is not None else "No newspapers yet"
        return acquisition_feed(
            connection=connection,
            request=request,
            feed_id=f"{URN}:feed:today",
            title="Today",
            subtitle=f"{day}, and the latest magazines",
            self_path="/opds/today",
            parent=("/opds", NAVIGATION),
            issues=[*newspapers, *magazines],
        )

    @router.api_route("/recent", methods=["GET", "HEAD"])
    def recent(request: Request) -> Response:
        """What the last scans brought in."""
        connection = catalogue(database)
        return acquisition_feed(
            connection=connection,
            request=request,
            feed_id=f"{URN}:feed:recent",
            title="Recently added",
            subtitle=None,
            self_path="/opds/recent",
            parent=("/opds", NAVIGATION),
            issues=queries.recently_added(connection, RECENT_LIMIT),
        )

    @router.api_route("/unsorted", methods=["GET", "HEAD"])
    def unsorted(request: Request, page: Annotated[int, Query(ge=1)] = 1) -> Response:
        """The files the parser could not place, newest first."""
        connection = catalogue(database)
        issues, total = queries.unsorted_issues(connection, limit=size, offset=(page - 1) * size)
        return paginated(
            connection=connection,
            request=request,
            feed_id=f"{URN}:feed:unsorted",
            title="Unsorted",
            subtitle="Files the parser could not place under a title",
            path="/opds/unsorted",
            parent=("/opds", NAVIGATION),
            page=page,
            total=total,
            issues=issues,
        )

    @router.api_route("/search", methods=["GET", "HEAD"])
    def search(
        request: Request,
        q: str = "",
        page: Annotated[int, Query(ge=1)] = 1,
    ) -> Response:
        """Issues matching ``q`` in their title, file name or derived title."""
        text = q.strip()
        if not text:
            raise HTTPException(status_code=400, detail=EMPTY_QUERY)
        connection = catalogue(database)
        issues, total = queries.search_issues(
            connection, text, limit=size, offset=(page - 1) * size
        )
        return paginated(
            connection=connection,
            request=request,
            feed_id=f"{URN}:feed:search:{quote(text)}",
            title=f"Search: {text}",
            subtitle=plural(total, "result"),
            path="/opds/search",
            parent=("/opds", NAVIGATION),
            page=page,
            total=total,
            issues=issues,
            params={"q": text},
        )

    @router.api_route("/opensearch.xml", methods=["GET", "HEAD"])
    def opensearch(request: Request) -> Response:
        """How a client turns what somebody typed into a request for a feed."""
        frame = Frame(request, settings)
        template = f"{frame.absolute('/opds/search')}?q={{searchTerms}}"
        payload = atom.opensearch_bytes(CATALOGUE_TITLE, SEARCH_DESCRIPTION, template)
        return xml(payload, OPENSEARCH_TYPE)

    return router
