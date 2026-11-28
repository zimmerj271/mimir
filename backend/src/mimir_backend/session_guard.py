"""HTTP credential extraction and first-party browser checks."""

from dataclasses import dataclass, field
from urllib.parse import urlsplit

from fastapi import Request

from mimir_backend.auth import AuthenticationRejected, AuthService, ClientType, Session

COOKIE = "__Host-mimir_session"


class AmbiguousCredentials(Exception):
    pass


class Forbidden(Exception):
    pass


class LoginCredentialConflict(Exception):
    pass


class UntrustedHost(Exception):
    pass


class InsecureBrowserTransport(Exception):
    pass


class ForeignReadOrigin(Exception):
    pass


class ExpectedJSON(Exception):
    pass


@dataclass(frozen=True)
class Credentials:
    kind: ClientType
    token: str = field(repr=False)

    @classmethod
    def extract(
        cls, request: Request, *, login: ClientType | None = None
    ) -> Credentials | None:
        authorizations = request.headers.getlist("authorization")
        cookies = []
        for header in request.headers.getlist("cookie"):
            for item in header.split(";"):
                name, _, value = item.strip().partition("=")
                if name == COOKIE:
                    cookies.append(value)
        if (
            authorizations
            and cookies
            or len(authorizations) > 1
            or len(cookies) > 1
            or any("," in value for value in authorizations)
        ):
            raise AmbiguousCredentials
        if login is not None:
            if authorizations or (login == ClientType.CLI and cookies):
                raise LoginCredentialConflict
            return None
        if cookies:
            return cls(ClientType.WEB, cookies[0])
        if authorizations:
            scheme, _, token = authorizations[0].partition(" ")
            if scheme.lower() == "bearer":
                return cls(ClientType.CLI, token.lstrip(" "))
        raise AuthenticationRejected


@dataclass(frozen=True)
class Principal:
    session: Session
    credentials: Credentials = field(repr=False)


class SessionGuard:
    def __init__(
        self, auth: AuthService, cli_authority: str, browser_origin: str | None
    ):
        self.auth = auth
        cli = urlsplit(f"http://{cli_authority}")
        if (
            cli.hostname not in {"127.0.0.1", "::1", "localhost"}
            or cli.netloc != cli_authority
            or cli.username is not None
        ):
            raise ValueError("A loopback CLI authority is required")
        self.cli_authority = cli_authority
        self.browser_origin = browser_origin
        self.browser_authority: str | None = None
        if browser_origin is not None:
            origin = urlsplit(browser_origin)
            if (
                origin.scheme != "https"
                or not origin.hostname
                or origin.username is not None
                or browser_origin != f"https://{origin.netloc}"
                or not origin.netloc.isascii()
                or any(c.isspace() or c in "*,%\\\\#" for c in origin.netloc)
            ):
                raise ValueError("An exact HTTPS browser origin is required")
            self.browser_authority = origin.netloc

    def check_host(self, request: Request) -> None:
        hosts = request.headers.getlist("host")
        if len(hosts) != 1 or hosts[0] not in (
            self.cli_authority,
            self.browser_authority,
        ):
            raise UntrustedHost

    def check_transport(self, request: Request, client: ClientType) -> None:
        if client == ClientType.CLI:
            if request.headers["host"] != self.cli_authority:
                raise UntrustedHost
            return
        if request.scope["scheme"] != "https":
            raise InsecureBrowserTransport
        if (
            self.browser_origin is None
            or request.headers["host"] != self.browser_authority
        ):
            raise UntrustedHost
        origins = request.headers.getlist("origin")
        if request.method in {"GET", "HEAD"}:
            if origins and origins != [self.browser_origin]:
                raise ForeignReadOrigin
        elif origins != [self.browser_origin] or request.headers.getlist(
            "x-mimir-csrf"
        ) != ["1"]:
            raise Forbidden

    def login(self, request: Request, client: ClientType) -> None:
        self.check_host(request)
        Credentials.extract(request, login=client)
        self.check_transport(request, client)

    async def authenticate(self, request: Request) -> Principal:
        self.check_host(request)
        credential = Credentials.extract(request)
        assert credential is not None
        self.check_transport(request, credential.kind)
        return Principal(
            await self.auth.authenticate(credential.token, credential.kind), credential
        )
