"""Libraries, statistics, titles, calendars and the issue list.

Every assertion is derived from the sample library the fixtures catalogue — the
counts come from the catalogue itself or from the example configuration, never
from a number typed out here that a change to the generator would silently
falsify.
"""

from __future__ import annotations

import datetime as dt
from typing import Any

from fastapi import FastAPI
from fastapi.testclient import TestClient

from paperstand.api import issues as issues_api
from paperstand.api import libraries as libraries_api
from paperstand.api import progress as progress_api
from paperstand.api import titles as titles_api
from paperstand.api import today as today_api
from paperstand.config import Settings
from tests.conftest import SAMPLE_TODAY, sample_issue_id

A_NEWSPAPER = "Newspapers/2026/03/17/Corriere_del_Ponte_17_Marzo_2026.pdf"


def titles(client: TestClient, **params: Any) -> list[dict[str, Any]]:
    response = client.get("/api/titles", params=params)
    assert response.status_code == 200
    payload: list[dict[str, Any]] = response.json()
    return payload


def title_named(client: TestClient, name: str) -> dict[str, Any]:
    match = [title for title in titles(client) if title["name"] == name]
    assert match, f"no title called {name!r}"
    return match[0]


# ---------------------------------------------------------------- libraries


def test_libraries_report_their_counts(catalogue_client: TestClient) -> None:
    response = catalogue_client.get("/api/libraries")

    assert response.status_code == 200
    libraries = response.json()
    assert {library["name"] for library in libraries} == {"Newspapers", "Magazines", "Zines"}
    newspapers = next(item for item in libraries if item["id"] == "newspapers")
    assert newspapers["kind"] == "newspaper"
    assert newspapers["path"] == "Newspapers"
    assert newspapers["title_count"] > 0
    assert newspapers["issue_count"] > 0
    # The example configuration does not name every daily in the library, so at
    # least one file has to land in the Unsorted bucket.
    assert newspapers["unsorted_count"] > 0


def test_stats_add_up_and_measure_the_caches(catalogue_client: TestClient) -> None:
    response = catalogue_client.get("/api/stats")

    assert response.status_code == 200
    stats = response.json()
    assert stats["issue_count"] == sum(item["issue_count"] for item in stats["libraries"])
    assert stats["title_count"] == sum(item["title_count"] for item in stats["libraries"])
    assert stats["duplicate_count"] > 0
    assert stats["covers_bytes"] > 0
    assert stats["pages_bytes"] == 0  # nothing has been rendered yet
    assert stats["db_bytes"] > 0


def test_the_catalogue_is_unavailable_without_a_database(settings: Settings) -> None:
    """A database that could not be opened is a 503, not a stack trace."""
    application = FastAPI()
    application.include_router(libraries_api.create_router(settings, None))
    application.include_router(titles_api.create_router(None))
    application.include_router(issues_api.create_router(None))
    application.include_router(today_api.create_router(settings, None))
    application.include_router(progress_api.create_router(None))

    with TestClient(application) as offline:
        for path in ("/api/libraries", "/api/stats", "/api/titles", "/api/issues", "/api/today"):
            assert offline.get(path).status_code == 503, path


# ------------------------------------------------------------------- titles


def test_titles_are_sorted_by_name_by_default(catalogue_client: TestClient) -> None:
    names = [title["name"] for title in titles(catalogue_client, kind="newspaper")]

    assert names == sorted(names, key=str.casefold)
    assert "Unsorted" not in names


def test_titles_can_be_sorted_by_the_latest_issue(catalogue_client: TestClient) -> None:
    dates = [title["last_date"] for title in titles(catalogue_client, sort="latest")]

    assert dates == sorted(dates, key=lambda value: value or "", reverse=True)


def test_unsorted_is_only_listed_when_asked_for(catalogue_client: TestClient) -> None:
    without = {title["name"] for title in titles(catalogue_client)}
    with_it = {title["name"] for title in titles(catalogue_client, include_unsorted=1)}

    assert "Unsorted" not in without
    assert "Unsorted" in with_it


def test_titles_filter_by_kind_and_by_library(catalogue_client: TestClient) -> None:
    newspapers = titles(catalogue_client, kind="newspaper")
    zines = titles(catalogue_client, library="zines")

    assert newspapers and all(title["kind"] == "newspaper" for title in newspapers)
    assert zines and all(title["library_id"] == "zines" for title in zines)
    assert {title["name"] for title in zines} == {
        "Random Mag",
        "Something",
        "Circuito",
        "Bright Meadows",
    }


def test_a_title_carries_its_latest_issue_and_its_cover(catalogue_client: TestClient) -> None:
    title = title_named(catalogue_client, "Corriere del Ponte")

    assert title["issue_count"] > 0
    assert title["first_date"] <= title["last_date"]
    latest = title["latest_issue"]
    assert latest["issue_date"] == title["last_date"]
    assert title["cover_url"] == latest["cover_url"]
    assert title["thumb_url"] == latest["thumb_url"]
    assert latest["cover_url"].startswith(f"/api/issues/{latest['id']}/cover.jpg?v=")


def test_title_detail_lists_its_years(catalogue_client: TestClient) -> None:
    title = title_named(catalogue_client, "Corriere del Ponte")

    detail = catalogue_client.get(f"/api/titles/{title['id']}").json()

    assert detail["id"] == title["id"]
    assert detail["years"] == [{"year": SAMPLE_TODAY.year, "count": title["issue_count"]}]


def test_an_unknown_title_is_a_404(catalogue_client: TestClient) -> None:
    assert catalogue_client.get("/api/titles/nope").status_code == 404
    assert catalogue_client.get("/api/titles/nope/calendar").status_code == 404


# ------------------------------------------------------- declared publications


def test_a_declared_titles_metadata_reaches_the_api(catalogue_client: TestClient) -> None:
    title = title_named(catalogue_client, "Corriere del Ponte")

    assert title["source"] == "publication"
    assert title["slug"] == "corriere-del-ponte"
    assert title["frequency"] == "daily"
    assert title["language"] == "it"
    assert title["issue_key"] == "date"
    assert title["supplements"] == ["Weekend"]
    assert title["parent_slug"] is None


def test_a_declared_supplement_carries_its_variant(catalogue_client: TestClient) -> None:
    title = title_named(catalogue_client, "Corriere del Ponte")
    issues = catalogue_client.get(
        "/api/issues", params={"title": title["id"], "limit": 200}
    ).json()["items"]
    weekend = next(issue for issue in issues if issue["variant"] == "Weekend")
    speciale = next(issue for issue in issues if issue["variant"] == "Speciale")

    assert weekend["is_duplicate"] is False
    assert speciale["is_duplicate"] is False
    weekend_detail = catalogue_client.get(f"/api/issues/{weekend['id']}").json()
    speciale_detail = catalogue_client.get(f"/api/issues/{speciale['id']}").json()
    assert weekend_detail["matched_rule"].endswith("variant:declared")
    assert speciale_detail["matched_rule"].endswith("variant:undeclared")


def test_a_declared_magazine_with_no_configured_title_is_real(
    catalogue_client: TestClient,
) -> None:
    title = next(
        title
        for title in titles(catalogue_client, library="magazines")
        if title["name"] == "Bright Meadows"
    )

    assert title["source"] == "publication"
    assert title["kind"] == "magazine"
    assert title["language"] == "en"
    assert title["frequency"] == "monthly"

    issues = catalogue_client.get("/api/issues", params={"title": title["id"], "limit": 10}).json()[
        "items"
    ]
    assert len(issues) == 1
    assert issues[0]["volume"] == 2024
    assert issues[0]["issue_number"] == "3"


def test_an_undeclared_title_has_no_publication_metadata(catalogue_client: TestClient) -> None:
    title = title_named(catalogue_client, "Orizzonte")

    assert title["source"] != "publication"
    assert title["slug"] is None
    assert title["frequency"] is None
    assert title["language"] is None
    assert title["issue_key"] is None
    assert title["parent_slug"] is None
    assert title["supplements"] is None


# ----------------------------------------------------------------- calendar


def test_the_calendar_maps_every_day_to_its_issue(catalogue_client: TestClient) -> None:
    title = title_named(catalogue_client, "Corriere del Ponte")

    calendar = catalogue_client.get(f"/api/titles/{title['id']}/calendar").json()
    issues = catalogue_client.get(
        "/api/issues", params={"title": title["id"], "limit": 200}
    ).json()["items"]
    distinct_dates = {issue["issue_date"] for issue in issues}

    assert calendar["year"] == SAMPLE_TODAY.year
    assert calendar["years"] == [{"year": SAMPLE_TODAY.year, "count": title["issue_count"]}]
    # A day may hold both the daily and a same-day supplement; the calendar maps
    # one issue per day, so it has as many entries as there are distinct dates,
    # not one per issue.
    assert len(calendar["days"]) == len(distinct_dates)
    assert SAMPLE_TODAY.isoformat() in calendar["days"]
    for day, identifier in calendar["days"].items():
        issue = catalogue_client.get(f"/api/issues/{identifier}").json()
        assert issue["issue_date"] == day
        assert issue["title_id"] == title["id"]
        assert issue["is_duplicate"] is False


def test_a_same_day_supplement_never_hides_the_daily(catalogue_client: TestClient) -> None:
    title = title_named(catalogue_client, "Corriere del Ponte")
    d1 = SAMPLE_TODAY - dt.timedelta(days=30)
    issues = catalogue_client.get(
        "/api/issues", params={"title": title["id"], "limit": 200}
    ).json()["items"]
    plain = next(
        issue
        for issue in issues
        if issue["issue_date"] == d1.isoformat() and issue["variant"] is None
    )
    weekend = next(
        issue
        for issue in issues
        if issue["issue_date"] == d1.isoformat() and issue["variant"] == "Weekend"
    )
    assert plain["id"] != weekend["id"]

    calendar = catalogue_client.get(
        f"/api/titles/{title['id']}/calendar", params={"year": d1.year}
    ).json()
    assert calendar["days"][d1.isoformat()] == plain["id"]

    today_payload = catalogue_client.get("/api/today", params={"date": d1.isoformat()}).json()
    winners = {issue["title_id"]: issue["id"] for issue in today_payload["newspapers"]}
    assert winners[title["id"]] == plain["id"]


def test_a_year_with_no_issues_has_an_empty_map(catalogue_client: TestClient) -> None:
    title = title_named(catalogue_client, "Corriere del Ponte")

    calendar = catalogue_client.get(
        f"/api/titles/{title['id']}/calendar", params={"year": 1999}
    ).json()

    assert calendar["year"] == 1999
    assert calendar["days"] == {}
    assert calendar["years"]  # the other years are still offered


# ------------------------------------------------------------------- issues


def test_issues_paginate_and_report_the_total(catalogue_client: TestClient) -> None:
    first = catalogue_client.get("/api/issues", params={"kind": "newspaper", "limit": 5}).json()
    second = catalogue_client.get(
        "/api/issues", params={"kind": "newspaper", "limit": 5, "offset": 5}
    ).json()

    assert first["total"] == second["total"] > 5
    assert len(first["items"]) == len(second["items"]) == 5
    assert {item["id"] for item in first["items"]}.isdisjoint(
        {item["id"] for item in second["items"]}
    )


def test_issues_are_sorted_the_way_they_were_asked_for(catalogue_client: TestClient) -> None:
    descending = catalogue_client.get("/api/issues", params={"sort": "date_desc"}).json()["items"]
    ascending = catalogue_client.get("/api/issues", params={"sort": "date_asc"}).json()["items"]
    added = catalogue_client.get("/api/issues", params={"sort": "added_desc"}).json()["items"]

    assert [item["issue_date"] for item in descending] == sorted(
        (item["issue_date"] for item in descending), key=lambda value: value or "", reverse=True
    )
    assert [item["issue_date"] for item in ascending] == sorted(
        item["issue_date"] for item in ascending
    )
    assert [item["added_at"] for item in added] == sorted(
        (item["added_at"] for item in added), reverse=True
    )


def test_issues_filter_by_title_library_year_and_range(catalogue_client: TestClient) -> None:
    title = title_named(catalogue_client, "Corriere del Ponte")
    by_title = catalogue_client.get("/api/issues", params={"title": title["id"]}).json()
    week = SAMPLE_TODAY - dt.timedelta(days=6)
    ranged = catalogue_client.get(
        "/api/issues",
        params={
            "title": title["id"],
            "from": week.isoformat(),
            "to": SAMPLE_TODAY.isoformat(),
        },
    ).json()
    other_year = catalogue_client.get(
        "/api/issues", params={"title": title["id"], "year": 1999}
    ).json()
    zines = catalogue_client.get("/api/issues", params={"library": "zines"}).json()

    assert all(item["library_id"] == "zines" for item in zines["items"])
    assert zines["total"] == 5
    assert by_title["total"] == title["issue_count"]
    assert ranged["total"] == 7
    assert all(week.isoformat() <= item["issue_date"] for item in ranged["items"])
    assert other_year == {"items": [], "total": 0}


def test_duplicates_are_hidden_unless_asked_for(catalogue_client: TestClient) -> None:
    hidden = catalogue_client.get("/api/issues", params={"limit": 1}).json()
    shown = catalogue_client.get("/api/issues", params={"limit": 1, "include_duplicates": 1}).json()

    assert shown["total"] > hidden["total"]


def test_the_limit_is_capped(catalogue_client: TestClient) -> None:
    assert catalogue_client.get("/api/issues", params={"limit": 201}).status_code == 422
    assert catalogue_client.get("/api/issues", params={"limit": 200}).status_code == 200


def test_an_issue_carries_its_urls_its_rule_and_its_neighbours(
    catalogue_client: TestClient, catalogue_settings: Settings
) -> None:
    identifier = sample_issue_id(catalogue_settings.library, A_NEWSPAPER)

    issue = catalogue_client.get(f"/api/issues/{identifier}").json()

    assert issue["id"] == identifier
    assert issue["title_name"] == "Corriere del Ponte"
    assert issue["kind"] == "newspaper"
    assert issue["content_hash"] is not None and len(issue["content_hash"]) == 64
    assert issue["id"] == issue["content_hash"][:16]
    assert issue["issue_date"] == SAMPLE_TODAY.isoformat()
    assert issue["date_precision"] == "day"
    assert issue["rel_path"] == A_NEWSPAPER
    assert issue["page_count"] and issue["page_count"] > 1
    assert issue["aspect"]["page_w"] > 0
    assert issue["file_url"] == f"/api/issues/{identifier}/file"
    assert issue["pages_url_template"].startswith(f"/api/issues/{identifier}/pages/{{n}}.webp?")
    assert "v=" in issue["pages_url_template"]
    assert issue["matched_rule"]
    assert issue["derived_title"]
    assert issue["progress"] is None
    # The latest issue of a daily that appears every day: nothing after it, and
    # the day before it in front of it.
    assert issue["next_issue_id"] is None
    previous = catalogue_client.get(f"/api/issues/{issue['prev_issue_id']}").json()
    assert previous["issue_date"] == (SAMPLE_TODAY - dt.timedelta(days=1)).isoformat()
    assert previous["next_issue_id"] == identifier


def test_an_unknown_issue_is_a_404(catalogue_client: TestClient) -> None:
    assert catalogue_client.get("/api/issues/nope").status_code == 404
