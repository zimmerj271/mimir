"""Owner-scoped profile operations using the API role and transaction-local RLS."""

from typing import Protocol
from uuid import UUID

from pydantic import BaseModel, ConfigDict
from sqlalchemy import URL, text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import create_async_engine

from mimir_backend.auth import AuthStorageError, Session


class User(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    user_id: UUID
    username: str
    display_name: str | None


class UserPatch(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    display_name: str | None = None


class Profiles(Protocol):
    async def current(
        self, session: Session, patch: UserPatch | None
    ) -> User | None: ...


class PostgresProfiles:
    def __init__(self, url: URL) -> None:
        if url.drivername != "postgresql+psycopg" or url.username != "mimir_api":
            raise ValueError("Profile access requires the API role")
        self.engine = create_async_engine(url, hide_parameters=True)

    async def current(self, session: Session, patch: UserPatch | None) -> User | None:
        columns = (
            "user_id, archive.session_username(:identity) AS username, display_name"
        )
        scope = "user_id = :owner AND status = 'active'"
        params: dict[str, object] = {
            "owner": session.user_id,
            "identity": session.identity_id,
        }
        statement = f"SELECT {columns} FROM archive.users WHERE {scope}"
        if patch is not None and "display_name" in patch.model_fields_set:
            statement = f"UPDATE archive.users SET display_name=:name WHERE {scope} RETURNING {columns}"
            params["name"] = patch.display_name
        try:
            async with self.engine.begin() as conn:
                await conn.execute(
                    text("SELECT set_config('app.current_user_id', :actor, true)"),
                    {"actor": str(session.user_id)},
                )
                row = (
                    (await conn.execute(text(statement), params))
                    .mappings()
                    .one_or_none()
                )
                if row is None:
                    return None
                if row["username"] is None:
                    raise AuthStorageError("Authenticated profile is inconsistent")
                return User.model_validate(dict(row))
        except SQLAlchemyError:
            raise AuthStorageError("Profile storage unavailable") from None
