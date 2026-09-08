"""The OPDS 1.2 catalogue.

Every feed is checked twice: once with ``feedparser``, which is what a reading
app's parser is, and once with ``ElementTree``, which is what says whether the
bytes are well-formed XML and carries the elements ``feedparser`` folds away
(``dc:date``, the exact link types). A feed that only one of the two accepts is
a feed that works in some apps.

The counts are never typed out by hand: they come from the REST API of the same
running application, which is the point — the feed and the web
interface must not disagree about what the catalogue holds.
"""

from __future__ import annotations

import sqlite3
import xml.etree.ElementTree as ET
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import feedparser
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from paperstand.config import Settings
from paperstand.db import utc_now
from paperstand.main import create_app
from paperstand.opds import atom
from paperstand.opds import router as opds_router
from paperstand.proxy import TrustedProxies
from tests.conftest import quiet_settings

ATOM = "{http://www.w3.org/2005/Atom}"
DC = "{http://purl.org/dc/terms/}"

NAVIGATION = "application/atom+xml;profile=opds-catalog;kind=navigation;charset=utf-8"
ACQUISITION = "application/atom+xml;profile=opds-catalog;kind=acquisition;charset=utf-8"

#: Every feed in the tree that is served from the sample library as it is.
EVERY_FEED = (
    ("/opds", NAVIGATION),
    ("/opds/kind/newspaper", NAVIGATION),
    ("/opds/kind/magazine", NAVIGATION),
    ("/opds/today", ACQUISITION),
    ("/opds/recent", ACQUISITION),
    ("/opds/unsorted", ACQUISITION),
)


# ------------------------------------------------------------------ helpers


def fetch(client: TestClient, path: str, **params: Any) -> tuple[Any, ET.Element]:
    """One feed, parsed both ways, with the parses asserted to have worked."""
    response = client.get(path, params=params)
    assert response.status_code == 200, response.text
    parsed = feedparser.parse(response.content)
    assert parsed.bozo == 0, getattr(parsed, "bozo_exception", None)
    return parsed, ET.fromstring(response.content)


def links(element: ET.Element) -> list[dict[str, str]]:
    """The ``<link>`` children of a feed or an entry, as their attributes."""
    return [link.attrib for link in element.findall(f"{ATOM}link")]


def rels(element: ET.Element) -> set[str]:
    return {link["rel"] for link in links(element)}


def href(element: ET.Element, rel: str) -> str:
    """The one href with this rel; fails loudly when there is not exactly one."""
    found = [link["href"] for link in links(element) if link["rel"] == rel]
    assert len(found) == 1, f"expected one {rel!r} link, got {found}"
    return found[0]


def entries(feed: ET.Element) -> list[ET.Element]:
    return feed.findall(f"{ATOM}entry")


def text(element: ET.Element, tag: str) -> str:
    child = element.find(tag)
    assert child is not None, f"no {tag} in {element.tag}"
    return child.text or ""


def local(url: str) -> str:
    """The path and query of an absolute href, to replay against the client."""
    parts = urlparse(url)
    return f"{parts.path}?{parts.query}" if parts.query else parts.path


@contextmanager
def serving(settings: Settings) -> Iterator[TestClient]:
    """A client for a fresh application over ``settings``."""
    with TestClient(create_app(settings)) as client:
        yield client


def rows(db_path: Path, statement: str, *params: Any) -> None:
    """Run one statement against the catalogue, the way a scan would."""
    connection = sqlite3.connect(db_path)
    try:
        connection.execute(statement, params)
        connection.commit()
    finally:
        connection.close()


def api(client: TestClient, path: str, **params: Any) -> Any:
    response = client.get(path, params=params)
    assert response.status_code == 200, response.text
    return response.json()


# ------------------------------------------------------- shape of every feed


@pytest.mark.parametrize(("path", "content_type"), EVERY_FEED)
def test_every_feed_is_a_well_formed_opds_document(
    catalogue_client: TestClient, path: str, content_type: str
) -> None:
    response = catalogue_client.get(path)

    assert response.status_code == 200
    assert response.headers["content-type"] == content_type
    assert response.content.startswith(b"<?xml version=")
    parsed = feedparser.parse(response.content)
    assert parsed.bozo == 0
    assert parsed.feed.title
    assert parsed.feed.author == "Paperstand"
    assert parsed.feed.updated

    root = ET.fromstring(response.content)
    assert root.tag == f"{ATOM}feed"
    assert text(root, f"{ATOM}id").startswith("urn:paperstand:")
    assert text(root, f"{ATOM}updated")


@pytest.mark.parametrize(("path", "content_type"), EVERY_FEED)
def test_every_feed_answers_head_the_way_it_answers_get(
    catalogue_client: TestClient, path: str, content_type: str
) -> None:
    """A client probing a stored catalogue must not be told it is gone."""
    response = catalogue_client.head(path)

    assert response.status_code == 200
    assert response.headers["content-type"] == content_type
    assert response.content == b""


@pytest.mark.parametrize(("path", "content_type"), EVERY_FEED)
def test_every_feed_carries_the_four_standard_links(
    catalogue_client: TestClient, path: str, content_type: str
) -> None:
    _, root = fetch(catalogue_client, path)

    assert href(root, "self").endswith(path)
    assert href(root, "start").endswith("/opds")
    assert href(root, "search").endswith("/opds/opensearch.xml")
    search = next(link for link in links(root) if link["rel"] == "search")
    assert search["type"] == "application/opensearchdescription+xml"
    # The root is the only feed with nothing above it.
    assert ("up" in rels(root)) is (path != "/opds")


def test_the_root_names_the_sections_a_reader_starts_from(
    catalogue_client: TestClient,
) -> None:
    _, root = fetch(catalogue_client, "/opds")

    titles = [text(entry, f"{ATOM}title") for entry in entries(root)]
    assert titles == ["Today", "Newspapers", "Magazines", "Recently added", "Unsorted"]
    for entry in entries(root):
        assert rels(entry) == {"subsection"}
        assert text(entry, f"{ATOM}id").startswith("urn:paperstand:feed:")
    kinds = {text(entry, f"{ATOM}title"): links(entry)[0]["type"] for entry in entries(root)}
    assert kinds["Newspapers"] == atom.NAVIGATION
    assert kinds["Magazines"] == atom.NAVIGATION
    assert kinds["Today"] == atom.ACQUISITION
    assert kinds["Recently added"] == atom.ACQUISITION


# ---------------------------------------------------------- the shelves


@pytest.mark.parametrize("kind", ["newspaper", "magazine"])
def test_a_kind_feed_holds_every_title_of_that_kind(
    catalogue_client: TestClient, kind: str
) -> None:
    expected = api(catalogue_client, "/api/titles", kind=kind)
    _, root = fetch(catalogue_client, f"/opds/kind/{kind}")

    assert [text(entry, f"{ATOM}title") for entry in entries(root)] == [
        title["name"] for title in expected
    ]
    assert all(title["source"] != "unsorted" for title in expected)
    for entry, title in zip(entries(root), expected, strict=True):
        assert rels(entry) == {
            "subsection",
            "http://opds-spec.org/image",
            "http://opds-spec.org/image/thumbnail",
        }
        assert href(entry, "subsection").endswith(f"/opds/titles/{title['id']}")
        assert href(entry, "http://opds-spec.org/image").endswith(title["cover_url"])
        assert text(entry, f"{ATOM}content").startswith(f"{title['issue_count']} issue")


def test_a_title_entry_says_how_many_issues_and_which_is_latest(
    catalogue_client: TestClient,
) -> None:
    _, root = fetch(catalogue_client, "/opds/kind/newspaper")
    daily = next(
        entry for entry in entries(root) if text(entry, f"{ATOM}title") == "Corriere del Ponte"
    )

    assert text(daily, f"{ATOM}content") == "14 issues, latest 17 March 2026"


def test_a_title_feed_holds_that_titles_issues_newest_first(
    catalogue_client: TestClient,
) -> None:
    title = api(catalogue_client, "/api/titles", kind="newspaper")[0]
    expected = api(catalogue_client, "/api/issues", title=title["id"], limit=200)

    _, root = fetch(catalogue_client, f"/opds/titles/{title['id']}")

    assert len(entries(root)) == expected["total"] == title["issue_count"]
    dates = [text(entry, f"{DC}date") for entry in entries(root)]
    assert dates == sorted(dates, reverse=True)
    assert href(root, "up").endswith(f"/opds/kind/{title['kind']}")


def test_an_unknown_title_is_a_404(catalogue_client: TestClient) -> None:
    assert catalogue_client.get("/opds/titles/nothing-of-the-sort").status_code == 404


def test_today_is_the_days_newspapers_then_the_latest_magazines(
    catalogue_client: TestClient,
) -> None:
    today = api(catalogue_client, "/api/today")
    expected = [issue["id"] for issue in today["newspapers"] + today["magazines"]]

    _, root = fetch(catalogue_client, "/opds/today")

    assert [text(entry, f"{DC}identifier") for entry in entries(root)] == [
        f"urn:paperstand:issue:{identifier}" for identifier in expected
    ]
    # The sample library's newest day is behind the real one, so the feed is
    # showing the fallback day — and says which in its subtitle.
    assert text(root, f"{ATOM}subtitle").startswith("Newspapers for 17 March 2026")


def test_recently_added_is_what_the_last_scans_brought_in(
    catalogue_client: TestClient,
) -> None:
    expected = api(catalogue_client, "/api/issues", sort="added_desc", limit=50)

    _, root = fetch(catalogue_client, "/opds/recent")

    assert [text(entry, f"{DC}identifier") for entry in entries(root)] == [
        f"urn:paperstand:issue:{issue['id']}" for issue in expected["items"]
    ]


# ------------------------------------------------------------------ entries


def test_an_issue_entry_carries_everything_a_reading_app_shows(
    catalogue_client: TestClient,
) -> None:
    today = api(catalogue_client, "/api/today")
    issue = today["newspapers"][0]

    _, root = fetch(catalogue_client, "/opds/today")
    entry = entries(root)[0]

    urn = f"urn:paperstand:issue:{issue['id']}"
    assert text(entry, f"{ATOM}id") == urn
    assert text(entry, f"{DC}identifier") == urn
    assert text(entry, f"{ATOM}title") == f"{issue['title_name']} — {issue['label']}"
    assert text(entry, f"{ATOM}updated")
    assert text(entry, f"{DC}date") == issue["issue_date"]
    author = entry.find(f"{ATOM}author")
    assert author is not None and text(author, f"{ATOM}name") == issue["title_name"]
    category = entry.find(f"{ATOM}category")
    assert category is not None
    assert category.attrib == {"term": "newspaper", "label": "Newspaper"}
    summary = text(entry, f"{ATOM}summary")
    assert summary.startswith(f"{issue['title_name']} · {issue['label']} · ")
    assert f"{issue['page_count']} pages" in summary

    acquisition = next(
        link for link in links(entry) if link["rel"] == "http://opds-spec.org/acquisition"
    )
    assert acquisition["type"] == "application/pdf"
    assert acquisition["title"] == "Download PDF"
    assert acquisition["href"].endswith(issue["file_url"])
    image = next(link for link in links(entry) if link["rel"] == "http://opds-spec.org/image")
    assert image["type"] == "image/jpeg"
    assert image["href"].endswith(issue["cover_url"])


def test_the_links_of_an_entry_actually_resolve(catalogue_client: TestClient) -> None:
    """The feed is only useful if what it points at can be fetched."""
    _, root = fetch(catalogue_client, "/opds/today")
    entry = entries(root)[0]

    pdf = catalogue_client.get(local(href(entry, "http://opds-spec.org/acquisition")))
    assert pdf.status_code == 200
    assert pdf.headers["content-type"] == "application/pdf"

    thumbnail = catalogue_client.get(local(href(entry, "http://opds-spec.org/image/thumbnail")))
    assert thumbnail.status_code == 200
    assert thumbnail.headers["content-type"] == "image/jpeg"

    cover = catalogue_client.get(local(href(entry, "http://opds-spec.org/image")))
    assert cover.status_code == 200
    assert cover.headers["content-type"] == "image/jpeg"


def test_an_issue_with_nothing_known_about_it_is_still_an_entry(
    catalogue_client: TestClient,
) -> None:
    """The unsorted bucket is where a file with no date and no title goes."""
    _, root = fetch(catalogue_client, "/opds/unsorted")

    assert entries(root)
    for entry in entries(root):
        assert text(entry, f"{ATOM}title")
        assert "http://opds-spec.org/acquisition" in rels(entry)


# --------------------------------------------------------------- Unsorted


def add_unsorted_pair(settings: Settings) -> tuple[str, str]:
    """Two files the parser could not place, on the same day.

    The point of the pair: everything the catalogue knows about them is
    identical — the same title (*Unsorted*), the same date, so the same label —
    except what the parser derived from each file name.
    """
    now = utc_now()
    connection = sqlite3.connect(settings.db_path)
    try:
        library, bucket = connection.execute(
            "SELECT library_id, id FROM titles WHERE source = 'unsorted' LIMIT 1"
        ).fetchone()
        for number, derived in ((1, "Le Monde Diplomatique"), (2, "Harper's Magazine")):
            connection.execute(
                "INSERT INTO issues (id, library_id, title_id, rel_path, filename, size, "
                "mtime_ns, issue_date, date_precision, date_source, derived_title, label, "
                "matched_rule, added_at, updated_at) VALUES (?, ?, ?, ?, ?, 4096, 1, "
                "'2026-03-11', 'day', 'filename', ?, '11 March 2026', 'test', ?, ?)",
                (
                    f"stray{number}",
                    library,
                    bucket,
                    f"Zines/stray_{number}_11_Marzo_2026.pdf",
                    f"stray_{number}_11_Marzo_2026.pdf",
                    derived,
                    now,
                    now,
                ),
            )
        connection.commit()
    finally:
        connection.close()
    return "Le Monde Diplomatique — 11 March 2026", "Harper's Magazine — 11 March 2026"


def test_two_unsorted_files_of_a_day_are_told_apart(catalogue_settings: Settings) -> None:
    """ "Unsorted — 11 March 2026" twice over is a shelf nobody can read."""
    first, second = add_unsorted_pair(catalogue_settings)

    with serving(catalogue_settings) as client:
        _, root = fetch(client, "/opds/unsorted")

    titles = [text(entry, f"{ATOM}title") for entry in entries(root)]
    assert first in titles
    assert second in titles
    assert len(titles) == len(set(titles))
    assert not any(title.startswith("Unsorted") for title in titles)


def test_an_unsorted_entry_is_named_after_its_own_file(
    catalogue_settings: Settings,
) -> None:
    """The name reaches the author and the summary too, not only the title."""
    add_unsorted_pair(catalogue_settings)

    with serving(catalogue_settings) as client:
        _, root = fetch(client, "/opds/unsorted")

    entry = next(
        entry
        for entry in entries(root)
        if text(entry, f"{ATOM}title").startswith("Le Monde Diplomatique")
    )
    author = entry.find(f"{ATOM}author")
    assert author is not None and text(author, f"{ATOM}name") == "Le Monde Diplomatique"
    assert text(entry, f"{ATOM}summary").startswith("Le Monde Diplomatique · 11 March 2026 · ")
    # It is still filed in the bucket, and still nowhere else.
    with serving(catalogue_settings) as client:
        _, magazines = fetch(client, "/opds/kind/magazine")
    assert "Le Monde Diplomatique" not in [
        text(shelf, f"{ATOM}title") for shelf in entries(magazines)
    ]


def test_search_tells_two_unsorted_files_apart_as_well(
    catalogue_settings: Settings,
) -> None:
    first, second = add_unsorted_pair(catalogue_settings)

    with serving(catalogue_settings) as client:
        # Both files match by file name; only their derived titles differ.
        _, root = fetch(client, "/opds/search", q="stray_")

    titles = [text(entry, f"{ATOM}title") for entry in entries(root)]
    assert sorted(titles) == sorted([first, second])


def test_an_unsorted_file_with_nothing_derived_falls_back_to_its_name(
    catalogue_settings: Settings,
) -> None:
    """``derived_title`` is never null, but it can be empty."""
    add_unsorted_pair(catalogue_settings)
    rows(catalogue_settings.db_path, "UPDATE issues SET derived_title = '' WHERE id = 'stray1'")

    with serving(catalogue_settings) as client:
        _, root = fetch(client, "/opds/unsorted")

    titles = [text(entry, f"{ATOM}title") for entry in entries(root)]
    assert "stray_1_11_Marzo_2026 — 11 March 2026" in titles


def test_unsorted_is_offered_only_when_there_is_something_in_it(
    catalogue_settings: Settings,
) -> None:
    with serving(catalogue_settings) as client:
        _, root = fetch(client, "/opds")
        assert "Unsorted" in [text(entry, f"{ATOM}title") for entry in entries(root)]

    rows(
        catalogue_settings.db_path,
        "DELETE FROM issues WHERE title_id IN (SELECT id FROM titles WHERE source = 'unsorted')",
    )

    with serving(catalogue_settings) as client:
        _, root = fetch(client, "/opds")
        assert "Unsorted" not in [text(entry, f"{ATOM}title") for entry in entries(root)]
        # The feed itself stays reachable; it is only the front page that stops
        # advertising an empty shelf.
        _, empty = fetch(client, "/opds/unsorted")
        assert entries(empty) == []


# -------------------------------------------------------------- pagination


@pytest.fixture
def small_pages(monkeypatch: pytest.MonkeyPatch) -> int:
    """Five issues to a page, so three pages fit in the sample library."""
    monkeypatch.setattr(opds_router, "PAGE_SIZE", 5)
    return 5


def test_pagination_links_are_right_at_both_edges_and_in_the_middle(
    catalogue_settings: Settings, small_pages: int
) -> None:
    with serving(catalogue_settings) as client:
        title = next(
            title
            for title in api(client, "/api/titles", kind="newspaper")
            if title["issue_count"] == 14
        )
        path = f"/opds/titles/{title['id']}"

        _, first = fetch(client, path)
        assert len(entries(first)) == small_pages
        assert rels(first) >= {"first", "last", "next"}
        assert "previous" not in rels(first)
        assert href(first, "first").endswith(path)
        assert href(first, "last").endswith(f"{path}?page=3")
        assert href(first, "next").endswith(f"{path}?page=2")

        _, middle = fetch(client, path, page=2)
        assert len(entries(middle)) == small_pages
        assert href(middle, "previous").endswith(path)
        assert href(middle, "next").endswith(f"{path}?page=3")
        assert href(middle, "self").endswith(f"{path}?page=2")

        _, last = fetch(client, path, page=3)
        assert len(entries(last)) == 14 - 2 * small_pages
        assert href(last, "previous").endswith(f"{path}?page=2")
        assert "next" not in rels(last)

        # Every page is an acquisition feed, and says so on every page link.
        for link in links(middle):
            if link["rel"] in {"first", "previous", "next", "last"}:
                assert link["type"] == atom.ACQUISITION


def test_a_feed_that_fits_on_one_page_has_no_pagination_links(
    catalogue_client: TestClient,
) -> None:
    _, root = fetch(catalogue_client, "/opds/unsorted")

    assert rels(root) & {"first", "previous", "next", "last"} == set()


def test_a_page_past_the_end_is_empty_rather_than_an_error(
    catalogue_client: TestClient,
) -> None:
    _, root = fetch(catalogue_client, "/opds/unsorted", page=99)

    assert entries(root) == []


def test_page_zero_is_refused(catalogue_client: TestClient) -> None:
    assert catalogue_client.get("/opds/unsorted", params={"page": 0}).status_code == 422


@pytest.mark.parametrize(
    ("total", "size", "expected"), [(0, 5, 1), (1, 5, 1), (5, 5, 1), (6, 5, 2), (14, 5, 3)]
)
def test_the_page_count(total: int, size: int, expected: int) -> None:
    assert atom.page_count(total, size) == expected


# ------------------------------------------------------------------ search


def test_search_finds_issues_by_title(catalogue_client: TestClient) -> None:
    _, root = fetch(catalogue_client, "/opds/search", q="gazzetta")

    assert entries(root)
    for entry in entries(root):
        assert "gazzetta" in text(entry, f"{ATOM}title").lower()
    assert href(root, "self").endswith("/opds/search?q=gazzetta")
    assert text(root, f"{ATOM}title") == "Search: gazzetta"


def test_search_finds_issues_by_file_name(catalogue_client: TestClient) -> None:
    issue = api(catalogue_client, "/api/issues", limit=1)["items"][0]
    stem = issue["filename"].rsplit(".", 1)[0]

    _, root = fetch(catalogue_client, "/opds/search", q=stem)

    found = [text(entry, f"{DC}identifier") for entry in entries(root)]
    assert f"urn:paperstand:issue:{issue['id']}" in found


def test_a_search_that_finds_nothing_is_an_empty_feed(
    catalogue_client: TestClient,
) -> None:
    _, root = fetch(catalogue_client, "/opds/search", q="nothing-matches-this")

    assert entries(root) == []
    assert text(root, f"{ATOM}subtitle") == "0 results"


def test_a_search_wildcard_is_not_a_wildcard(catalogue_client: TestClient) -> None:
    """``%`` is what somebody typed, not "match anything"."""
    _, root = fetch(catalogue_client, "/opds/search", q="%")

    assert entries(root) == []


@pytest.mark.parametrize("params", [{}, {"q": ""}, {"q": "   "}])
def test_a_search_for_nothing_is_refused(
    catalogue_client: TestClient, params: dict[str, str]
) -> None:
    response = catalogue_client.get("/opds/search", params=params)

    assert response.status_code == 400
    assert "search" in response.json()["detail"]


def test_search_pagination_keeps_the_query(catalogue_settings: Settings, small_pages: int) -> None:
    with serving(catalogue_settings) as client:
        _, root = fetch(client, "/opds/search", q="gazzetta")

        assert href(root, "next").endswith("/opds/search?q=gazzetta&page=2")
        _, second = fetch(client, "/opds/search", q="gazzetta", page=2)
        assert entries(second)
        assert href(second, "previous").endswith("/opds/search?q=gazzetta")


def test_the_opensearch_description_points_at_the_search_feed(
    catalogue_client: TestClient,
) -> None:
    response = catalogue_client.get("/opds/opensearch.xml")

    assert response.status_code == 200
    assert response.headers["content-type"] == "application/opensearchdescription+xml;charset=utf-8"
    root = ET.fromstring(response.content)
    assert root.tag == "{http://a9.com/-/spec/opensearch/1.1/}OpenSearchDescription"
    url = root.find("{http://a9.com/-/spec/opensearch/1.1/}Url")
    assert url is not None
    assert url.attrib["type"] == atom.ACQUISITION
    assert url.attrib["template"].endswith("/opds/search?q={searchTerms}")
    assert url.attrib["template"].startswith("http://testserver/")

    # And the template, filled in, is a feed.
    filled = local(url.attrib["template"].replace("{searchTerms}", "gazzetta del lago"))
    assert catalogue_client.get(filled).status_code == 200


# ------------------------------------------------------------ absolute URLs


def every_href(payload: bytes) -> list[str]:
    """Every href in a feed, entries included."""
    return [link.attrib["href"] for link in ET.fromstring(payload).iter(f"{ATOM}link")]


@pytest.mark.parametrize("path", [path for path, _ in EVERY_FEED])
def test_the_base_url_setting_wins_over_the_request(
    catalogue_settings: Settings, path: str
) -> None:
    configured = quiet_settings(
        catalogue_settings.library,
        catalogue_settings.data,
        base_url="https://paperstand.example/",
    )

    with serving(configured) as client:
        response = client.get(path)

    hrefs = every_href(response.content)
    assert hrefs
    assert all(link.startswith("https://paperstand.example/") for link in hrefs), hrefs
    assert "https://paperstand.example//" not in " ".join(hrefs)


def test_a_trusted_proxys_headers_build_the_urls(catalogue_settings: Settings) -> None:
    forwarded = {"X-Forwarded-Proto": "https", "X-Forwarded-Host": "nas.local"}

    with TestClient(create_app(catalogue_settings), client=("127.0.0.1", 4321)) as client:
        response = client.get("/opds/today", headers=forwarded)

    hrefs = every_href(response.content)
    assert hrefs
    assert all(link.startswith("https://nas.local/") for link in hrefs), hrefs


def test_an_untrusted_client_cannot_rewrite_the_urls(
    catalogue_settings: Settings,
) -> None:
    """Anyone can send these headers; only the configured proxies are believed."""
    forwarded = {"X-Forwarded-Proto": "https", "X-Forwarded-Host": "evil.example"}

    with serving(catalogue_settings) as client:
        response = client.get("/opds", headers=forwarded)

    assert all(link.startswith("http://testserver/") for link in every_href(response.content))


def test_every_proxy_can_be_trusted_on_a_port_only_a_proxy_reaches(
    catalogue_settings: Settings,
) -> None:
    trusting = quiet_settings(
        catalogue_settings.library, catalogue_settings.data, trusted_proxies="*"
    )
    forwarded = {"X-Forwarded-Proto": "https", "X-Forwarded-Host": "nas.local:8443"}

    with serving(trusting) as client:
        response = client.get("/opds", headers=forwarded)

    assert all(link.startswith("https://nas.local:8443/") for link in every_href(response.content))


def test_a_forwarded_host_that_could_not_be_one_is_ignored(
    catalogue_settings: Settings,
) -> None:
    """A trusted proxy is still not allowed to put anything at all in a URL."""
    with TestClient(create_app(catalogue_settings), client=("127.0.0.1", 4321)) as client:
        response = client.get("/opds", headers={"X-Forwarded-Host": "nas.local/evil?"})

    assert all(link.startswith("http://testserver/") for link in every_href(response.content))


# --------------------------------------------------------- trusted proxies


@pytest.mark.parametrize(
    ("trusted", "client", "believed"),
    [
        ("127.0.0.1", "127.0.0.1", True),
        ("127.0.0.1", "10.0.0.5", False),
        ("127.0.0.1", None, False),
        ("127.0.0.1", "", False),
        ("10.0.0.0/8", "10.1.2.3", True),
        ("10.0.0.0/8", "192.168.0.1", False),
        ("127.0.0.1, 172.18.0.0/16", "172.18.4.9", True),
        ("*", "203.0.113.7", True),
        ("::1", "::1", True),
        # Not an address at all — a Unix socket peer — so compared as a string.
        ("unix", "unix", True),
        ("unix", "other", False),
    ],
)
def test_which_clients_are_believed(trusted: str, client: str | None, believed: bool) -> None:
    assert (client in TrustedProxies(trusted)) is believed


@pytest.mark.parametrize(
    ("trusted", "forwarded", "client"),
    [
        # One proxy: the only entry is the client.
        ("127.0.0.1", "203.0.113.7", "203.0.113.7"),
        # Two: the last hop is a trusted proxy, so the client is the one before it.
        ("10.0.0.0/8", "203.0.113.7, 10.0.0.2", "203.0.113.7"),
        ("*", "203.0.113.7, 10.0.0.2", "203.0.113.7"),
        ("127.0.0.1", "", None),
    ],
)
def test_which_hop_of_a_forwarded_chain_is_the_client(
    trusted: str, forwarded: str, client: str | None
) -> None:
    assert TrustedProxies(trusted).client_from(forwarded) == client


def test_a_forwarded_for_does_not_stop_the_host_being_applied(
    catalogue_settings: Settings,
) -> None:
    """The three headers arrive together, and all three have to be honoured.

    This is the shape of a real request from a reverse proxy, and the reason
    Uvicorn's own middleware is switched off: running outside the application, it
    rewrites the client address from ``X-Forwarded-For`` *before* the trust check
    below, which then sees the end user's address instead of the proxy's and
    refuses to believe the host.
    """
    forwarded = {
        "X-Forwarded-Proto": "https",
        "X-Forwarded-Host": "nas.local",
        "X-Forwarded-For": "203.0.113.7, 127.0.0.1",
    }

    with TestClient(create_app(catalogue_settings), client=("127.0.0.1", 4321)) as client:
        response = client.get("/opds/today", headers=forwarded)

    hrefs = every_href(response.content)
    assert hrefs
    assert all(link.startswith("https://nas.local/") for link in hrefs), hrefs


def test_the_server_leaves_the_forwarded_headers_to_the_application(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``paperstand serve`` must not switch Uvicorn's middleware on as well.

    Two implementations of the same thing, one inside the application and one
    outside it, disagree about which hop to believe — and the outer one wins,
    because it runs first. `make dev-api` passes `--no-proxy-headers` for the
    same reason.
    """
    import uvicorn

    from paperstand.__main__ import serve

    recorded: dict[str, Any] = {}

    def fake_run(app: str, **options: Any) -> None:
        recorded.update(options)
        recorded["app"] = app

    monkeypatch.setattr(uvicorn, "run", fake_run)
    serve(host="127.0.0.1", port=9999)

    assert recorded["app"] == "paperstand.main:app"
    assert recorded["proxy_headers"] is False


def test_the_makefile_starts_the_dev_server_without_uvicorns_middleware() -> None:
    """The documented development command goes through the same one path."""
    makefile = (Path(__file__).resolve().parents[2] / "Makefile").read_text("utf-8")
    dev_api = makefile.split("dev-api:", 1)[1].split("\ndev-web:", 1)[0]

    assert "uvicorn paperstand.main:app" in dev_api
    assert "--no-proxy-headers" in dev_api


# -------------------------------------------------------------- escaping


AWKWARD = "Q&A <Weekly>"


def add_awkward_title(settings: Settings) -> str:
    """A title whose name is a reminder that a feed is XML."""
    now = utc_now()
    connection = sqlite3.connect(settings.db_path)
    try:
        library = str(connection.execute("SELECT id FROM libraries LIMIT 1").fetchone()[0])
        connection.execute(
            "INSERT INTO titles (id, library_id, name, sort_name, kind, source, created_at) "
            "VALUES ('awkward', ?, ?, 'q&a weekly', 'magazine', 'config', ?)",
            (library, AWKWARD, now),
        )
        connection.execute(
            "INSERT INTO issues (id, library_id, title_id, rel_path, filename, size, mtime_ns, "
            "issue_date, date_precision, date_source, derived_title, label, matched_rule, "
            "added_at, updated_at) VALUES ('awkward1', ?, 'awkward', 'Zines/q&a.pdf', "
            "'q&a <1>.pdf', 1234, 1, '2026-03-17', 'day', 'filename', ?, '17 March 2026', "
            "'test', ?, ?)",
            (library, AWKWARD, now, now),
        )
        connection.commit()
    finally:
        connection.close()
    return AWKWARD


def test_a_title_full_of_xml_comes_back_out_intact(catalogue_settings: Settings) -> None:
    add_awkward_title(catalogue_settings)

    with serving(catalogue_settings) as client:
        kinds = client.get("/opds/kind/magazine")
        issues = client.get("/opds/titles/awkward")

    assert b"Q&amp;A &lt;Weekly&gt;" in kinds.content
    assert b"<Weekly>" not in kinds.content
    assert feedparser.parse(kinds.content).bozo == 0
    names = [text(entry, f"{ATOM}title") for entry in entries(ET.fromstring(kinds.content))]
    assert AWKWARD in names

    entry = entries(ET.fromstring(issues.content))[0]
    assert text(entry, f"{ATOM}title") == f"{AWKWARD} — 17 March 2026"
    author = entry.find(f"{ATOM}author")
    assert author is not None and text(author, f"{ATOM}name") == AWKWARD


def test_an_awkward_title_can_be_searched_for(catalogue_settings: Settings) -> None:
    add_awkward_title(catalogue_settings)

    with serving(catalogue_settings) as client:
        _, root = fetch(client, "/opds/search", q="Q&A")

    assert [text(entry, f"{ATOM}title") for entry in entries(root)] == [
        f"{AWKWARD} — 17 March 2026"
    ]


# ----------------------------------------------------------------- wording


@pytest.mark.parametrize(
    ("size", "expected"),
    [
        (0, "0 B"),
        (999, "999 B"),
        (1_000, "1.0 kB"),
        (41_800_000, "41.8 MB"),
        (2_500_000_000, "2.5 GB"),
    ],
)
def test_human_size(size: int, expected: str) -> None:
    assert opds_router.human_size(size) == expected


@pytest.mark.parametrize(
    ("value", "expected"),
    [("2026-03-17", "17 March 2026"), ("2026-01-01", "1 January 2026"), ("later", "later")],
)
def test_human_date(value: str, expected: str) -> None:
    assert opds_router.human_date(value) == expected


# -------------------------------------------------- an empty or absent catalogue


def test_a_catalogue_with_nothing_in_it_still_serves_a_feed(tmp_path: Path) -> None:
    """No library, no scan, no issues: still a valid Atom document."""
    library, data = tmp_path / "library", tmp_path / "data"
    library.mkdir()
    data.mkdir()

    with serving(quiet_settings(library, data)) as client:
        _, root = fetch(client, "/opds")
        _, today_feed = fetch(client, "/opds/today")

    # No scan has ever finished, so `updated` falls back to the current instant.
    assert text(root, f"{ATOM}updated")
    assert entries(today_feed) == []
    # And with nothing unplaced, the front page does not offer the bucket.
    assert [text(entry, f"{ATOM}title") for entry in entries(root)] == [
        "Today",
        "Newspapers",
        "Magazines",
        "Recently added",
    ]


def test_a_catalogue_that_could_not_be_opened_is_a_503(settings: Settings) -> None:
    """The same answer every other catalogue endpoint gives."""
    app = FastAPI()
    app.include_router(opds_router.create_router(settings, None))

    with TestClient(app) as client:
        for path in ("/opds", "/opds/today", "/opds/kind/newspaper"):
            assert client.get(path).status_code == 503, path
