"""``/api/today`` and the reading progress endpoints."""

from __future__ import annotations

import datetime as dt
import sqlite3
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from paperstand.api.progress import clamp
from paperstand.api.today import local_today
from paperstand.config import Settings
from tests.conftest import (
    SAMPLE_TODAY,
    add_user,
    app_of,
    quiet_settings,
    sample_issue_id,
    schema_4_catalogue,
    signed_in_client,
)

A_NEWSPAPER = "Newspapers/2026/03/17/Corriere_del_Ponte_17_Marzo_2026.pdf"


def today(client: TestClient, **params: Any) -> dict[str, Any]:
    response = client.get("/api/today", params=params)
    assert response.status_code == 200
    payload: dict[str, Any] = response.json()
    return payload


def mark_duplicate(db_path: Path, issue_id: str, winner: str) -> None:
    """Point one issue at another, the way a later scan would."""
    connection = sqlite3.connect(db_path)
    try:
        connection.execute("UPDATE issues SET duplicate_of = ? WHERE id = ?", (winner, issue_id))
        connection.commit()
    finally:
        connection.close()


def mark_missing(db_path: Path, issue_id: str) -> None:
    """Mark an issue missing, the way a scan would once its file vanished."""
    connection = sqlite3.connect(db_path)
    try:
        connection.execute(
            "UPDATE issues SET missing_since = datetime('now'), duplicate_of = NULL WHERE id = ?",
            (issue_id,),
        )
        connection.commit()
    finally:
        connection.close()


# -------------------------------------------------------------------- today


def test_an_exact_date_gives_one_issue_per_daily_title(catalogue_client: TestClient) -> None:
    payload = today(catalogue_client, date=SAMPLE_TODAY.isoformat())

    assert payload["date"] == payload["newspapers_date"] == SAMPLE_TODAY.isoformat()
    assert payload["is_today"] is False  # the sample library's "today" is fixed
    assert payload["newspapers"]
    titles = [issue["title_id"] for issue in payload["newspapers"]]
    assert len(titles) == len(set(titles))
    assert all(issue["kind"] == "newspaper" for issue in payload["newspapers"])
    assert all(issue["issue_date"] == SAMPLE_TODAY.isoformat() for issue in payload["newspapers"])
    assert all(issue["is_duplicate"] is False for issue in payload["newspapers"])


def test_a_day_with_nothing_on_it_falls_back_and_says_so(catalogue_client: TestClient) -> None:
    asked = SAMPLE_TODAY + dt.timedelta(days=3)

    payload = today(catalogue_client, date=asked.isoformat())

    assert payload["date"] == asked.isoformat()
    assert payload["newspapers_date"] == SAMPLE_TODAY.isoformat()
    assert payload["newspapers"]
    assert all(issue["issue_date"] == SAMPLE_TODAY.isoformat() for issue in payload["newspapers"])


def test_a_day_before_the_library_begins_has_no_newspapers(catalogue_client: TestClient) -> None:
    payload = today(catalogue_client, date="1999-01-01")

    assert payload["newspapers"] == []
    assert payload["newspapers_date"] is None


def test_magazines_are_the_latest_of_each_title_newest_first(
    catalogue_client: TestClient,
) -> None:
    payload = today(catalogue_client, date=SAMPLE_TODAY.isoformat())

    magazines = payload["magazines"]
    assert magazines
    assert all(issue["kind"] == "magazine" for issue in magazines)
    titles = [issue["title_id"] for issue in magazines]
    assert len(titles) == len(set(titles))
    dates = [issue["issue_date"] or "" for issue in magazines]
    assert dates == sorted(dates, reverse=True)
    for issue in magazines:
        newer = catalogue_client.get(
            "/api/issues", params={"title": issue["title_id"], "limit": 1}
        ).json()
        assert newer["items"][0]["id"] == issue["id"]


def test_recently_added_is_capped_and_skips_duplicates(catalogue_client: TestClient) -> None:
    payload = today(catalogue_client, date=SAMPLE_TODAY.isoformat())

    recent = payload["recently_added"]
    assert 0 < len(recent) <= 12
    assert all(issue["is_duplicate"] is False for issue in recent)
    assert [issue["added_at"] for issue in recent] == sorted(
        (issue["added_at"] for issue in recent), reverse=True
    )


def test_continue_reading_holds_what_was_started_and_not_finished(
    catalogue_client: TestClient, catalogue_settings: Settings
) -> None:
    identifier = sample_issue_id(catalogue_settings.library, A_NEWSPAPER)
    issue = catalogue_client.get(f"/api/issues/{identifier}").json()
    assert issue["page_count"] >= 3

    assert today(catalogue_client, date=SAMPLE_TODAY.isoformat())["continue_reading"] == []

    catalogue_client.put(f"/api/issues/{identifier}/progress", json={"page": 2})
    started = today(catalogue_client, date=SAMPLE_TODAY.isoformat())["continue_reading"]

    assert [item["id"] for item in started] == [identifier]
    assert started[0]["progress"] == {
        "page": 2,
        "page_count": issue["page_count"],
        "updated_at": started[0]["progress"]["updated_at"],
    }

    # The first page is "not started" and the last one is "finished": neither is
    # something to be offered as unfinished reading.
    catalogue_client.put(f"/api/issues/{identifier}/progress", json={"page": 1})
    assert today(catalogue_client, date=SAMPLE_TODAY.isoformat())["continue_reading"] == []
    catalogue_client.put(f"/api/issues/{identifier}/progress", json={"page": issue["page_count"]})
    assert today(catalogue_client, date=SAMPLE_TODAY.isoformat())["continue_reading"] == []


def test_a_missing_newspaper_is_absent_from_today_and_from_continue_reading(
    catalogue_client: TestClient, catalogue_settings: Settings
) -> None:
    identifier = sample_issue_id(catalogue_settings.library, A_NEWSPAPER)
    catalogue_client.put(f"/api/issues/{identifier}/progress", json={"page": 2})
    assert today(catalogue_client, date=SAMPLE_TODAY.isoformat())["newspapers_date"] == (
        SAMPLE_TODAY.isoformat()
    )

    mark_missing(catalogue_settings.db_path, identifier)

    payload = today(catalogue_client, date=SAMPLE_TODAY.isoformat())
    assert identifier not in {issue["id"] for issue in payload["newspapers"]}
    assert identifier not in {issue["id"] for issue in payload["continue_reading"]}
    assert identifier not in {issue["id"] for issue in payload["recently_added"]}


def test_today_defaults_to_the_day_in_the_configured_zone(catalogue_client: TestClient) -> None:
    payload = today(catalogue_client)

    assert payload["is_today"] is True
    assert payload["date"] == dt.datetime.now().astimezone().date().isoformat()


def test_a_malformed_date_is_refused(catalogue_client: TestClient) -> None:
    assert catalogue_client.get("/api/today", params={"date": "yesterday"}).status_code == 422


@pytest.mark.parametrize("zone", ["Pacific/Kiritimati", "Pacific/Niue"])
def test_the_configured_zone_decides_what_today_is(settings: Settings, zone: str) -> None:
    """Two zones a day apart cannot both think it is the same date."""
    configured = quiet_settings(settings.library, settings.data, tz=zone)

    assert local_today(configured) == dt.datetime.now(_zone(zone)).date()


def test_an_unknown_zone_falls_back_to_the_system_one(settings: Settings) -> None:
    configured = quiet_settings(settings.library, settings.data, tz="Mars/Olympus_Mons")

    assert local_today(configured) == dt.datetime.now().astimezone().date()


def _zone(name: str) -> dt.tzinfo:
    from zoneinfo import ZoneInfo

    return ZoneInfo(name)


# ----------------------------------------------------------------- progress


def test_progress_is_written_read_listed_and_deleted(
    catalogue_client: TestClient, catalogue_settings: Settings
) -> None:
    identifier = sample_issue_id(catalogue_settings.library, A_NEWSPAPER)
    page_count = catalogue_client.get(f"/api/issues/{identifier}").json()["page_count"]

    assert catalogue_client.get(f"/api/issues/{identifier}/progress").status_code == 404
    assert catalogue_client.get("/api/progress").json() == []

    written = catalogue_client.put(f"/api/issues/{identifier}/progress", json={"page": 2})
    assert written.status_code == 200
    assert written.json()["issue_id"] == identifier
    assert written.json()["page"] == 2
    assert written.json()["page_count"] == page_count

    assert catalogue_client.get(f"/api/issues/{identifier}/progress").json() == written.json()
    listed = catalogue_client.get("/api/progress").json()
    assert [item["id"] for item in listed] == [identifier]
    assert listed[0]["progress"]["page"] == 2

    assert catalogue_client.delete(f"/api/issues/{identifier}/progress").status_code == 204
    assert catalogue_client.get(f"/api/issues/{identifier}/progress").status_code == 404
    assert catalogue_client.get("/api/progress").json() == []


def test_a_page_beyond_the_document_is_clamped(
    catalogue_client: TestClient, catalogue_settings: Settings
) -> None:
    identifier = sample_issue_id(catalogue_settings.library, A_NEWSPAPER)
    page_count = catalogue_client.get(f"/api/issues/{identifier}").json()["page_count"]

    stored = catalogue_client.put(f"/api/issues/{identifier}/progress", json={"page": 9999}).json()

    assert stored["page"] == page_count


def test_a_page_below_one_is_refused(
    catalogue_client: TestClient, catalogue_settings: Settings
) -> None:
    identifier = sample_issue_id(catalogue_settings.library, A_NEWSPAPER)

    assert (
        catalogue_client.put(f"/api/issues/{identifier}/progress", json={"page": 0}).status_code
        == 422
    )


def test_progress_on_an_unknown_issue_is_a_404(catalogue_client: TestClient) -> None:
    assert catalogue_client.put("/api/issues/nope/progress", json={"page": 2}).status_code == 404
    assert catalogue_client.get("/api/issues/nope/progress").status_code == 404
    # Deleting what is not there is not an error: the row may have outlived its
    # issue, and the caller only wants it gone.
    assert catalogue_client.delete("/api/issues/nope/progress").status_code == 204


@pytest.mark.parametrize(
    ("page", "page_count", "expected"),
    [(5, 10, 5), (11, 10, 10), (0, 10, 1), (-3, 10, 1), (99, None, 99), (99, 0, 99)],
)
def test_the_clamp(page: int, page_count: int | None, expected: int) -> None:
    assert clamp(page, page_count) == expected


# ------------------------------------------------------ progress per account


@pytest.fixture
def reader_client(catalogue_client: TestClient) -> TestClient:
    """A second client over the same application, signed in as a reader.

    Not entered as a context manager: that would run the application's
    lifespan a second time, and its end would close the catalogue under the
    administrator's client too.
    """
    return TestClient(app_of(catalogue_client), headers=add_user(catalogue_client, "reader"))


def by_id(issues: list[dict[str, Any]], identifier: str) -> dict[str, Any]:
    return next(issue for issue in issues if issue["id"] == identifier)


def pages_seen(client: TestClient, identifier: str) -> dict[str, int | None]:
    """The page of one issue as every response that embeds it shows it to ``client``.

    ``None`` where the issue is there with ``progress: null``; a list the
    issue is not on at all — continue reading, ``/api/progress`` — is also
    ``None``, which is what "no position" means for those two.
    """
    detail = client.get(f"/api/issues/{identifier}").json()
    title_id = detail["title_id"]
    day = today(client, date=SAMPLE_TODAY.isoformat())
    listed = client.get("/api/issues", params={"title": title_id, "limit": 200}).json()
    title = client.get(f"/api/titles/{title_id}").json()
    shelf = client.get("/api/titles", params={"kind": detail["kind"]}).json()
    latest = [title["latest_issue"], by_id(shelf, title_id)["latest_issue"]]
    # The issue has to be its title's latest for the two title views to embed it.
    assert [issue["id"] for issue in latest] == [identifier, identifier]
    progress_list = client.get("/api/progress").json()
    own = client.get(f"/api/issues/{identifier}/progress")
    embedded = {
        "detail": detail["progress"],
        "issues": by_id(listed["items"], identifier)["progress"],
        "newspapers": by_id(day["newspapers"], identifier)["progress"],
        "title": latest[0]["progress"],
        "titles": latest[1]["progress"],
        "continue_reading": next(
            (issue["progress"] for issue in day["continue_reading"] if issue["id"] == identifier),
            None,
        ),
        "progress_list": next(
            (issue["progress"] for issue in progress_list if issue["id"] == identifier), None
        ),
    }
    pages: dict[str, int | None] = {
        where: progress["page"] if progress is not None else None
        for where, progress in embedded.items()
    }
    pages["record"] = own.json()["page"] if own.status_code == 200 else None
    return pages


def everywhere(page: int | None) -> dict[str, int | None]:
    return dict.fromkeys(
        (
            "detail",
            "issues",
            "newspapers",
            "title",
            "titles",
            "continue_reading",
            "progress_list",
            "record",
        ),
        page,
    )


def test_each_account_sees_its_own_position_and_nobody_else_s(
    catalogue_client: TestClient, reader_client: TestClient, catalogue_settings: Settings
) -> None:
    identifier = sample_issue_id(catalogue_settings.library, A_NEWSPAPER)
    page_count = catalogue_client.get(f"/api/issues/{identifier}").json()["page_count"]
    assert page_count >= 4, "continue reading needs a page between the first and the last"
    progress = f"/api/issues/{identifier}/progress"

    assert catalogue_client.put(progress, json={"page": 2}).status_code == 200
    # The administrator has a position; the reader, so far, has none anywhere.
    assert pages_seen(catalogue_client, identifier) == everywhere(2)
    assert pages_seen(reader_client, identifier) == everywhere(None)
    assert reader_client.get(progress).status_code == 404
    assert reader_client.get("/api/progress").json() == []

    assert reader_client.put(progress, json={"page": 3}).status_code == 200

    assert pages_seen(catalogue_client, identifier) == everywhere(2)
    assert pages_seen(reader_client, identifier) == everywhere(3)

    # Forgetting is just as private: the reader's position stays.
    assert catalogue_client.delete(progress).status_code == 204

    assert pages_seen(catalogue_client, identifier) == everywhere(None)
    assert pages_seen(reader_client, identifier) == everywhere(3)


def test_an_upgraded_catalogue_s_positions_belong_to_the_bootstrapped_admin(
    settings: Settings,
) -> None:
    """The single-user rows of schema 4 are the first administrator's, through the API too."""
    identifier = "a1b2c3d4e5f60718"
    schema_4_catalogue(settings.db_path, {identifier: 12})

    with signed_in_client(settings) as client:
        assert client.get(f"/api/issues/{identifier}/progress").json()["page"] == 12
        second = add_user(client, "second", is_admin=True)
        assert client.get(f"/api/issues/{identifier}/progress", headers=second).status_code == 404


# --------------------------------------------------- Unsorted is not a title


def unsorted_issue(client: TestClient, kind: str) -> dict[str, Any]:
    """One issue the parser could not place, in a library of ``kind``."""
    titles = client.get("/api/titles", params={"kind": kind, "include_unsorted": 1}).json()
    bucket = next(title for title in titles if title["source"] == "unsorted")
    issues = client.get("/api/issues", params={"title": bucket["id"], "limit": 1}).json()
    assert issues["items"], f"no unsorted {kind} in the sample library"
    issue: dict[str, Any] = issues["items"][0]
    return issue


def test_the_unsorted_bucket_never_reaches_the_front_page(
    catalogue_client: TestClient,
) -> None:
    """It is where a file goes when nothing is known about it, not a title."""
    stray = unsorted_issue(catalogue_client, "magazine")
    payload = today(catalogue_client, date=stray["issue_date"])

    assert stray["title_name"] == "Unsorted"
    assert all(issue["title_name"] != "Unsorted" for issue in payload["magazines"])
    assert all(issue["title_name"] != "Unsorted" for issue in payload["newspapers"])
    assert all(issue["title_id"] != stray["title_id"] for issue in payload["magazines"])


def test_an_unsorted_newspaper_is_kept_off_the_days_shelf(
    catalogue_client: TestClient,
) -> None:
    stray = unsorted_issue(catalogue_client, "newspaper")

    payload = today(catalogue_client, date=stray["issue_date"])

    assert payload["newspapers"]
    assert all(issue["title_id"] != stray["title_id"] for issue in payload["newspapers"])


def test_the_unsorted_bucket_is_still_reachable(catalogue_client: TestClient) -> None:
    """Hidden from the shelves, not from the catalogue."""
    stray = unsorted_issue(catalogue_client, "magazine")

    assert catalogue_client.get(f"/api/issues/{stray['id']}").status_code == 200
    listed = catalogue_client.get("/api/issues", params={"title": stray["title_id"]}).json()
    assert listed["total"] > 0
    named = catalogue_client.get("/api/titles", params={"include_unsorted": 1}).json()
    assert any(title["source"] == "unsorted" for title in named)


def test_a_fallback_day_of_only_unsorted_issues_keeps_looking_back(
    catalogue_client: TestClient,
) -> None:
    """The fallback date must be one that has something to show."""
    stray = unsorted_issue(catalogue_client, "newspaper")
    payload = today(catalogue_client, date=stray["issue_date"])

    assert payload["newspapers_date"] is not None
    assert payload["newspapers"]


# ------------------------------------------- a duplicate carries no bookmark


def test_a_duplicate_leaves_the_progress_lists(
    catalogue_client: TestClient, catalogue_settings: Settings
) -> None:
    identifier = sample_issue_id(catalogue_settings.library, A_NEWSPAPER)
    catalogue_client.put(f"/api/issues/{identifier}/progress", json={"page": 2})
    assert [item["id"] for item in catalogue_client.get("/api/progress").json()] == [identifier]
    assert today(catalogue_client, date=SAMPLE_TODAY.isoformat())["continue_reading"]

    other = catalogue_client.get("/api/issues", params={"limit": 2}).json()["items"][1]["id"]
    mark_duplicate(catalogue_settings.db_path, identifier, other)

    assert catalogue_client.get("/api/progress").json() == []
    assert today(catalogue_client, date=SAMPLE_TODAY.isoformat())["continue_reading"] == []
    # The row itself is untouched: `/api/issues/{id}/progress` still answers.
    assert catalogue_client.get(f"/api/issues/{identifier}/progress").json()["page"] == 2
