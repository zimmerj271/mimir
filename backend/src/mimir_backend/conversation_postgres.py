"""SQLAlchemy archive repositories; Flyway owns schema and visibility SQL."""

from collections.abc import AsyncIterator, Mapping
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import datetime
from uuid import UUID

from pydantic import ValidationError
from sqlalchemy import URL, text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncConnection, create_async_engine

from mimir_backend.auth import AuthenticationRejected, Clock, Session
from mimir_backend.conversations import (
    ArchiveModel,
    ArchiveNotFound,
    ArchiveStorageError,
    BranchCreate,
    Conversation,
    ConversationCreate,
    ConversationDetail,
    ConversationPatch,
    ConversationVersionConflict,
    InvalidBranchPoint,
    ListingBoundary,
    Page,
    Project,
    ProjectCreate,
    ProjectPatch,
    VisibleMessage,
)

PROJECT_COLUMNS = "project_id, name, description, instructions, status, created_at"
CONVERSATION_COLUMNS = (
    "conversation_id, project_id, title, status, conversation_version, updated_at"
)


class ArchiveSQL:
    """Bound actor and parameterized SQL; never accept client-selected scope."""

    def __init__(self, connection: AsyncConnection, owner_id: UUID) -> None:
        self.connection = connection
        self.owner_id = owner_id

    async def rows(
        self, statement: str, parameters: Mapping[str, object]
    ) -> list[Mapping[str, object]]:
        result = await self.connection.execute(
            text(statement), {**parameters, "owner": self.owner_id}
        )
        return [dict(row) for row in result.mappings()]

    async def one(
        self, statement: str, parameters: Mapping[str, object]
    ) -> Mapping[str, object]:
        rows = await self.rows(statement, parameters)
        if not rows:
            raise ArchiveNotFound("Archive resource not found")
        if len(rows) != 1:
            raise ArchiveStorageError("Invalid archive storage result")
        return rows[0]

    @staticmethod
    def model[T: ArchiveModel](kind: type[T], row: Mapping[str, object]) -> T:
        try:
            return kind.model_validate(dict(row))
        except ValidationError:
            raise ArchiveStorageError("Invalid archive storage result") from None


class PostgresProjects:
    def __init__(self, sql: ArchiveSQL) -> None:
        self._sql = sql

    async def get(self, identity: UUID) -> Project:
        return self._sql.model(
            Project,
            await self._sql.one(
                f"SELECT {PROJECT_COLUMNS} FROM archive.projects "
                "WHERE user_id=:owner AND project_id=:identity",
                {"identity": identity},
            ),
        )

    async def create(self, identity: UUID, command: ProjectCreate) -> Project:
        return self._sql.model(
            Project,
            await self._sql.one(
                "INSERT INTO archive.projects(project_id,user_id,name,description) "
                f"VALUES (:identity,:owner,:name,:description) RETURNING {PROJECT_COLUMNS}",
                {"identity": identity, **command.model_dump()},
            ),
        )

    async def patch(self, identity: UUID, command: ProjectPatch) -> Project:
        fields = command.model_dump(exclude_unset=True)
        if not fields:
            return await self.get(identity)
        # Identifiers come only from the closed model, never user-provided keys.
        assignments = ", ".join(f"{name}=:{name}" for name in fields)
        return self._sql.model(
            Project,
            await self._sql.one(
                f"UPDATE archive.projects SET {assignments} "
                f"WHERE user_id=:owner AND project_id=:identity RETURNING {PROJECT_COLUMNS}",
                {"identity": identity, **fields},
            ),
        )

    async def list(
        self, limit: int, before: ListingBoundary | None
    ) -> Page[Project, ListingBoundary]:
        boundary = ""
        params: dict[str, object] = {"limit": limit + 1}
        if before is not None:
            boundary = "AND (created_at,project_id) < (:timestamp,:identity)"
            params.update(timestamp=before.timestamp, identity=before.identity)
        rows = await self._sql.rows(
            f"SELECT {PROJECT_COLUMNS} FROM archive.projects WHERE user_id=:owner "
            f"{boundary} ORDER BY created_at DESC, project_id DESC LIMIT :limit",
            params,
        )
        items = tuple(self._sql.model(Project, row) for row in rows[:limit])
        next_boundary = (
            ListingBoundary(items[-1].created_at, items[-1].project_id)
            if len(rows) > limit
            else None
        )
        return Page(items, next_boundary)


class PostgresConversations:
    def __init__(self, sql: ArchiveSQL) -> None:
        self._sql = sql

    async def get(self, identity: UUID) -> ConversationDetail:
        columns = ", ".join(
            f"c.{name.strip()}" for name in CONVERSATION_COLUMNS.split(",")
        )
        return self._sql.model(
            ConversationDetail,
            await self._sql.one(
                f"SELECT {columns}, r.run_id AS active_run_id "
                "FROM archive.visible_conversations c LEFT JOIN runtime.model_runs r "
                "ON r.user_id=c.user_id AND r.conversation_id=c.conversation_id "
                "AND r.kind='conversation' AND r.status IN ('pending','running') "
                "WHERE c.user_id=:owner AND c.conversation_id=:identity",
                {"identity": identity},
            ),
        )

    async def lock(self, identity: UUID) -> Conversation:
        # Lock base metadata first; query visibility in a fresh READ COMMITTED
        # statement after any lock wait, as with V004's append/snapshot protocol.
        await self._sql.one(
            "SELECT conversation_id FROM archive.conversations "
            "WHERE user_id=:owner AND conversation_id=:identity FOR UPDATE",
            {"identity": identity},
        )
        return self._sql.model(
            Conversation,
            (await self.get(identity)).model_dump(exclude={"active_run_id"}),
        )

    async def create(
        self, identity: UUID, command: ConversationCreate, now: datetime
    ) -> Conversation:
        return self._sql.model(
            Conversation,
            await self._sql.one(
                "INSERT INTO archive.conversations(conversation_id,user_id,project_id,"
                "title,created_at,updated_at) VALUES (:identity,:owner,:project_id,:title,:now,:now) "
                f"RETURNING {CONVERSATION_COLUMNS}",
                {"identity": identity, "now": now, **command.model_dump()},
            ),
        )

    async def patch(
        self, identity: UUID, command: ConversationPatch, now: datetime
    ) -> Conversation:
        current = await self.get(identity)
        fields = command.model_dump(
            exclude_unset=True, exclude={"expected_conversation_version"}
        )
        if current.conversation_version != command.expected_conversation_version:
            raise ConversationVersionConflict("Conversation version conflict")
        if all(getattr(current, name) == value for name, value in fields.items()):
            return self._sql.model(
                Conversation, current.model_dump(exclude={"active_run_id"})
            )
        assignments = ", ".join(f"{name}=:{name}" for name in fields)
        rows = await self._sql.rows(
            f"UPDATE archive.conversations SET {assignments}, "
            "conversation_version=conversation_version+1, updated_at=greatest(updated_at,:now) "
            "WHERE user_id=:owner AND conversation_id=:identity "
            "AND conversation_version=:expected "
            f"RETURNING {CONVERSATION_COLUMNS}",
            {
                "identity": identity,
                "expected": command.expected_conversation_version,
                "now": now,
                **fields,
            },
        )
        if not rows:
            raise ConversationVersionConflict("Conversation version conflict")
        return self._sql.model(Conversation, rows[0])

    async def branch(
        self, identity: UUID, parent: Conversation, command: BranchCreate, now: datetime
    ) -> Conversation:
        points = await self._sql.rows(
            "SELECT ordinal FROM archive.conversation_visible_revisions "
            "WHERE user_id=:owner AND conversation_id=:parent AND revision_id=:revision",
            {"parent": parent.conversation_id, "revision": command.source_revision_id},
        )
        if not points:
            raise InvalidBranchPoint("Revision is not in the visible parent history")
        point = points[0]["ordinal"]
        if not isinstance(point, int):
            raise ArchiveStorageError("Invalid archive storage result")
        params: dict[str, object] = {
            "identity": identity,
            "project": parent.project_id,
            "parent": parent.conversation_id,
            "revision": command.source_revision_id,
            "mode": command.mode,
            "title": command.title,
            "now": now,
            "prefix": point - (1 if command.mode == "replace_message" else 0),
        }
        row = await self._sql.one(
            "INSERT INTO archive.conversations(conversation_id,user_id,project_id,title,"
            "parent_conversation_id,branch_point_revision_id,branch_mode,created_at,updated_at) "
            "VALUES (:identity,:owner,:project,:title,:parent,:revision,:mode,:now,:now) "
            f"RETURNING {CONVERSATION_COLUMNS}",
            params,
        )
        await self._sql.connection.execute(
            text(
                "INSERT INTO archive.conversation_branch_revisions "
                "(user_id,conversation_id,ordinal,message_id,revision_id) "
                "SELECT user_id,:identity,ordinal,message_id,revision_id "
                "FROM archive.conversation_visible_revisions "
                "WHERE user_id=:owner AND conversation_id=:parent AND ordinal<=:prefix "
                "ORDER BY ordinal"
            ),
            {**params, "owner": self._sql.owner_id},
        )
        return self._sql.model(Conversation, row)

    async def list(
        self, limit: int, before: ListingBoundary | None, *, active_only: bool = False
    ) -> Page[Conversation, ListingBoundary]:
        clauses = ["user_id=:owner"]
        params: dict[str, object] = {"limit": limit + 1}
        if active_only:
            clauses.append("status='active'")
        if before is not None:
            clauses.append("(updated_at,conversation_id) < (:timestamp,:identity)")
            params.update(timestamp=before.timestamp, identity=before.identity)
        rows = await self._sql.rows(
            f"SELECT {CONVERSATION_COLUMNS} FROM archive.visible_conversations "
            f"WHERE {' AND '.join(clauses)} "
            "ORDER BY updated_at DESC,conversation_id DESC LIMIT :limit",
            params,
        )
        items = tuple(self._sql.model(Conversation, row) for row in rows[:limit])
        next_boundary = (
            ListingBoundary(items[-1].updated_at, items[-1].conversation_id)
            if len(rows) > limit
            else None
        )
        return Page(items, next_boundary)

    async def history(
        self, identity: UUID, limit: int, before: int | None
    ) -> Page[VisibleMessage, int]:
        boundary = "AND v.ordinal < :before" if before is not None else ""
        rows = await self._sql.rows(
            "SELECT v.message_id,v.revision_id,v.conversation_id,v.ordinal,"
            "m.role,m.created_at,r.content_text FROM archive.conversation_visible_revisions v "
            "JOIN archive.visible_conversations c USING (user_id,conversation_id) "
            "JOIN archive.messages m ON m.user_id=v.user_id AND m.message_id=v.message_id "
            "JOIN archive.message_revisions r ON r.user_id=v.user_id "
            "AND r.message_id=v.message_id AND r.revision_id=v.revision_id "
            "WHERE v.user_id=:owner AND v.conversation_id=:identity "
            f"{boundary} ORDER BY v.ordinal DESC LIMIT :limit",
            {"identity": identity, "before": before, "limit": limit + 1},
        )
        items = tuple(
            self._sql.model(VisibleMessage, row) for row in reversed(rows[:limit])
        )
        return Page(items, items[0].ordinal if len(rows) > limit else None)


@dataclass(frozen=True)
class ArchiveRepositories:
    connection: AsyncConnection
    projects: PostgresProjects
    conversations: PostgresConversations


class ArchiveDatabase:
    """Short transaction boundary, account authentication and redacted failures.

    Caller must supply a T09-authenticated Session, never a body/path owner ID.
    No method commits independently: the yielded connection also permits T11's
    operation ledger to participate in the same atomic transaction.
    """

    def __init__(self, url: URL, clock: Clock) -> None:
        if url.drivername != "postgresql+psycopg" or url.username != "mimir_api":
            raise ValueError("Archive access requires the API role")
        self._clock = clock
        self.engine = create_async_engine(
            url, echo=False, hide_parameters=True, pool_size=4, max_overflow=0
        )

    async def close(self) -> None:
        await self.engine.dispose()

    @asynccontextmanager
    async def transaction(
        self, session: Session, *, write: bool = False
    ) -> AsyncIterator[ArchiveRepositories]:
        if not session.is_active(self._clock.now()):
            raise AuthenticationRejected("Authentication rejected")
        try:
            async with self.engine.connect() as connection:
                await connection.execution_options(
                    isolation_level="READ COMMITTED" if write else "REPEATABLE READ"
                )
                async with connection.begin():
                    if not write:
                        await connection.execute(text("SET TRANSACTION READ ONLY"))
                    await connection.execute(
                        text("SELECT set_config('app.current_user_id', :actor, true)"),
                        {"actor": str(session.user_id)},
                    )
                    sql = ArchiveSQL(connection, session.user_id)
                    users = await sql.rows(
                        "SELECT status FROM archive.users WHERE user_id=:owner"
                        + (" FOR SHARE" if write else ""),
                        {},
                    )
                    fences = await sql.rows(
                        "SELECT deletion_id FROM runtime.deletion_controls "
                        "WHERE user_id=:owner AND fenced_at IS NOT NULL LIMIT 1",
                        {},
                    )
                    if not users or users[0]["status"] != "active" or fences:
                        raise AuthenticationRejected("Authentication rejected")
                    yield ArchiveRepositories(
                        connection, PostgresProjects(sql), PostgresConversations(sql)
                    )
        except SQLAlchemyError:
            raise ArchiveStorageError("Archive storage unavailable") from None
