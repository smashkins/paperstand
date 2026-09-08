"""Atom documents, as OPDS 1.2 wants them.

Nothing here knows what a periodical is. It knows what a feed, an entry and a
link look like, which media type says "this feed is a menu" and which says "this
feed is a shelf you can download from", and how a page of a long feed points at
the pages either side of it. The catalogue is turned into these shapes in
:mod:`paperstand.opds.router`.

**Namespaces are declared literally.** The elements are named ``dc:date`` rather
than ``{http://purl.org/dc/terms/}date``, and the three declarations are written
onto the root element by hand. ``ElementTree`` can do this properly, through
``register_namespace``, but only through a process-wide table that maps one URI
to one prefix — which cannot express "Atom is the default namespace here and
OpenSearch is the default namespace there", and which would make the output of
this module depend on what else in the process had registered a prefix. Written
out by hand, the bytes are exactly what the specification's examples show, and
they are the same bytes whatever else is running.

Escaping is still ``ElementTree``'s: every value goes through ``Element.set``
and ``Element.text``, so a title containing ``&`` or ``<`` — and there are such
periodicals — comes out as ``&amp;`` and ``&lt;`` without anybody having to
remember to do it.
"""

from __future__ import annotations

import xml.etree.ElementTree as ET
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field

ATOM_NS = "http://www.w3.org/2005/Atom"
DC_NS = "http://purl.org/dc/terms/"
OPDS_NS = "http://opds-spec.org/2010/catalog"
OPENSEARCH_NS = "http://a9.com/-/spec/opensearch/1.1/"

#: Whose catalogue this is. One name, in every feed.
AUTHOR = "Paperstand"

# ------------------------------------------------------------- media types

#: The profile that makes an Atom feed an OPDS catalogue.
CATALOG = "application/atom+xml;profile=opds-catalog"

#: A feed of *entries that lead somewhere else*: the root, the two shelves of
#: titles. A client draws it as a menu.
NAVIGATION = f"{CATALOG};kind=navigation"

#: A feed of *entries that can be downloaded*: issues, with covers and a PDF.
ACQUISITION = f"{CATALOG};kind=acquisition"

OPENSEARCH_TYPE = "application/opensearchdescription+xml"
JPEG = "image/jpeg"
PDF = "application/pdf"


def served(media_type: str) -> str:
    """The media type as a ``Content-Type`` header: XML, and it is UTF-8.

    Starlette only adds a charset to ``text/*``, and a feed whose encoding a
    client has to guess at is a feed that renders an accented title as mojibake
    on somebody's phone.
    """
    return f"{media_type};charset=utf-8"


# -------------------------------------------------------------- link types

SELF = "self"
START = "start"
UP = "up"
SEARCH = "search"
SUBSECTION = "subsection"
NEXT = "next"
PREVIOUS = "previous"
FIRST = "first"
LAST = "last"

#: The cover, at full size, and the thumbnail a grid is drawn with.
IMAGE_REL = "http://opds-spec.org/image"
THUMBNAIL_REL = "http://opds-spec.org/image/thumbnail"

#: "This entry can be downloaded, and here is where from."
ACQUISITION_REL = "http://opds-spec.org/acquisition"

# Post-MVP: OPDS-PSE (`xmlns:pse="http://vaemendis.net/opds-pse/ns"`) adds a
# `rel="http://vaemendis.net/opds-pse/stream"` link whose href carries `{pageNumber}`
# and `{maxWidth}` placeholders, letting a client page through an issue without
# downloading it — Paperstand already renders exactly that, at
# `/api/issues/{id}/pages/{n}.webp?w={w}`. It is deliberately not emitted in
# 0.1.0: the extension expects `pse:count` on the link and JPEG pages, and both
# want deciding on with a client in hand rather than from the specification.


@dataclass(frozen=True, slots=True)
class Link:
    """One ``<link>``: where it points, what it is, and why it is there."""

    rel: str
    href: str
    type: str | None = None
    title: str | None = None


@dataclass(frozen=True, slots=True)
class Entry:
    """One ``<entry>``, in the two shapes OPDS uses.

    A *navigation* entry has a title, a link and usually a ``content`` line; an
    *acquisition* entry adds the author, the category, the summary and the dates
    a reading app sorts and groups by. The optional fields are simply left out
    when they are not known, which is the whole difference between the two.
    """

    id: str
    title: str
    updated: str
    links: Sequence[Link] = ()
    author: str | None = None
    content: str | None = None
    summary: str | None = None
    category: tuple[str, str] | None = None
    dc_date: str | None = None
    dc_identifier: str | None = None


@dataclass(frozen=True, slots=True)
class Feed:
    """A complete feed, ready to be rendered."""

    id: str
    title: str
    updated: str
    links: Sequence[Link] = ()
    entries: Sequence[Entry] = field(default_factory=tuple)
    subtitle: str | None = None


# ---------------------------------------------------------------- building


def _text(parent: ET.Element, tag: str, value: str, **attributes: str) -> ET.Element:
    """A child element carrying text, and nothing else."""
    element = ET.SubElement(parent, tag, attributes)
    element.text = value
    return element


def _link(parent: ET.Element, link: Link) -> ET.Element:
    attributes = {"rel": link.rel, "href": link.href}
    if link.type is not None:
        attributes["type"] = link.type
    if link.title is not None:
        attributes["title"] = link.title
    return ET.SubElement(parent, "link", attributes)


def _entry(parent: ET.Element, entry: Entry) -> ET.Element:
    element = ET.SubElement(parent, "entry")
    _text(element, "id", entry.id)
    _text(element, "title", entry.title)
    _text(element, "updated", entry.updated)
    if entry.author is not None:
        _text(ET.SubElement(element, "author"), "name", entry.author)
    if entry.dc_identifier is not None:
        _text(element, "dc:identifier", entry.dc_identifier)
    if entry.dc_date is not None:
        _text(element, "dc:date", entry.dc_date)
    if entry.category is not None:
        term, label = entry.category
        ET.SubElement(element, "category", {"term": term, "label": label})
    if entry.content is not None:
        _text(element, "content", entry.content, type="text")
    if entry.summary is not None:
        _text(element, "summary", entry.summary, type="text")
    for link in entry.links:
        _link(element, link)
    return element


def build(feed: Feed) -> ET.Element:
    """Turn a :class:`Feed` into the element tree that is serialised."""
    root = ET.Element(
        "feed",
        {
            "xmlns": ATOM_NS,
            "xmlns:dc": DC_NS,
            # Declared even though 0.1.0 emits no element from it: it is part of
            # what makes the document an OPDS catalogue, and the extensions that
            # need it (facets, prices, indirect acquisition) are one element away.
            "xmlns:opds": OPDS_NS,
        },
    )
    _text(root, "id", feed.id)
    _text(root, "title", feed.title)
    if feed.subtitle is not None:
        _text(root, "subtitle", feed.subtitle)
    _text(root, "updated", feed.updated)
    _text(ET.SubElement(root, "author"), "name", AUTHOR)
    for link in feed.links:
        _link(root, link)
    for entry in feed.entries:
        _entry(root, entry)
    return root


def render(root: ET.Element) -> bytes:
    """The document as bytes, with the XML declaration a feed must carry."""
    # `tostring` is overloaded on `encoding` and typed as returning `Any` for
    # the byte-producing branch; the annotation is what makes the callers safe.
    payload: bytes = ET.tostring(root, encoding="utf-8", xml_declaration=True)
    return payload


def feed_bytes(feed: Feed) -> bytes:
    """Build and render in one step, which is all a route ever wants."""
    return render(build(feed))


def opensearch_bytes(short_name: str, description: str, template: str) -> bytes:
    """The OpenSearch description that tells a client how to search.

    One ``Url`` template, pointing at the search feed with ``{searchTerms}``
    where the query goes. The element names are unprefixed and the namespace is
    the default one, which is the form every client's examples are written in.
    """
    root = ET.Element("OpenSearchDescription", {"xmlns": OPENSEARCH_NS})
    _text(root, "ShortName", short_name)
    _text(root, "Description", description)
    _text(root, "InputEncoding", "UTF-8")
    _text(root, "OutputEncoding", "UTF-8")
    ET.SubElement(root, "Url", {"type": ACQUISITION, "template": template})
    return render(root)


# -------------------------------------------------------------- pagination


def page_count(total: int, size: int) -> int:
    """How many pages ``total`` items make, at least one."""
    if total <= 0 or size <= 0:
        return 1
    return (total + size - 1) // size


def page_links(href: Callable[[int], str], page: int, pages: int, media_type: str) -> list[Link]:
    """``first``, ``previous``, ``next`` and ``last`` for one page of a feed.

    Nothing is emitted for a feed that fits on one page: a ``first`` and a
    ``last`` both pointing at the feed the client is already reading are two
    buttons that do nothing. ``href`` builds the URL of a page, so a feed that
    carries other query parameters — the search feed carries ``q`` — keeps them.
    """
    if pages <= 1:
        return []
    links = [Link(FIRST, href(1), media_type), Link(LAST, href(pages), media_type)]
    if page > 1:
        links.append(Link(PREVIOUS, href(page - 1), media_type))
    if page < pages:
        links.append(Link(NEXT, href(page + 1), media_type))
    return links
