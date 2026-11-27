"""Exercise the ASGI producer with ephemeral credentials and canonical schemas."""

from datetime import UTC, datetime, timedelta
from pathlib import Path
from secrets import token_urlsafe
from unittest.mock import AsyncMock, Mock
from uuid import uuid7

import httpx
import jsonschema
import pytest
import yaml
from fastapi import FastAPI, Request

from mimir_backend.auth import (
    AuthenticationRejected,
    AuthService,
    AuthStorageError,
    ClientType,
    IssuedSession,
    Session,
)
from mimir_backend.auth_http import AuthHTTP
from mimir_backend.profile import Profiles, User
from mimir_backend.session_guard import (
    COOKIE,
    ForeignReadOrigin,
    InsecureBrowserTransport,
    LoginCredentialConflict,
    SessionGuard,
    UntrustedHost,
)

CONTRACTS = Path(__file__).resolve().parents[2] / "contracts"
ORIGIN = "https://browser.invalid"
BROWSER = {"Origin": ORIGIN, "X-Mimir-CSRF": "1"}
CLI = "127.0.0.1:8000"


@pytest.fixture(scope="module")
def validate():
    schemas = yaml.safe_load((CONTRACTS / "openapi.yaml").read_text())["components"][
        "schemas"
    ]

    def check(name, value):
        jsonschema.Draft202012Validator(
            {
                "$ref": f"#/components/schemas/{name}",
                "components": {"schemas": schemas},
            },
            format_checker=jsonschema.Draft202012Validator.FORMAT_CHECKER,
        ).validate(value)

    return check


class HTTPHarness:
    def __init__(self):
        self.now = datetime.now(UTC)
        self.user = uuid7()
        self.identity = uuid7()
        self.auth = AsyncMock(spec=AuthService)
        self.profiles = AsyncMock(spec=Profiles)
        self.profiles.current.return_value = User(
            user_id=self.user, username="sample", display_name=None
        )
        self.clock = Mock()
        self.clock.now.return_value = self.now
        self.credentials = {}
        self.auth.login.side_effect = self.issue
        self.auth.authenticate.side_effect = self.authenticate
        guard = SessionGuard(self.auth, CLI, ORIGIN)
        app = FastAPI()
        AuthHTTP(self.auth, self.profiles, self.clock, guard).install(app)
        self.transport = httpx.ASGITransport(app=app)

    def client(self, url):
        return httpx.AsyncClient(transport=self.transport, base_url=url + "/api/v1/")

    def issue(self, username, password, client_type):
        token = token_urlsafe(32)
        session = Session(
            self.user,
            self.identity,
            client_type,
            self.now,
            self.now + timedelta(days=30),
            self.now + timedelta(days=90),
            None,
            "active",
        )
        self.credentials[token] = session
        return IssuedSession(token, session)

    def authenticate(self, token, client_type):
        session = self.credentials.get(token)
        if session is None or session.client_type != client_type:
            raise AuthenticationRejected
        return session


@pytest.fixture
def harness():
    return HTTPHarness()


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", list(ClientType))
async def test_login_fresh_response_schema_cookie_bound_and_session(
    harness, validate, kind
):
    web = kind == ClientType.WEB
    async with harness.client(ORIGIN if web else f"http://{CLI}") as client:
        body = {
            "client_type": kind,
            "username": "sample",
            "password": token_urlsafe(16),
        }
        if web:
            client.cookies.set(
                COOKIE, token_urlsafe(32), domain="browser.invalid", path="/"
            )
        previous = None
        for _ in range(2):
            issued = await client.post(
                "auth/login", json=body, headers=BROWSER if web else {}
            )
            assert issued.status_code == 201
            validate("WebLoginResponse" if web else "CliLoginResponse", issued.json())
            if web:
                token = issued.cookies[COOKIE]
                attrs = {
                    p.strip().lower()
                    for p in issued.headers["set-cookie"].split(";")[1:]
                }
                assert attrs == {
                    "httponly",
                    "secure",
                    "path=/",
                    "samesite=strict",
                    f"max-age={90 * 86400}",
                }
                assert token not in issued.text
            else:
                token = issued.json()["access_token"]
                assert "set-cookie" not in issued.headers
            assert token != previous
            previous = token
            assert issued.headers["cache-control"] == "no-store"
        headers = {} if web else {"Authorization": f"Bearer {token}"}
        metadata = await client.get("auth/session", headers=headers)
        assert metadata.headers["cache-control"] == "no-store"
        validate("Session", metadata.json())
        assert metadata.json() == issued.json()["session"]
        validate("User", (await client.get("users/me", headers=headers)).json())
        logout = await client.post("auth/logout", headers=BROWSER if web else headers)
        assert logout.status_code == 204 and not logout.content
        assert logout.headers["cache-control"] == "no-store"
        if web:
            assert {
                p.strip().lower() for p in logout.headers["set-cookie"].split(";")[1:]
            } == {"httponly", "secure", "path=/", "samesite=strict", "max-age=0"}
        else:
            assert "set-cookie" not in logout.headers


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("method", "path", "payload"),
    [
        ("GET", "auth/session", None),
        ("GET", "users/me", None),
        ("PATCH", "users/me", {}),
        ("POST", "auth/logout", None),
    ],
)
async def test_missing_credentials_on_each_protected_route(
    harness, validate, method, path, payload
):
    async with harness.client(f"http://{CLI}") as client:
        response = await client.request(method, path, json=payload)
        assert response.status_code == 401
        validate("Error", response.json())
        assert response.json()["error"]["code"] == "unauthenticated"
        harness.profiles.current.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "case",
    [
        "mixed",
        "duplicate_auth",
        "duplicate_cookie",
        "two_cookie_headers",
        "bad_scheme",
        "empty",
        "cli_as_cookie",
        "web_as_bearer",
    ],
)
async def test_no_fallback_after_conflict_or_transport_mismatch(harness, case):
    token = harness.issue(
        "", "", ClientType.CLI if case == "cli_as_cookie" else ClientType.WEB
    ).token
    auth = ("Authorization", f"Bearer {token}")
    cookie = ("Cookie", f"{COOKIE}={token}")
    fields = {
        "mixed": [auth, cookie],
        "duplicate_auth": [auth, auth],
        "duplicate_cookie": [("Cookie", f"{cookie[1]}; {cookie[1]}")],
        "two_cookie_headers": [cookie, cookie],
        "bad_scheme": [("Authorization", "Basic " + token)],
        "empty": [("Authorization", "")],
        "cli_as_cookie": [cookie],
        "web_as_bearer": [auth],
    }
    async with harness.client(
        ORIGIN if case == "cli_as_cookie" else f"http://{CLI}"
    ) as client:
        response = await client.get("auth/session", headers=fields[case])
        if case in {
            "mixed",
            "duplicate_auth",
            "duplicate_cookie",
            "two_cookie_headers",
        }:
            assert response.status_code == 400
            assert response.json()["error"]["code"] == "ambiguous_credentials"
            harness.auth.authenticate.assert_not_awaited()
        else:
            assert response.status_code == 401
        assert token not in response.text


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "headers",
    [
        {},
        {"Origin": ORIGIN},
        {"X-Mimir-CSRF": "1"},
        {"Origin": "null", "X-Mimir-CSRF": "1"},
        {"Origin": ORIGIN + "/", "X-Mimir-CSRF": "1"},
        {"Origin": ORIGIN, "X-Mimir-CSRF": "0"},
        [("Origin", ORIGIN), ("Origin", ORIGIN), ("X-Mimir-CSRF", "1")],
    ],
)
async def test_mutation_rejected_before_renewal_or_logout(harness, headers):
    async with harness.client(ORIGIN) as client:
        client.cookies.set(COOKIE, token_urlsafe(32))
        response = await client.post("auth/logout", headers=headers)
        assert response.status_code == 403
        assert response.json()["error"]["code"] == "forbidden"
        harness.auth.authenticate.assert_not_awaited()
        harness.auth.logout.assert_not_awaited()


@pytest.mark.asyncio
async def test_invalid_input_and_storage_failure_keep_sensitive_values_private(
    harness, validate
):
    async with harness.client(f"http://{CLI}") as client:
        secret = token_urlsafe(32)
        body = {"client_type": "cli", "username": "sample", "password": secret}
        for invalid in [
            {**body, "extra": secret},
            {**body, "client_type": secret},
            {**body, "password": ""},
        ]:
            result = await client.post("auth/login", json=invalid)
            assert result.status_code == 422
            assert secret not in result.text
            validate("Error", result.json())
        harness.auth.login.side_effect = AuthStorageError(secret)
        result = await client.post("auth/login", json=body)
        assert result.status_code == 503
        assert secret not in result.text
        harness.auth.login.side_effect = AuthenticationRejected(secret)
        result = await client.post("auth/login", json=body)
        assert result.status_code == 401
        assert secret not in result.text


@pytest.mark.asyncio
async def test_display_name_utf8_boundary(harness):
    async with harness.client(f"http://{CLI}") as client:
        issued = harness.issue("", "", ClientType.CLI)
        headers = {"Authorization": f"Bearer {issued.token}"}
        text = "é" * 524288
        assert (
            await client.patch("users/me", json={"display_name": text}, headers=headers)
        ).status_code == 200
        assert (
            await client.patch(
                "users/me", json={"display_name": text + "a"}, headers=headers
            )
        ).status_code == 422
        assert harness.profiles.current.await_count == 1


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("url", "headers", "client_type", "reason"),
    [
        (f"http://{CLI}", {"Authorization": "present"}, "cli", LoginCredentialConflict),
        (
            f"http://{CLI}",
            {"Cookie": f"{COOKIE}=expired"},
            "cli",
            LoginCredentialConflict,
        ),
        (
            ORIGIN,
            {**BROWSER, "Authorization": "present"},
            "web",
            LoginCredentialConflict,
        ),
        (
            "http://browser.invalid",
            {**BROWSER, "X-Forwarded-Proto": "https"},
            "web",
            InsecureBrowserTransport,
        ),
        (
            "https://untrusted.invalid",
            {**BROWSER, "X-Forwarded-Host": "browser.invalid"},
            "web",
            UntrustedHost,
        ),
    ],
)
async def test_login_policy_denies_before_issuance(
    harness, url, headers, client_type, reason
):
    request = httpx.Request("POST", url + "/api/v1/auth/login", headers=headers)
    guard = SessionGuard(harness.auth, CLI, ORIGIN)
    with pytest.raises(reason):
        guard.login(
            Request(
                {
                    "type": "http",
                    "scheme": request.url.scheme,
                    "method": "POST",
                    "headers": [(k.lower(), v) for k, v in request.headers.raw],
                }
            ),
            ClientType(client_type),
        )
    harness.auth.login.assert_not_awaited()


@pytest.mark.asyncio
async def test_get_with_foreign_origin_rejected_before_renewal(harness):
    guard = SessionGuard(harness.auth, CLI, ORIGIN)
    request = Request(
        {
            "type": "http",
            "scheme": "https",
            "method": "GET",
            "headers": [
                (b"cookie", f"{COOKIE}={token_urlsafe(32)}".encode()),
                (b"host", b"browser.invalid"),
                (b"origin", b"https://other.invalid"),
            ],
        }
    )
    with pytest.raises(ForeignReadOrigin):
        await guard.authenticate(request)
    harness.auth.authenticate.assert_not_awaited()
