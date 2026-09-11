"""``GET|HEAD /api/issues/{id}/file``: ranges, validators and path safety."""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from paperstand.api.common import etag_matches, resolve_in_library
from paperstand.config import Settings
from tests.conftest import sample_issue_id

A_NEWSPAPER = "Newspapers/2026/03/17/Corriere_del_Ponte_17_Marzo_2026.pdf"


@pytest.fixture
def issue_id_(catalogue_settings: Settings) -> str:
    """The id every test in this file streams, hashed from this test's own copy."""
    return sample_issue_id(catalogue_settings.library, A_NEWSPAPER)


@pytest.fixture
def url(issue_id_: str) -> str:
    return f"/api/issues/{issue_id_}/file"


@pytest.fixture
def pdf_size(catalogue_settings: Settings) -> int:
    """The size on disk of the issue every test in this file streams."""
    return (catalogue_settings.library / A_NEWSPAPER).stat().st_size


def clone_issue(db_path: Path, source_id: str, rel_path: str, identifier: str) -> str:
    """Copy an issue row, pointing the copy at ``rel_path``.

    The only way to get a hostile ``rel_path`` into the catalogue: no scan would
    ever write one, which is exactly why the endpoint must not trust that.
    """
    connection = sqlite3.connect(db_path)
    connection.row_factory = sqlite3.Row
    try:
        row = connection.execute("SELECT * FROM issues WHERE id = ?", (source_id,)).fetchone()
        values = dict(row)
        values["id"] = identifier
        values["rel_path"] = rel_path
        columns = ", ".join(values)
        placeholders = ", ".join(f":{name}" for name in values)
        connection.execute(f"INSERT INTO issues ({columns}) VALUES ({placeholders})", values)
        connection.commit()
    finally:
        connection.close()
    return identifier


# -------------------------------------------------------------------- basics


def test_the_whole_document_is_served_inline(
    catalogue_client: TestClient, pdf_size: int, url: str, issue_id_: str
) -> None:
    response = catalogue_client.get(url)

    assert response.status_code == 200
    assert response.headers["content-type"] == "application/pdf"
    assert response.headers["accept-ranges"] == "bytes"
    assert response.headers["content-length"] == str(pdf_size)
    assert response.headers["cache-control"] == "private, max-age=0, must-revalidate"
    assert response.headers["content-disposition"] == (
        "inline; filename*=UTF-8''Corriere_del_Ponte_17_Marzo_2026.pdf"
    )
    assert response.headers["etag"].startswith(f'"{issue_id_}-')
    assert "last-modified" in response.headers
    assert len(response.content) == pdf_size
    assert response.content.startswith(b"%PDF")


def test_head_returns_the_headers_and_no_body(
    catalogue_client: TestClient, pdf_size: int, url: str
) -> None:
    head = catalogue_client.head(url)
    get = catalogue_client.get(url)

    assert head.status_code == 200
    assert head.headers["accept-ranges"] == "bytes"
    assert head.headers["content-length"] == str(pdf_size)
    assert head.headers["etag"] == get.headers["etag"]
    assert head.headers["last-modified"] == get.headers["last-modified"]
    assert head.content == b""


def test_the_response_is_never_content_encoded(catalogue_client: TestClient, url: str) -> None:
    """A compressed body would move every offset a range reader computed."""
    response = catalogue_client.get(url, headers={"Accept-Encoding": "gzip, deflate, br"})

    assert response.status_code == 200
    assert "content-encoding" not in {name.lower() for name in response.headers}
    assert response.content.startswith(b"%PDF")


# -------------------------------------------------------------------- ranges


def test_a_leading_range(catalogue_client: TestClient, pdf_size: int, url: str) -> None:
    response = catalogue_client.get(url, headers={"Range": "bytes=0-1023"})

    assert response.status_code == 206
    assert response.headers["content-range"] == f"bytes 0-1023/{pdf_size}"
    assert response.headers["content-length"] == "1024"
    assert len(response.content) == 1024


def test_a_suffix_range(catalogue_client: TestClient, pdf_size: int, url: str) -> None:
    response = catalogue_client.get(url, headers={"Range": "bytes=-100"})
    whole = catalogue_client.get(url).content

    assert response.status_code == 206
    assert response.headers["content-range"] == f"bytes {pdf_size - 100}-{pdf_size - 1}/{pdf_size}"
    assert response.headers["content-length"] == "100"
    assert response.content == whole[-100:]


def test_an_open_ended_range(catalogue_client: TestClient, pdf_size: int, url: str) -> None:
    response = catalogue_client.get(url, headers={"Range": "bytes=100-"})
    whole = catalogue_client.get(url).content

    assert response.status_code == 206
    assert response.headers["content-range"] == f"bytes 100-{pdf_size - 1}/{pdf_size}"
    assert response.headers["content-length"] == str(pdf_size - 100)
    assert response.content == whole[100:]


def test_a_range_past_the_end_is_not_satisfiable(
    catalogue_client: TestClient, pdf_size: int, url: str
) -> None:
    response = catalogue_client.get(
        url, headers={"Range": f"bytes={pdf_size + 10}-{pdf_size + 20}"}
    )

    assert response.status_code == 416
    assert response.headers["content-range"] == f"bytes */{pdf_size}"


# -------------------------------------------------------------- conditionals


def test_a_matching_validator_is_a_304(catalogue_client: TestClient, url: str) -> None:
    etag = catalogue_client.head(url).headers["etag"]

    response = catalogue_client.get(url, headers={"If-None-Match": etag})

    assert response.status_code == 304
    assert response.headers["etag"] == etag
    assert response.content == b""


def test_a_stale_validator_is_the_whole_document(catalogue_client: TestClient, url: str) -> None:
    response = catalogue_client.get(url, headers={"If-None-Match": '"something-else"'})

    assert response.status_code == 200


def test_if_range_keeps_a_range_only_while_the_validator_holds(
    catalogue_client: TestClient, pdf_size: int, url: str
) -> None:
    etag = catalogue_client.head(url).headers["etag"]

    matching = catalogue_client.get(url, headers={"Range": "bytes=0-9", "If-Range": etag})
    stale = catalogue_client.get(url, headers={"Range": "bytes=0-9", "If-Range": '"gone-stale"'})

    assert matching.status_code == 206
    assert stale.status_code == 200
    assert len(stale.content) == pdf_size


@pytest.mark.parametrize(
    ("header", "expected"),
    [
        (None, False),
        ("", False),
        ('"a"', True),
        ('W/"a"', True),
        ('"b", "a"', True),
        ("*", True),
        ('"b"', False),
    ],
)
def test_validator_matching(header: str | None, expected: bool) -> None:
    assert etag_matches(header, '"a"') is expected


# ------------------------------------------------------------------- safety


def test_an_unknown_issue_is_a_404(catalogue_client: TestClient) -> None:
    assert catalogue_client.get("/api/issues/nope/file").status_code == 404


def test_a_file_that_vanished_is_a_404(
    catalogue_client: TestClient, catalogue_settings: Settings, url: str
) -> None:
    (catalogue_settings.library / A_NEWSPAPER).unlink()

    assert catalogue_client.get(url).status_code == 404


@pytest.mark.parametrize(
    "rel_path",
    [
        "../../../etc/passwd",
        "Newspapers/../../outside.pdf",
        "/etc/passwd",
        "Newspapers/2026/../../../../etc/hosts",
    ],
)
def test_a_row_pointing_outside_the_library_is_refused(
    catalogue_settings: Settings, catalogue_client: TestClient, rel_path: str, issue_id_: str
) -> None:
    """A hostile ``rel_path`` cannot reach a byte outside the library root."""
    outside = catalogue_settings.library.parent / "outside.pdf"
    outside.write_bytes(b"%PDF-1.4 not yours\n")
    identifier = clone_issue(catalogue_settings.db_path, issue_id_, rel_path, "0000escape000000")

    response = catalogue_client.get(f"/api/issues/{identifier}/file")

    assert response.status_code == 404
    assert b"not yours" not in response.content


def test_resolve_refuses_to_leave_the_root(tmp_path: Path) -> None:
    root = tmp_path / "library"
    (root / "Newspapers").mkdir(parents=True)
    (root / "Newspapers" / "one.pdf").write_bytes(b"%PDF")
    (tmp_path / "outside.pdf").write_bytes(b"%PDF")

    assert resolve_in_library(root, "Newspapers/one.pdf") == (root / "Newspapers/one.pdf")
    assert resolve_in_library(root, "../outside.pdf") is None
    assert resolve_in_library(root, "/etc/passwd") is None
    assert resolve_in_library(root, "") is None


def test_a_symlink_out_of_the_library_is_refused(
    catalogue_settings: Settings, catalogue_client: TestClient, issue_id_: str
) -> None:
    outside = catalogue_settings.library.parent / "elsewhere.pdf"
    outside.write_bytes(b"%PDF-1.4 not yours\n")
    link = catalogue_settings.library / "link.pdf"
    link.symlink_to(outside)
    identifier = clone_issue(catalogue_settings.db_path, issue_id_, "link.pdf", "0000symlink00000")

    response = catalogue_client.get(f"/api/issues/{identifier}/file")

    assert response.status_code == 404
