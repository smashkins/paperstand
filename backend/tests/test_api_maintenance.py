"""``GET /api/maintenance`` and ``GET /api/maintenance/gaps``."""

from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any

from fastapi.testclient import TestClient

from paperstand.config import Settings
from paperstand.db import utc_now
from paperstand.organizer.report import write_run
from paperstand.schemas import OrganizerMove, OrganizerParked, OrganizerRun
from tests.conftest import sample_issue_id
from tests.test_api_catalogue import A_NEWSPAPER, mark_missing


def mark_unreadable(db_path: Path, issue_id: str, error: str = "cannot open") -> None:
    """Mark an issue unreadable, the way a scan would once the file broke."""
    connection = sqlite3.connect(db_path)
    try:
        connection.execute(
            "UPDATE issues SET cover_status = 'error', cover_error = ? WHERE id = ?",
            (error, issue_id),
        )
        connection.commit()
    finally:
        connection.close()


def mark_title_missing(db_path: Path, title_name: str) -> None:
    """Mark every issue of one title missing."""
    connection = sqlite3.connect(db_path)
    try:
        connection.execute(
            "UPDATE issues SET missing_since = ?, duplicate_of = NULL "
            "WHERE title_id = (SELECT id FROM titles WHERE name = ?)",
            (utc_now(), title_name),
        )
        connection.commit()
    finally:
        connection.close()


def gaps_for(client: TestClient, **params: Any) -> list[dict[str, Any]]:
    response = client.get("/api/maintenance/gaps", params=params)
    assert response.status_code == 200
    payload: list[dict[str, Any]] = response.json()
    return payload


# --------------------------------------------------------------- the summary


def test_summary_counts_missing_and_unreadable(
    catalogue_client: TestClient, catalogue_settings: Settings
) -> None:
    missing_id = sample_issue_id(catalogue_settings.library, A_NEWSPAPER)
    mark_missing(catalogue_settings.db_path, missing_id)
    other = catalogue_client.get(
        "/api/issues", params={"library": "newspapers", "limit": 5}
    ).json()["items"]
    unreadable_id = next(item["id"] for item in other if item["id"] != missing_id)
    mark_unreadable(catalogue_settings.db_path, unreadable_id)

    response = catalogue_client.get("/api/maintenance")

    assert response.status_code == 200
    payload = response.json()
    assert payload["missing_count"] == 1
    assert payload["unreadable_count"] == 1
    assert payload["missing_grace_days"] == 7
    assert payload["organizer"] is None
    assert payload["attention"] == 2


def test_summary_lists_the_unsorted_buckets(catalogue_client: TestClient) -> None:
    response = catalogue_client.get("/api/maintenance")

    payload = response.json()
    buckets = {bucket["library_id"]: bucket for bucket in payload["unsorted"]}
    assert "newspapers" in buckets
    assert buckets["newspapers"]["count"] > 0
    assert payload["unsorted_count"] == sum(bucket["count"] for bucket in payload["unsorted"])


def test_organizer_is_null_until_a_report_exists(
    catalogue_client: TestClient, catalogue_settings: Settings
) -> None:
    before = catalogue_client.get("/api/maintenance").json()
    assert before["organizer"] is None
    assert before["attention"] == before["missing_count"] + before["unreadable_count"]

    run = OrganizerRun(
        started_at="2026-09-01T00:00:00+00:00",
        finished_at="2026-09-01T00:00:05+00:00",
        mode="apply",
        inbox="/inbox",
        refused=False,
        moved=1,
        duplicate=0,
        unsorted=1,
        skipped=0,
        failed=0,
        moves=[
            OrganizerMove(
                source="Il_Mattutino_2026-09-01.pdf",
                destination="Il Mattutino/2026/Il Mattutino - 2026-09-01.pdf",
            )
        ],
        parked=[
            OrganizerParked(
                folder="unsorted",
                name="Unknown_2026-09-01.pdf",
                reason="no declared title matches",
                size=1234,
                modified="2026-09-01T00:00:00+00:00",
            )
        ],
        scan_requested=True,
    )
    write_run(catalogue_settings.organizer_report_path, run)

    after = catalogue_client.get("/api/maintenance").json()
    assert after["organizer"]["mode"] == "apply"
    assert after["organizer"]["moved"] == 1
    assert len(after["organizer"]["parked"]) == 1
    assert after["attention"] == after["missing_count"] + after["unreadable_count"] + 1


# ---------------------------------------------------------- ?unreadable=true


def test_unreadable_lists_only_that_row_with_its_error(
    catalogue_client: TestClient, catalogue_settings: Settings
) -> None:
    issue_id = sample_issue_id(catalogue_settings.library, A_NEWSPAPER)
    mark_unreadable(catalogue_settings.db_path, issue_id, "PDF is corrupt")

    response = catalogue_client.get("/api/issues", params={"unreadable": True})

    assert response.status_code == 200
    payload = response.json()
    assert payload["total"] == 1
    assert [item["id"] for item in payload["items"]] == [issue_id]
    assert payload["items"][0]["cover_error"] == "PDF is corrupt"


def test_unreadable_and_missing_never_overlap(
    catalogue_client: TestClient, catalogue_settings: Settings
) -> None:
    issue_id = sample_issue_id(catalogue_settings.library, A_NEWSPAPER)
    mark_missing(catalogue_settings.db_path, issue_id)

    unreadable = catalogue_client.get("/api/issues", params={"unreadable": True}).json()
    missing = catalogue_client.get("/api/issues", params={"missing": True}).json()

    assert issue_id not in [item["id"] for item in unreadable["items"]]
    assert issue_id in [item["id"] for item in missing["items"]]


# --------------------------------------------------------------------- gaps


def test_corriere_del_ponte_has_sixteen_daily_gaps_and_is_not_overdue(
    catalogue_client: TestClient,
) -> None:
    gaps = gaps_for(catalogue_client, today="2026-03-17")

    corriere = next(t for t in gaps if t["title_name"] == "Corriere del Ponte")
    assert corriere["date_gap_count"] == 16
    assert len(corriere["date_gaps"]) == 16
    assert corriere["overdue_days"] is None


def test_corriere_del_ponte_is_overdue_thirty_days_later(catalogue_client: TestClient) -> None:
    gaps = gaps_for(catalogue_client, today="2026-04-16")

    corriere = next(t for t in gaps if t["title_name"] == "Corriere del Ponte")
    assert corriere["overdue_days"] is not None
    assert corriere["overdue_days"] > 0


def test_numbered_magazines_with_holes_are_listed(catalogue_client: TestClient) -> None:
    gaps = gaps_for(catalogue_client, today="2026-03-17")

    orizzonte = next(t for t in gaps if t["title_name"] == "Orizzonte")
    assert orizzonte["number_gap_count"] == 1
    assert orizzonte["number_gaps"] == [{"volume": None, "first": 1654, "last": 1654}]

    confini = next(t for t in gaps if t["title_name"] == "Confini")
    assert confini["number_gap_count"] == 4
    assert confini["number_gaps"] == [{"volume": None, "first": 4, "last": 7}]


def test_the_unsorted_bucket_never_appears_in_gaps(catalogue_client: TestClient) -> None:
    gaps = gaps_for(catalogue_client, today="2026-03-17")

    assert all(t["title_name"] != "Unsorted" for t in gaps)


def test_a_title_with_only_missing_rows_produces_no_gap(
    catalogue_client: TestClient, catalogue_settings: Settings
) -> None:
    before = gaps_for(catalogue_client, today="2026-03-17")
    assert any(t["title_name"] == "Confini" for t in before)

    mark_title_missing(catalogue_settings.db_path, "Confini")

    after = gaps_for(catalogue_client, today="2026-03-17")
    assert not any(t["title_name"] == "Confini" for t in after)
