"""T08 authentication services; HTTP, cookies and CSRF belong to T09."""

from __future__ import annotations

import asyncio
import hashlib
import re
import secrets
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from typing import Protocol
from uuid import UUID, uuid7

from argon2 import PasswordHasher, extract_parameters
from argon2.exceptions import InvalidHashError, VerificationError
from argon2.low_level import Type


class AuthenticationRejected(Exception):
    """Uniform rejection: no account existence, credential or state disclosure."""


class InvalidAuthInput(Exception):
    """An administrator supplied invalid provisioning input."""


class AccountAlreadyExists(Exception):
    """Exact case-sensitive username already provisioned."""


class AuthStorageError(Exception):
    """Redacted storage failure; no driver SQL/parameters cross this boundary."""


class ClientType(StrEnum):
    CLI = "cli"
    WEB = "web"


@dataclass(frozen=True)
class Identity:
    user_id: UUID
    identity_id: UUID
    password_hash: str = field(repr=False)
    account_status: str


@dataclass(frozen=True)
class Session:
    user_id: UUID
    identity_id: UUID
    client_type: ClientType
    last_used_at: datetime
    expires_at: datetime
    absolute_expires_at: datetime
    revoked_at: datetime | None
    account_status: str

    def is_active(self, now: datetime) -> bool:
        return (
            self.account_status == "active"
            and self.revoked_at is None
            and now < self.expires_at
            and now < self.absolute_expires_at
        )


@dataclass(frozen=True)
class IssuedSession:
    token: str = field(repr=False)
    session: Session


class Clock(Protocol):
    def now(self) -> datetime: ...


class SystemClock:
    def now(self) -> datetime:
        return datetime.now(UTC)


class AuthRepository(Protocol):
    async def lookup_identity(self, username: str) -> Identity | None: ...

    async def lookup_session(self, token_hash: bytes) -> Session | None: ...

    async def issue_session(
        self, identity: Identity, token_hash: bytes, client_type: ClientType
    ) -> Session | None: ...

    async def renew_session(self, token_hash: bytes) -> Session | None: ...

    async def revoke_session(self, token_hash: bytes) -> bool: ...


class ProvisioningRepository(Protocol):
    async def provision(
        self,
        user_id: UUID,
        identity_id: UUID,
        username: str,
        password_hash: str,
        display_name: str | None,
    ) -> None: ...

    async def disable(self, user_id: UUID) -> bool: ...


class PasswordService:
    """Bounded Argon2id work off the event loop; PHC input is never trusted.

    Issuance uses RFC 9106's low-memory profile. Verification accepts bounded
    Argon2id v19 parameters so existing valid verifiers need not match defaults.
    T08 does not introduce password changes or an automatic rehash workflow.
    """

    def __init__(self, parallel_operations: int = 2) -> None:
        if parallel_operations < 1:
            raise ValueError("Password concurrency must be positive")
        self._hasher = PasswordHasher(
            time_cost=3,
            memory_cost=65536,
            parallelism=4,
            hash_len=32,
            salt_len=16,
            type=Type.ID,
        )
        self._slots = asyncio.Semaphore(parallel_operations)
        self._dummy_hash: str | None = None

    @staticmethod
    def valid_password(password: str) -> bool:
        # Explicit resource ceiling; never truncate/normalize secret material.
        try:
            return 0 < len(password.encode("utf-8")) <= 1048576
        except UnicodeEncodeError:
            return False

    def _perform(self, operation: str, value: str, password: str) -> str | bool:
        if operation == "hash":
            return self._hasher.hash(value)
        return self._hasher.verify(value, password)

    async def _work(self, operation: str, value: str, password: str = "") -> str | bool:
        async with self._slots:
            # Keep the slot occupied until the native worker finishes, even if
            # its caller is cancelled. Cancellation cannot defeat the work cap.
            task = asyncio.create_task(
                asyncio.to_thread(self._perform, operation, value, password)
            )
            try:
                return await asyncio.shield(task)
            except asyncio.CancelledError:
                while not task.done():
                    try:
                        await asyncio.shield(task)
                    except asyncio.CancelledError:
                        continue
                    except VerificationError:
                        break
                # Retrieve a completed exception, avoiding detached worker errors.
                if not task.cancelled():
                    task.exception()
                raise

    async def hash(self, password: str) -> str:
        if not self.valid_password(password):
            raise InvalidAuthInput("Password must contain 1 to 1048576 UTF-8 bytes")
        result = await self._work("hash", password)
        assert isinstance(result, str)
        return result

    async def verify(self, verifier: str | None, password: str) -> bool:
        if not self.valid_password(password):
            return False
        if verifier is None:
            # Unknown users still perform an expensive verification. Assignment
            # races may initialize twice, but all work uses the same bounded pool.
            if self._dummy_hash is None:
                self._dummy_hash = await self.hash(secrets.token_urlsafe(32))
            await self.verify(self._dummy_hash, password)
            return False
        try:
            params = extract_parameters(verifier)
            if not (
                params.type == Type.ID
                and params.version == 19
                and 65536 <= params.memory_cost <= 262144
                and 3 <= params.time_cost <= 10
                and 1 <= params.parallelism <= 8
                and 16 <= params.salt_len <= 64
                and 16 <= params.hash_len <= 64
            ):
                return False
            return bool(await self._work("verify", verifier, password))
        except InvalidHashError, VerificationError:
            return False


class SessionTokens:
    """256 random bits, URL-safe encoding, SHA-256 only across persistence."""

    def generate(self) -> str:
        return secrets.token_urlsafe(32)

    def digest(self, token: str) -> bytes:
        if re.fullmatch(r"[A-Za-z0-9_-]{43}", token) is None:
            raise AuthenticationRejected("Authentication rejected")
        return hashlib.sha256(token.encode("ascii")).digest()


class AuthService:
    def __init__(
        self,
        repository: AuthRepository,
        passwords: PasswordService,
        tokens: SessionTokens,
        clock: Clock,
    ) -> None:
        self._repository = repository
        self._passwords = passwords
        self._tokens = tokens
        self._clock = clock

    async def login(
        self, username: str, password: str, client_type: ClientType
    ) -> IssuedSession:
        if not username or not isinstance(client_type, ClientType):
            raise AuthenticationRejected("Authentication rejected")
        identity = await self._repository.lookup_identity(username)
        verified = await self._passwords.verify(
            identity.password_hash if identity else None, password
        )
        if not verified or identity is None or identity.account_status != "active":
            raise AuthenticationRejected("Authentication rejected")
        token = self._tokens.generate()
        session = await self._repository.issue_session(
            identity, self._tokens.digest(token), client_type
        )
        if session is None or not session.is_active(self._clock.now()):
            raise AuthenticationRejected("Authentication rejected")
        return IssuedSession(token, session)

    async def authenticate(self, token: str) -> Session:
        token_hash = self._tokens.digest(token)
        session = await self._repository.lookup_session(token_hash)
        if session is None or not session.is_active(self._clock.now()):
            raise AuthenticationRejected("Authentication rejected")
        # Atomic database revalidation closes lookup/renewal races. A successful
        # preliminary lookup alone is never exposed as an authenticated owner.
        renewed = await self._repository.renew_session(token_hash)
        if renewed is None or not renewed.is_active(self._clock.now()):
            raise AuthenticationRejected("Authentication rejected")
        return renewed

    async def logout(self, token: str) -> None:
        # Idempotent for well-formed missing, expired or already revoked tokens.
        await self._repository.revoke_session(self._tokens.digest(token))


class ProvisioningService:
    """Local administrator boundary, deliberately separate from login services."""

    def __init__(
        self, repository: ProvisioningRepository, passwords: PasswordService
    ) -> None:
        self._repository = repository
        self._passwords = passwords

    async def provision(
        self, username: str, password: str, display_name: str | None = None
    ) -> UUID:
        if not username:
            raise InvalidAuthInput("Username must not be empty")
        verifier = await self._passwords.hash(password)
        user_id = uuid7()
        await self._repository.provision(
            user_id, uuid7(), username, verifier, display_name
        )
        return user_id

    async def disable(self, user_id: UUID) -> bool:
        return await self._repository.disable(user_id)
