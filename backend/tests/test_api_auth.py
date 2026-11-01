"""Access control over HTTP: the three credentials, the auth routes, the users routes.

The suites of every other router run signed in as the bootstrapped
administrator (see ``conftest.py``) and test what the router does; this one
tests who may call it at all. Its first test walks every route the application
has, so a router included without its guard — or a dependency FastAPI quietly
turned into a query parameter — fails here rather than in production.
"""

from __future__ import annotations

import base64
import re
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import fastapi.routing
import pytest
from fastapi.testclient import TestClient

from paperstand import accounts
from paperstand.api.auth import COOKIE_MAX_AGE, COOKIE_NAME, Authenticator
from paperstand.config import Settings
from paperstand.main import create_app
from tests.conftest import (
    ADMIN_PASSWORD,
    ADMIN_USERNAME,
    USER_PASSWORD,
    add_user,
    app_of,
    bearer,
    catalogue_connection,
    issue,
    quiet_settings,
    sample_issue_id,
)

A_NEWSPAPER = "Newspapers/2026/03/17/Corriere_del_Ponte_17_Marzo_2026.pdf"
CHALLENGE = 'Basic realm="Paperstand"'

#: The routes under `/api` and `/opds` that answer without an account.
PUBLIC = {("/api/health", "GET"), ("/api/auth/login", "POST")}


def basic(username: str, password: str) -> dict[str, str]:
    """A Basic credential, encoded as UTF-8."""
    raw = base64.b64encode(f"{username}:{password}".encode()).decode("ascii")
    return {"Authorization": f"Basic {raw}"}


def anonymous(client: TestClient) -> TestClient:
    """The same client with no credential of its own: every request says who it is."""
    client.headers.pop("Authorization", None)
    client.cookies.clear()
    return client


def login(client: TestClient, username: str, password: str, **extra: Any) -> dict[str, Any]:
    response = client.post(
        "/api/auth/login", json={"username": username, "password": password, **extra}
    )
    assert response.status_code == 200, response.text
    payload: dict[str, Any] = response.json()
    return payload


@pytest.fixture
def issue_id_(catalogue_settings: Settings) -> str:
    return sample_issue_id(catalogue_settings.library, A_NEWSPAPER)


@pytest.fixture
def reader(catalogue_client: TestClient) -> dict[str, str]:
    """The headers of a signed-in account that is not an administrator."""
    return add_user(catalogue_client, "reader")


# ------------------------------------------------------------- the route walk


def concrete(path: str) -> str:
    """A route's path with every parameter filled in with something that matches."""
    return re.sub(r"\{[^}]+\}", "1", path)


def every_route(client: TestClient) -> Iterator[tuple[str, set[str]]]:
    """Every path and its methods, the routes of included routers among them.

    Recent FastAPI keeps an included router as one entry of ``app.routes`` and
    resolves its routes lazily; ``iter_route_contexts`` is how its own OpenAPI
    export walks them. An older FastAPI flattens them into ``app.routes``.
    """
    expand = getattr(fastapi.routing, "iter_route_contexts", None)
    routes = app_of(client).routes
    for route in expand(routes) if expand is not None else routes:
        path, methods = getattr(route, "path", None), getattr(route, "methods", None)
        if path is not None and methods:
            yield str(path), set(methods)


def guarded_routes(client: TestClient) -> list[tuple[str, str]]:
    routes = []
    for path, methods in every_route(client):
        if not (path.startswith("/api") or path.startswith("/opds")):
            continue
        for method in sorted(methods):
            if (path, method) not in PUBLIC:
                routes.append((path, method))
    return routes


def test_every_route_but_the_public_ones_needs_an_account(client: TestClient) -> None:
    anonymous(client)
    routes = guarded_routes(client)

    # The walk really is over the whole application, the new routes included.
    paths = {path for path, _ in routes}
    assert {
        "/api/titles",
        "/api/issues/{issue_id}/file",
        "/api/issues/{issue_id}/cover.jpg",
        "/api/scan",
        "/api/scan/status",
        "/api/maintenance",
        "/api/users",
        "/api/users/{user_id}",
        "/api/auth/me",
        "/api/auth/logout",
        "/api/auth/password",
        "/opds",
        "/opds/search",
    } <= paths
    assert len(routes) > 30

    for path, method in routes:
        response = client.request(method, concrete(path))
        where = f"{method} {path}"
        assert response.status_code == 401, where
        if method != "HEAD":
            assert set(response.json()) == {"detail"}, where
        if path.startswith("/opds"):
            assert response.headers.get("www-authenticate") == CHALLENGE, where
        else:
            assert "www-authenticate" not in response.headers, where


def test_the_public_routes_answer_without_an_account(client: TestClient) -> None:
    anonymous(client)

    assert client.get("/api/health").status_code == 200
    assert client.post("/api/auth/login", json={"username": "x", "password": "y"}).status_code == (
        401
    )
    assert client.get("/openapi.json").status_code == 200
    assert client.get("/docs").status_code == 200
    assert client.get("/redoc").status_code == 200


def test_a_credential_without_a_catalogue_is_a_503_not_a_sign_out() -> None:
    """A client holding a good token must not be told it was signed out."""
    missing = Path("/paperstand/does/not/exist")

    with TestClient(create_app(quiet_settings(missing, missing))) as client:
        assert client.get("/api/titles").status_code == 401
        assert client.get("/api/titles", headers=bearer("some-token")).status_code == 503
        assert client.get("/api/titles", headers=basic("admin", "password")).status_code == 503


# -------------------------------------------------------- the three credentials


def test_each_credential_reads_a_json_route(catalogue_client: TestClient) -> None:
    token = issue(catalogue_client)
    client = anonymous(catalogue_client)

    by_bearer = client.get("/api/auth/me", headers=bearer(token))
    by_basic = client.get("/api/auth/me", headers=basic(ADMIN_USERNAME, ADMIN_PASSWORD))
    login(client, ADMIN_USERNAME, ADMIN_PASSWORD)
    by_cookie = client.get("/api/auth/me")

    for response in (by_bearer, by_basic, by_cookie):
        assert response.status_code == 200
        assert response.json()["username"] == ADMIN_USERNAME
        assert response.json()["is_admin"] is True


def test_each_credential_reads_a_cover_and_a_range_of_the_pdf(
    catalogue_client: TestClient, catalogue_settings: Settings, issue_id_: str
) -> None:
    token = issue(catalogue_client)
    client = anonymous(catalogue_client)
    pdf = (catalogue_settings.library / A_NEWSPAPER).read_bytes()
    cover = f"/api/issues/{issue_id_}/cover.jpg"
    file = f"/api/issues/{issue_id_}/file"
    ranged = {"Range": "bytes=0-99"}

    credentials: list[dict[str, str]] = [
        bearer(token),
        basic(ADMIN_USERNAME, ADMIN_PASSWORD),
    ]
    for headers in credentials:
        image = client.get(cover, headers=headers)
        assert image.status_code == 200
        assert image.headers["content-type"] == "image/jpeg"
        part = client.get(file, headers={**headers, **ranged})
        assert part.status_code == 206
        assert part.headers["content-range"] == f"bytes 0-99/{len(pdf)}"
        assert part.content == pdf[:100]
        assert "content-encoding" not in part.headers

    login(client, ADMIN_USERNAME, ADMIN_PASSWORD)
    assert client.get(cover).status_code == 200
    part = client.get(file, headers=ranged)
    assert part.status_code == 206
    assert part.content == pdf[:100]


def test_basic_credentials_are_utf_8(catalogue_client: TestClient) -> None:
    add_user(catalogue_client, "lettrice-ü", password="città-sul-lago")
    client = anonymous(catalogue_client)

    response = client.get("/api/auth/me", headers=basic("lettrice-ü", "città-sul-lago"))

    assert response.status_code == 200
    assert response.json()["username"] == "lettrice-ü"


@pytest.mark.parametrize(
    "value",
    [
        "Basic !!!not-base64!!!",
        "Basic " + base64.b64encode(b"no-colon-here").decode(),
        "Basic " + base64.b64encode(b"admin:\xff\xfe").decode(),
        "Basic ",
        "Bearer ",
        "Bearer not-a-token",
        "Token something",
    ],
)
def test_a_malformed_credential_is_a_plain_401(catalogue_client: TestClient, value: str) -> None:
    client = anonymous(catalogue_client)

    api = client.get("/api/titles", headers={"Authorization": value})
    feed = client.get("/opds", headers={"Authorization": value})

    assert api.status_code == 401
    assert "www-authenticate" not in api.headers
    assert feed.status_code == 401
    assert feed.headers["www-authenticate"] == CHALLENGE


def test_an_invalid_earlier_credential_does_not_block_a_valid_later_one(
    catalogue_client: TestClient,
) -> None:
    client = anonymous(catalogue_client)
    login(client, ADMIN_USERNAME, ADMIN_PASSWORD)

    # A stale bearer token, then a good cookie.
    assert client.get("/api/auth/me", headers=bearer("revoked-long-ago")).status_code == 200

    # A stale cookie, then good Basic credentials.
    client.cookies.set(COOKIE_NAME, "revoked-long-ago")
    assert (
        client.get("/api/auth/me", headers=basic(ADMIN_USERNAME, ADMIN_PASSWORD)).status_code == 200
    )
    assert client.get("/api/auth/me").status_code == 401


def test_the_credential_is_resolved_once_per_request(
    catalogue_client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The router's `user` and the route's `admin` share one resolution."""
    authenticator = app_of(catalogue_client).state.authenticator
    assert isinstance(authenticator, Authenticator)
    calls: list[str] = []
    real = authenticator.identify

    def counting(request: Any) -> Any:
        calls.append(request.url.path)
        return real(request)

    monkeypatch.setattr(authenticator, "identify", counting)
    victim = accounts.create_user(catalogue_connection(catalogue_client), "second", USER_PASSWORD)

    assert catalogue_client.get("/api/scan/status").status_code == 200
    assert catalogue_client.delete(f"/api/users/{victim.id}").status_code == 204
    assert calls == ["/api/scan/status", f"/api/users/{victim.id}"]


# ------------------------------------------------------------ the Basic cache


def test_a_basic_credential_is_verified_once_then_remembered(
    catalogue_client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    client = anonymous(catalogue_client)
    authenticator = app_of(client).state.authenticator
    checks: list[str] = []
    real = accounts.authenticate

    def counting(connection: Any, username: str, password: str) -> Any:
        checks.append(username)
        return real(connection, username, password)

    monkeypatch.setattr(accounts, "authenticate", counting)
    good = basic(ADMIN_USERNAME, ADMIN_PASSWORD)

    for _ in range(3):
        assert client.get("/api/titles", headers=good).status_code == 200
    assert checks == [ADMIN_USERNAME]
    assert len(authenticator.basic_cache) == 1

    # A failure is checked every time, and never remembered.
    for _ in range(2):
        assert client.get("/api/titles", headers=basic(ADMIN_USERNAME, "wrong")).status_code == 401
    assert checks == [ADMIN_USERNAME] * 3
    assert len(authenticator.basic_cache) == 1


def test_a_password_changed_behind_the_servers_back_is_refused_next_time(
    catalogue_client: TestClient,
) -> None:
    """The CLI's path: `accounts` straight on the database, in another process."""
    old = add_user(catalogue_client, "reader", password="the old password")
    client = anonymous(catalogue_client)
    assert client.get("/api/titles", headers=basic("reader", "the old password")).status_code == 200

    connection = catalogue_connection(client)
    account = accounts.find_user(connection, "reader")
    assert account is not None
    accounts.set_password(connection, account.id, "the new password")

    assert client.get("/api/titles", headers=basic("reader", "the old password")).status_code == 401
    assert client.get("/api/titles", headers=basic("reader", "the new password")).status_code == 200
    # The reset signed the token out too.
    assert client.get("/api/titles", headers=old).status_code == 401


def test_a_deleted_account_is_refused_even_from_the_cache(catalogue_client: TestClient) -> None:
    add_user(catalogue_client, "reader")
    client = anonymous(catalogue_client)
    assert client.get("/api/titles", headers=basic("reader", USER_PASSWORD)).status_code == 200

    connection = catalogue_connection(client)
    account = accounts.find_user(connection, "reader")
    assert account is not None
    accounts.delete_user(connection, account.id, acting_user_id=None)

    assert client.get("/api/titles", headers=basic("reader", USER_PASSWORD)).status_code == 401


def test_the_cache_drops_its_oldest_entry() -> None:
    authenticator = Authenticator(None, cache_size=2)
    cache = authenticator.basic_cache
    keys = [cache.key(name, "password") for name in ("a", "b", "c")]

    cache.put(keys[0], 1, "h1")
    cache.put(keys[1], 2, "h2")
    assert cache.get(keys[0]) == (1, "h1")  # now the most recent
    cache.put(keys[2], 3, "h3")

    assert len(cache) == 2
    assert cache.get(keys[1]) is None
    assert cache.get(keys[0]) == (1, "h1")
    assert cache.key("a", "b:c") != cache.key("a:b", "c")


# ------------------------------------------------------------- /api/auth/*


def test_login_answers_a_token_a_user_and_a_cookie(catalogue_client: TestClient) -> None:
    client = anonymous(catalogue_client)

    response = client.post(
        "/api/auth/login",
        json={"username": "ADMIN", "password": ADMIN_PASSWORD, "device": "Tablet in the kitchen"},
    )

    assert response.status_code == 200
    payload = response.json()
    assert set(payload) == {"token", "user"}
    assert set(payload["user"]) == {"id", "username", "is_admin", "created_at"}
    assert payload["user"]["username"] == ADMIN_USERNAME
    assert response.headers["cache-control"] == "no-store"

    cookie = response.headers["set-cookie"]
    assert cookie.startswith(f"{COOKIE_NAME}={payload['token']};")
    assert "HttpOnly" in cookie
    assert "SameSite=lax" in cookie
    assert "Path=/" in cookie
    assert f"Max-Age={COOKIE_MAX_AGE}" in cookie
    assert "Secure" not in cookie

    devices = [
        row["device"]
        for row in catalogue_connection(client).execute("SELECT device FROM auth_tokens")
    ]
    assert "Tablet in the kitchen" in devices
    assert client.get("/api/auth/me", headers=bearer(payload["token"])).status_code == 200


def test_an_unknown_name_and_a_wrong_password_are_the_same_401(
    catalogue_client: TestClient,
) -> None:
    client = anonymous(catalogue_client)

    unknown = client.post("/api/auth/login", json={"username": "nobody", "password": "whatever"})
    wrong = client.post("/api/auth/login", json={"username": "admin", "password": "whatever"})

    assert unknown.status_code == wrong.status_code == 401
    assert unknown.json() == wrong.json()
    assert "www-authenticate" not in unknown.headers
    assert "set-cookie" not in unknown.headers
    assert client.post("/api/auth/login", json={"username": "admin"}).status_code == 422


def test_logout_revokes_the_token_it_was_made_with(catalogue_client: TestClient) -> None:
    client = anonymous(catalogue_client)
    kept = issue(client)
    gone = issue(client)

    response = client.post("/api/auth/logout", headers=bearer(gone))

    assert response.status_code == 204
    assert f'{COOKIE_NAME}=""' in response.headers["set-cookie"]
    assert client.get("/api/auth/me", headers=bearer(gone)).status_code == 401
    assert client.get("/api/auth/me", headers=bearer(kept)).status_code == 200


def test_logout_with_the_cookie_revokes_the_cookie(catalogue_client: TestClient) -> None:
    client = anonymous(catalogue_client)
    token = login(client, ADMIN_USERNAME, ADMIN_PASSWORD)["token"]

    assert client.post("/api/auth/logout").status_code == 204

    assert COOKIE_NAME not in client.cookies
    assert client.get("/api/auth/me", headers=bearer(token)).status_code == 401


def test_logout_with_basic_has_nothing_to_revoke(catalogue_client: TestClient) -> None:
    client = anonymous(catalogue_client)
    token = issue(client)
    credentials = basic(ADMIN_USERNAME, ADMIN_PASSWORD)

    assert client.post("/api/auth/logout", headers=credentials).status_code == 204
    assert client.get("/api/auth/me", headers=credentials).status_code == 200
    assert client.get("/api/auth/me", headers=bearer(token)).status_code == 200


def test_a_password_change_keeps_this_device_and_signs_out_the_others(
    catalogue_client: TestClient,
) -> None:
    client = anonymous(catalogue_client)
    this_device = issue(client)
    other_device = issue(client)

    wrong = client.put(
        "/api/auth/password",
        headers=bearer(this_device),
        json={"current_password": "not it", "new_password": "a brand new password"},
    )
    short = client.put(
        "/api/auth/password",
        headers=bearer(this_device),
        json={"current_password": ADMIN_PASSWORD, "new_password": "short"},
    )
    changed = client.put(
        "/api/auth/password",
        headers=bearer(this_device),
        json={"current_password": ADMIN_PASSWORD, "new_password": "a brand new password"},
    )

    assert wrong.status_code == 403
    assert short.status_code == 422
    assert changed.status_code == 204
    assert client.get("/api/auth/me", headers=bearer(this_device)).status_code == 200
    assert client.get("/api/auth/me", headers=bearer(other_device)).status_code == 401
    bad = client.post(
        "/api/auth/login", json={"username": ADMIN_USERNAME, "password": ADMIN_PASSWORD}
    )
    assert bad.status_code == 401
    login(client, ADMIN_USERNAME, "a brand new password")


# ------------------------------------------------------------- administrators


@pytest.mark.parametrize(
    ("method", "path"),
    [
        ("POST", "/api/scan"),
        ("GET", "/api/maintenance"),
        ("GET", "/api/maintenance/gaps"),
        ("GET", "/api/users"),
        ("POST", "/api/users"),
        ("PATCH", "/api/users/1"),
        ("DELETE", "/api/users/1"),
    ],
)
def test_a_reader_cannot_do_an_administrators_job(
    catalogue_client: TestClient, reader: dict[str, str], method: str, path: str
) -> None:
    body = {"username": "third", "password": USER_PASSWORD} if path == "/api/users" else {}
    response = catalogue_client.request(method, path, headers=reader, json=body or None)

    assert response.status_code == 403
    assert set(response.json()) == {"detail"}


def test_a_reader_reads_the_catalogue_and_follows_a_scan(
    catalogue_client: TestClient, reader: dict[str, str], issue_id_: str
) -> None:
    for path in (
        "/api/scan/status",
        "/api/titles",
        "/api/today",
        f"/api/issues/{issue_id_}",
        "/api/auth/me",
        "/opds",
    ):
        assert catalogue_client.get(path, headers=reader).status_code == 200, path


def test_users_are_listed_by_name_without_regard_to_case(catalogue_client: TestClient) -> None:
    add_user(catalogue_client, "Zeta")
    add_user(catalogue_client, "beta")

    names = [user["username"] for user in catalogue_client.get("/api/users").json()]

    assert names == ["admin", "beta", "Zeta"]


def test_an_admin_creates_an_account_that_can_sign_in(catalogue_client: TestClient) -> None:
    response = catalogue_client.post(
        "/api/users", json={"username": "  reader ", "password": USER_PASSWORD}
    )

    assert response.status_code == 201
    created = response.json()
    assert created["username"] == "reader"
    assert created["is_admin"] is False
    login(anonymous(catalogue_client), "reader", USER_PASSWORD)


@pytest.mark.parametrize(
    "body",
    [
        {"username": "rea:der", "password": USER_PASSWORD},
        {"username": "   ", "password": USER_PASSWORD},
        {"username": "a" * 65, "password": USER_PASSWORD},
        {"username": "rea\nder", "password": USER_PASSWORD},
        {"username": "reader", "password": "short"},
        {"username": "reader", "password": "a" * 1025},
        {"username": "reader"},
    ],
)
def test_an_account_that_breaks_the_rules_is_a_422(
    catalogue_client: TestClient, body: dict[str, str]
) -> None:
    assert catalogue_client.post("/api/users", json=body).status_code == 422


def test_a_name_that_is_taken_whatever_its_case_is_a_409_without_scan_id(
    catalogue_client: TestClient,
) -> None:
    response = catalogue_client.post(
        "/api/users", json={"username": "ADMIN", "password": USER_PASSWORD}
    )

    assert response.status_code == 409
    assert set(response.json()) == {"detail"}


def test_the_last_admin_cannot_be_demoted(catalogue_client: TestClient) -> None:
    me = catalogue_client.get("/api/auth/me").json()

    response = catalogue_client.patch(f"/api/users/{me['id']}", json={"is_admin": False})

    assert response.status_code == 409
    assert set(response.json()) == {"detail"}
    assert catalogue_client.get("/api/auth/me").json()["is_admin"] is True


def test_an_admin_promotes_and_demotes(catalogue_client: TestClient) -> None:
    headers = add_user(catalogue_client, "reader")
    target = catalogue_client.get("/api/auth/me", headers=headers).json()

    promoted = catalogue_client.patch(f"/api/users/{target['id']}", json={"is_admin": True})
    assert promoted.status_code == 200 and promoted.json()["is_admin"] is True
    assert catalogue_client.get("/api/maintenance", headers=headers).status_code == 200

    demoted = catalogue_client.patch(f"/api/users/{target['id']}", json={"is_admin": False})
    assert demoted.status_code == 200 and demoted.json()["is_admin"] is False
    assert catalogue_client.get("/api/maintenance", headers=headers).status_code == 403

    assert catalogue_client.patch("/api/users/999", json={"is_admin": True}).status_code == 404
    assert catalogue_client.patch("/api/users/999", json={}).status_code == 404


def test_a_reset_by_an_admin_signs_that_account_out(catalogue_client: TestClient) -> None:
    headers = add_user(catalogue_client, "reader")
    target = catalogue_client.get("/api/auth/me", headers=headers).json()

    response = catalogue_client.patch(
        f"/api/users/{target['id']}", json={"password": "a reset password"}
    )

    assert response.status_code == 200
    assert catalogue_client.get("/api/auth/me", headers=headers).status_code == 401
    login(anonymous(catalogue_client), "reader", "a reset password")


def test_an_admin_resetting_their_own_password_keeps_their_token(
    catalogue_client: TestClient,
) -> None:
    other_device = issue(catalogue_client)
    me = catalogue_client.get("/api/auth/me").json()

    response = catalogue_client.patch(f"/api/users/{me['id']}", json={"password": "a new one!"})

    assert response.status_code == 200
    assert catalogue_client.get("/api/auth/me").status_code == 200
    assert catalogue_client.get("/api/auth/me", headers=bearer(other_device)).status_code == 401


def test_deleting_an_account_signs_it_out_and_forgets_where_it_got_to(
    catalogue_client: TestClient, issue_id_: str
) -> None:
    headers = add_user(catalogue_client, "reader")
    target = catalogue_client.get("/api/auth/me", headers=headers).json()
    progress = f"/api/issues/{issue_id_}/progress"
    assert catalogue_client.put(progress, json={"page": 2}, headers=headers).status_code == 200
    assert catalogue_client.put(progress, json={"page": 3}).status_code == 200

    assert catalogue_client.delete(f"/api/users/{target['id']}").status_code == 204

    assert catalogue_client.get("/api/auth/me", headers=headers).status_code == 401
    assert catalogue_client.delete(f"/api/users/{target['id']}").status_code == 404
    # The account's positions went with it; the administrator's did not.
    me = catalogue_client.get("/api/auth/me").json()
    owners = catalogue_connection(catalogue_client).execute(
        "SELECT user_id FROM reading_progress WHERE issue_id = ?", (issue_id_,)
    )
    assert [row["user_id"] for row in owners] == [me["id"]]
    assert catalogue_client.get(progress).json()["page"] == 3


def test_nobody_deletes_themselves(catalogue_client: TestClient) -> None:
    add_user(catalogue_client, "second", is_admin=True)
    me = catalogue_client.get("/api/auth/me").json()

    response = catalogue_client.delete(f"/api/users/{me['id']}")

    assert response.status_code == 409
    assert set(response.json()) == {"detail"}
    assert catalogue_client.get("/api/auth/me").status_code == 200


# ------------------------------------------------------------- cache headers


def test_every_private_response_says_so(catalogue_client: TestClient) -> None:
    json_route = catalogue_client.get("/api/titles")
    health = catalogue_client.get("/api/health")
    feed = catalogue_client.get("/opds")
    refused = anonymous(catalogue_client).get("/api/titles")

    for response in (json_route, health, feed, refused):
        assert response.headers["cache-control"] == "private, no-cache"


def test_images_and_the_pdf_keep_their_own_cache_headers(
    catalogue_client: TestClient, issue_id_: str
) -> None:
    issue_ = catalogue_client.get(f"/api/issues/{issue_id_}").json()

    versioned = catalogue_client.get(issue_["cover_url"])
    unversioned = catalogue_client.get(f"/api/issues/{issue_id_}/thumb.jpg")
    pdf = catalogue_client.get(issue_["file_url"], headers={"Range": "bytes=0-9"})

    assert versioned.headers["cache-control"] == "private, max-age=31536000, immutable"
    assert unversioned.headers["cache-control"] == "private, no-cache"
    assert pdf.status_code == 206
    assert pdf.headers["cache-control"] == "private, max-age=0, must-revalidate"


def test_the_frontend_is_left_alone(catalogue_client: TestClient) -> None:
    """The SPA's files are public and cacheable; only the API is private."""
    response = catalogue_client.get("/")

    assert "private" not in response.headers.get("cache-control", "")


# ------------------------------------------------- the cookie's transport rules


@pytest.fixture
def proxied(catalogue_settings: Settings) -> Iterator[TestClient]:
    """A client connecting from the trusted proxy's address."""
    with TestClient(create_app(catalogue_settings), client=("127.0.0.1", 4321)) as client:
        yield client


def test_the_cookie_is_secure_when_the_client_used_https(proxied: TestClient) -> None:
    body = {"username": ADMIN_USERNAME, "password": ADMIN_PASSWORD}

    over_https = proxied.post("/api/auth/login", json=body, headers={"X-Forwarded-Proto": "https"})
    over_http = proxied.post("/api/auth/login", json=body)

    assert "Secure" in over_https.headers["set-cookie"]
    assert "Secure" not in over_http.headers["set-cookie"]


def test_an_untrusted_client_cannot_claim_https(catalogue_client: TestClient) -> None:
    client = anonymous(catalogue_client)

    response = client.post(
        "/api/auth/login",
        json={"username": ADMIN_USERNAME, "password": ADMIN_PASSWORD},
        headers={"X-Forwarded-Proto": "https"},
    )

    assert "Secure" not in response.headers["set-cookie"]


@pytest.mark.parametrize("site", ["cross-site", "same-site"])
def test_another_site_cannot_change_anything_with_the_cookie(
    catalogue_client: TestClient, issue_id_: str, site: str
) -> None:
    token = issue(catalogue_client)
    client = anonymous(catalogue_client)
    login(client, ADMIN_USERNAME, ADMIN_PASSWORD)
    from_elsewhere = {"Sec-Fetch-Site": site}

    assert client.post("/api/scan", headers=from_elsewhere).status_code == 403
    progress = client.put(
        f"/api/issues/{issue_id_}/progress", json={"page": 2}, headers=from_elsewhere
    )
    assert progress.status_code == 403
    assert client.post("/api/auth/logout", headers=from_elsewhere).status_code == 403
    # Reading is no harm, and the cookie still works for it.
    assert client.get("/api/auth/me", headers=from_elsewhere).status_code == 200
    # A bearer client is never asked: it does not send the cookie at all.
    client.cookies.clear()
    put = client.put(
        f"/api/issues/{issue_id_}/progress",
        json={"page": 2},
        headers={**from_elsewhere, **bearer(token)},
    )
    assert put.status_code == 200


@pytest.mark.parametrize("site", ["same-origin", "none", None])
def test_the_web_interface_itself_can_change_things_with_the_cookie(
    catalogue_client: TestClient, issue_id_: str, site: str | None
) -> None:
    client = anonymous(catalogue_client)
    login(client, ADMIN_USERNAME, ADMIN_PASSWORD)
    headers = {"Sec-Fetch-Site": site} if site is not None else {}

    response = client.put(f"/api/issues/{issue_id_}/progress", json={"page": 2}, headers=headers)

    assert response.status_code == 200


# A plain-http install gets no Fetch Metadata from the browser: the Origin decides.


@pytest.mark.parametrize(
    "origin",
    [
        "http://elsewhere.example",
        "http://testserver:8081",
        "http://shelf.testserver",
        "null",
        "not an origin",
        "http://testserver:port",
    ],
)
def test_without_fetch_metadata_another_origin_cannot_change_anything_with_the_cookie(
    catalogue_client: TestClient, issue_id_: str, origin: str
) -> None:
    client = anonymous(catalogue_client)
    login(client, ADMIN_USERNAME, ADMIN_PASSWORD)
    from_elsewhere = {"Origin": origin}

    assert client.post("/api/scan", headers=from_elsewhere).status_code == 403
    progress = client.put(
        f"/api/issues/{issue_id_}/progress", json={"page": 2}, headers=from_elsewhere
    )
    assert progress.status_code == 403
    assert client.post("/api/auth/logout", headers=from_elsewhere).status_code == 403
    assert client.get("/api/auth/me", headers=from_elsewhere).status_code == 200


@pytest.mark.parametrize(
    "origin",
    [
        "http://testserver",
        "HTTP://TestServer:80",
        "https://testserver",
        "https://testserver:443",
        None,
    ],
)
def test_without_fetch_metadata_the_servers_own_origin_can_change_things_with_the_cookie(
    catalogue_client: TestClient, issue_id_: str, origin: str | None
) -> None:
    client = anonymous(catalogue_client)
    login(client, ADMIN_USERNAME, ADMIN_PASSWORD)
    headers = {"Origin": origin} if origin is not None else {}

    response = client.put(f"/api/issues/{issue_id_}/progress", json={"page": 2}, headers=headers)

    assert response.status_code == 200


def test_the_origin_is_compared_with_the_host_the_trusted_proxy_was_asked_for(
    proxied: TestClient, issue_id_: str
) -> None:
    login(proxied, ADMIN_USERNAME, ADMIN_PASSWORD)
    forwarded = {"X-Forwarded-Proto": "https", "X-Forwarded-Host": "Paperstand.Example:443"}

    def put(origin: str) -> int:
        response = proxied.put(
            f"/api/issues/{issue_id_}/progress",
            json={"page": 2},
            headers={**forwarded, "Origin": origin},
        )
        return int(response.status_code)

    assert put("https://paperstand.example") == 200
    assert put("http://testserver") == 403


def test_without_fetch_metadata_the_base_url_can_change_things_with_the_cookie(
    catalogue_settings: Settings, issue_id_: str
) -> None:
    settings = quiet_settings(
        catalogue_settings.library,
        catalogue_settings.data,
        base_url="https://paperstand.example/reading/",
    )
    with TestClient(create_app(settings)) as client:
        login(client, ADMIN_USERNAME, ADMIN_PASSWORD)

        def put(origin: str) -> int:
            response = client.put(
                f"/api/issues/{issue_id_}/progress", json={"page": 2}, headers={"Origin": origin}
            )
            return int(response.status_code)

        assert put("https://paperstand.example") == 200
        assert put("http://testserver") == 200
        assert put("https://elsewhere.example") == 403


def test_a_bearer_or_basic_client_is_never_asked_where_it_came_from(
    catalogue_client: TestClient, issue_id_: str
) -> None:
    token = issue(catalogue_client)
    client = anonymous(catalogue_client)
    from_elsewhere = {"Origin": "http://elsewhere.example"}

    for credential in (bearer(token), basic(ADMIN_USERNAME, ADMIN_PASSWORD)):
        response = client.put(
            f"/api/issues/{issue_id_}/progress",
            json={"page": 2},
            headers={**from_elsewhere, **credential},
        )
        assert response.status_code == 200
