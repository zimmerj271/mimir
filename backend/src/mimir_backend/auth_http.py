"""FastAPI routes for the T09 authentication boundary."""

from collections.abc import AsyncIterator, Callable, Coroutine
from contextlib import asynccontextmanager
from datetime import datetime
from json import JSONDecodeError
from typing import Annotated
from uuid import UUID, uuid7

from fastapi import APIRouter, Depends, FastAPI, Request, Response
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from fastapi.routing import APIRoute
from pydantic import BaseModel, ConfigDict, Field, SecretStr
from sqlalchemy import URL

from mimir_backend.auth import (
    AuthenticationRejected,
    AuthService,
    AuthStorageError,
    ClientType,
    Clock,
    PasswordService,
    Session,
    SessionTokens,
    SystemClock,
)
from mimir_backend.auth_postgres import AuthDatabase, PostgresAuthRepository
from mimir_backend.profile import PostgresProfiles, Profiles, User, UserPatch
from mimir_backend.session_guard import (
    COOKIE,
    AmbiguousCredentials,
    ExpectedJSON,
    Forbidden,
    ForeignReadOrigin,
    InsecureBrowserTransport,
    LoginCredentialConflict,
    Principal,
    SessionGuard,
    UntrustedHost,
)


class LoginRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    username: Annotated[str, Field(min_length=1)]
    password: Annotated[SecretStr, Field(min_length=1)]
    client_type: ClientType


class SessionMetadata(BaseModel):
    model_config = ConfigDict(from_attributes=True, extra="forbid")
    user_id: UUID
    expires_at: datetime
    absolute_expires_at: datetime
    client_type: ClientType


class CliLoginResponse(BaseModel):
    access_token: str = Field(repr=False)
    session: SessionMetadata


class WebLoginResponse(BaseModel):
    session: SessionMetadata


class ProfileMissing(Exception):
    pass


class InvalidRequest(Exception):
    pass


class JSONRequestRoute(APIRoute):
    """Check JSON transport and parsing before FastAPI resolves dependencies.

    Request.json() caches the decoded value for the framework. Catch only input
    decoding failures here, leaving receive errors and handler failures intact.
    """

    def get_route_handler(self) -> Callable[[Request], Coroutine[None, None, Response]]:
        handler = super().get_route_handler()

        async def checked(request: Request) -> Response:
            if self.body_field is not None:
                self.require_json(request)
                body = await request.body()
                try:
                    # json.loads(bytes) also accepts UTF-16/32. HTTP JSON bodies
                    # use UTF-8, so validate the encoding before Request.json().
                    body.decode("utf-8")
                    await request.json()
                except UnicodeDecodeError, JSONDecodeError:
                    raise InvalidRequest from None
            return await handler(request)

        return checked

    @staticmethod
    def require_json(request: Request) -> None:
        types = request.headers.getlist("content-type")
        if (
            len(types) != 1
            or types[0].split(";", 1)[0].strip().lower() != "application/json"
        ):
            raise ExpectedJSON


class AuthHTTP:
    def __init__(
        self, auth: AuthService, profiles: Profiles, clock: Clock, guard: SessionGuard
    ):
        self.auth = auth
        self.profiles = profiles
        self.clock = clock
        self.guard = guard

    def install(self, app: FastAPI) -> None:
        router = APIRouter(route_class=JSONRequestRoute)
        # Bind the shared guard once. The same owner context is available to future routes.
        principal = Depends(self.guard.authenticate)

        @router.post("/api/v1/auth/login", status_code=201, operation_id="login")
        async def login(
            body: LoginRequest, request: Request, response: Response
        ) -> CliLoginResponse | WebLoginResponse:
            self.guard.login(request, body.client_type)
            issued = await self.auth.login(
                body.username, body.password.get_secret_value(), body.client_type
            )
            session = SessionMetadata.model_validate(issued.session)
            response.headers["Cache-Control"] = "no-store"
            if body.client_type == ClientType.WEB:
                remaining = int(
                    (session.absolute_expires_at - self.clock.now()).total_seconds()
                )
                if remaining <= 0:
                    raise AuthenticationRejected
                self.cookie(response, issued.token, remaining)
                return WebLoginResponse(session=session)
            return CliLoginResponse(access_token=issued.token, session=session)

        @router.get("/api/v1/auth/session", operation_id="getSession")
        async def inspect(
            response: Response, actor: Principal = principal
        ) -> SessionMetadata:
            response.headers["Cache-Control"] = "no-store"
            return SessionMetadata.model_validate(actor.session)

        @router.post("/api/v1/auth/logout", status_code=204, operation_id="logout")
        async def logout(actor: Principal = principal) -> Response:
            await self.auth.logout(actor.credentials.token)
            response = Response(status_code=204, headers={"Cache-Control": "no-store"})
            if actor.credentials.kind == ClientType.WEB:
                self.cookie(response, "", 0)
            return response

        @router.get("/api/v1/users/me", operation_id="getMe")
        async def get_me(actor: Principal = principal) -> User:
            return await self.profile(actor.session, None)

        @router.patch("/api/v1/users/me", operation_id="patchMe")
        async def patch_me(body: UserPatch, actor: Principal = principal) -> User:
            if body.display_name is not None:
                try:
                    if len(body.display_name.encode("utf-8")) > 1_048_576:
                        raise InvalidRequest
                except UnicodeEncodeError:
                    raise InvalidRequest from None
            return await self.profile(actor.session, body)

        app.include_router(router)
        for exc_type in ERROR_RESPONSES:
            app.add_exception_handler(exc_type, self.error)

    async def profile(self, session: Session, patch: UserPatch | None) -> User:
        user = await self.profiles.current(session, patch)
        if user is None:
            raise ProfileMissing
        return user

    @staticmethod
    def cookie(response: Response, value: str, max_age: int) -> None:
        response.set_cookie(
            COOKIE,
            value,
            max_age=max_age,
            path="/",
            secure=True,
            httponly=True,
            samesite="strict",
        )

    @staticmethod
    async def error(request: Request, exc: Exception) -> JSONResponse:
        status, code = ERROR_RESPONSES[type(exc)]
        return JSONResponse(
            {
                "error": {
                    "code": code,
                    "message": code.replace("_", " ").capitalize(),
                    "details": {},
                },
                "request_id": str(uuid7()),
            },
            status_code=status,
            headers={"Cache-Control": "no-store"},
        )


ERROR_RESPONSES: dict[type[Exception], tuple[int, str]] = {
    AuthenticationRejected: (401, "unauthenticated"),
    AmbiguousCredentials: (400, "ambiguous_credentials"),
    LoginCredentialConflict: (400, "ambiguous_credentials"),
    UntrustedHost: (403, "forbidden"),
    InsecureBrowserTransport: (403, "forbidden"),
    ForeignReadOrigin: (403, "forbidden"),
    ExpectedJSON: (422, "invalid_request"),
    Forbidden: (403, "forbidden"),
    ProfileMissing: (404, "not_found"),
    AuthStorageError: (503, "dependency_unavailable"),
    InvalidRequest: (422, "invalid_request"),
    RequestValidationError: (422, "invalid_request"),
}


def create_app(
    *,
    auth_url: URL,
    api_url: URL,
    cli_authority: str,
    browser_origin: str | None = None,
) -> FastAPI:
    """Compose the ASGI application using deployment-supplied role credentials."""
    if auth_url.username != "mimir_auth":
        raise ValueError("Session access requires the authentication role")
    auth_db = AuthDatabase(auth_url)
    profiles = PostgresProfiles(api_url)
    clock = SystemClock()
    auth = AuthService(
        PostgresAuthRepository(auth_db), PasswordService(), SessionTokens(), clock
    )
    guard = SessionGuard(auth, cli_authority, browser_origin)

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        try:
            yield
        finally:
            await profiles.engine.dispose()
            await auth_db.close()

    app = FastAPI(
        lifespan=lifespan,
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
        redirect_slashes=False,
    )
    AuthHTTP(auth, profiles, clock, guard).install(app)
    return app
