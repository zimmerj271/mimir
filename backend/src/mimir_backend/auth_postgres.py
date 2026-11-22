"""SQLAlchemy 2.x adapters for V002 lookups and V006 restricted operations."""

from collections.abc import Mapping
from datetime import datetime
from uuid import UUID

from sqlalchemy import URL, text
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

from mimir_backend.auth import (
    AccountAlreadyExists,
    AuthStorageError,
    ClientType,
    Identity,
    Session,
)


class AuthDatabase:
    """Explicit per-role connection pool; never use migration credentials here.

    Bind parameters and driver errors are kept out of ordinary application logs.
    Close at the application/administrator composition root, not per request.
    """

    def __init__(self, url: URL) -> None:
        if url.drivername != "postgresql+psycopg" or url.username not in {
            "mimir_auth",
            "mimir_maintenance",
        }:
            raise ValueError("An explicit non-owner authentication role is required")
        self.engine: AsyncEngine = create_async_engine(
            url,
            echo=False,
            hide_parameters=True,
            isolation_level="READ COMMITTED",
            pool_size=2,
            max_overflow=0,
        )

    async def close(self) -> None:
        await self.engine.dispose()

    async def call(
        self, statement: str, parameters: Mapping[str, object]
    ) -> Mapping[str, object] | None:
        try:
            async with self.engine.begin() as connection:
                result = await connection.execute(text(statement), dict(parameters))
                row = result.mappings().one_or_none()
                return dict(row) if row is not None else None
        except IntegrityError as exc:
            # No server DETAIL, username, verifier or SQL parameter in exceptions.
            if (
                getattr(exc.orig, "sqlstate", None) == "23505"
                and getattr(getattr(exc.orig, "diag", None), "constraint_name", None)
                == "identities_username_key"
            ):
                raise AccountAlreadyExists("Username already provisioned") from None
            raise AuthStorageError("Authentication storage operation failed") from None
        except SQLAlchemyError:
            raise AuthStorageError("Authentication storage operation failed") from None


class AuthRows:
    """Validate the untyped SQL driver boundary before returning domain values."""

    @staticmethod
    def required[T](row: Mapping[str, object], name: str, kind: type[T]) -> T:
        value = row.get(name)
        if not isinstance(value, kind):
            raise AuthStorageError("Invalid authentication storage result")
        return value

    @classmethod
    def session(cls, row: Mapping[str, object] | None) -> Session | None:
        if row is None:
            return None
        try:
            client_type = ClientType(cls.required(row, "client_type", str))
        except ValueError:
            raise AuthStorageError("Invalid authentication storage result") from None
        revoked_at = row.get("revoked_at")
        if revoked_at is not None and not isinstance(revoked_at, datetime):
            raise AuthStorageError("Invalid authentication storage result")
        timestamps = [
            cls.required(row, name, datetime)
            for name in ("last_used_at", "expires_at", "absolute_expires_at")
        ]
        if any(value.tzinfo is None for value in timestamps) or (
            revoked_at is not None and revoked_at.tzinfo is None
        ):
            raise AuthStorageError("Invalid authentication storage timestamp")
        return Session(
            user_id=cls.required(row, "user_id", UUID),
            identity_id=cls.required(row, "identity_id", UUID),
            client_type=client_type,
            last_used_at=timestamps[0],
            expires_at=timestamps[1],
            absolute_expires_at=timestamps[2],
            revoked_at=revoked_at,
            account_status=cls.required(row, "account_status", str),
        )


class PostgresAuthRepository:
    def __init__(self, database: AuthDatabase) -> None:
        self._database = database

    async def lookup_identity(self, username: str) -> Identity | None:
        row = await self._database.call(
            "SELECT * FROM auth.lookup_identity(:username)", {"username": username}
        )
        if row is None:
            return None
        return Identity(
            user_id=AuthRows.required(row, "user_id", UUID),
            identity_id=AuthRows.required(row, "identity_id", UUID),
            password_hash=AuthRows.required(row, "password_hash", str),
            account_status=AuthRows.required(row, "account_status", str),
        )

    async def lookup_session(self, token_hash: bytes) -> Session | None:
        return AuthRows.session(
            await self._database.call(
                "SELECT * FROM auth.lookup_session(:token_hash)",
                {"token_hash": token_hash},
            )
        )

    async def issue_session(
        self, identity: Identity, token_hash: bytes, client_type: ClientType
    ) -> Session | None:
        return AuthRows.session(
            await self._database.call(
                "SELECT * FROM auth.issue_session(:identity_id, :verified_hash, "
                ":token_hash, :client_type)",
                {
                    "identity_id": identity.identity_id,
                    "verified_hash": identity.password_hash,
                    "token_hash": token_hash,
                    "client_type": client_type.value,
                },
            )
        )

    async def renew_session(self, token_hash: bytes) -> Session | None:
        return AuthRows.session(
            await self._database.call(
                "SELECT * FROM auth.renew_session(:token_hash)",
                {"token_hash": token_hash},
            )
        )

    async def revoke_session(self, token_hash: bytes) -> bool:
        row = await self._database.call(
            "SELECT auth.revoke_session(:token_hash) AS found",
            {"token_hash": token_hash},
        )
        if row is None:
            raise AuthStorageError("Invalid authentication storage result")
        return AuthRows.required(row, "found", bool)


class PostgresProvisioningRepository:
    def __init__(self, database: AuthDatabase) -> None:
        self._database = database

    async def provision(
        self,
        user_id: UUID,
        identity_id: UUID,
        username: str,
        password_hash: str,
        display_name: str | None,
    ) -> None:
        await self._database.call(
            "SELECT auth.provision_user(:user_id, :identity_id, :username, "
            ":password_hash, :display_name)",
            {
                "user_id": user_id,
                "identity_id": identity_id,
                "username": username,
                "password_hash": password_hash,
                "display_name": display_name,
            },
        )

    async def disable(self, user_id: UUID) -> bool:
        row = await self._database.call(
            "SELECT auth.disable_account(:user_id) AS found", {"user_id": user_id}
        )
        if row is None:
            raise AuthStorageError("Invalid authentication storage result")
        return AuthRows.required(row, "found", bool)
