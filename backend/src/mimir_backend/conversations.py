"""T10 archive domain, scoped to one caller-owned database transaction.

The composition root opens ArchiveDatabase.transaction with the authenticated
Session and constructs these services from its repositories. This keeps future
T11 idempotency and T12 submission in the same transaction without hidden commits.
HTTP defaults, opaque cursors and deletion orchestration belong to later tasks.
"""

from dataclasses import dataclass
from datetime import datetime
from typing import Literal, Protocol
from uuid import UUID, uuid7

from pydantic import BaseModel, ConfigDict, Field, model_validator

from mimir_backend.auth import Clock

Status = Literal["active", "archived"]
BranchMode = Literal["after_revision", "replace_message"]


class ArchiveNotFound(Exception):
    """Uniform missing, foreign or conversation-fenced resource."""


class ConversationVersionConflict(Exception):
    """Expected conversation version does not match committed metadata."""


class InvalidBranchPoint(Exception):
    """The exact revision is not in the eligible parent's visible path."""


class ArchiveStorageError(Exception):
    """Redacted database/visibility failure, never a successful absence."""


class ArchiveModel(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)


class Project(ArchiveModel):
    project_id: UUID
    name: str
    description: str | None
    instructions: str | None
    status: Status
    created_at: datetime


class Conversation(ArchiveModel):
    conversation_id: UUID
    project_id: UUID | None
    title: str
    status: Status
    conversation_version: int
    updated_at: datetime


class ConversationDetail(Conversation):
    active_run_id: UUID | None


class VisibleMessage(ArchiveModel):
    message_id: UUID
    revision_id: UUID
    conversation_id: UUID
    role: Literal["user", "assistant", "system", "tool"]
    ordinal: int
    content_text: str = Field(repr=False)
    created_at: datetime


class ProjectCreate(ArchiveModel):
    name: str = Field(min_length=1, max_length=200)
    description: str | None = None


class ProjectPatch(ArchiveModel):
    name: str | None = Field(default=None, min_length=1, max_length=200)
    description: str | None = None
    status: Status | None = None

    @model_validator(mode="after")
    def nonnullable_fields(self) -> ProjectPatch:
        if any(
            name in self.model_fields_set and getattr(self, name) is None
            for name in ("name", "status")
        ):
            raise ValueError("Name and status cannot be null")
        return self


class ConversationCreate(ArchiveModel):
    # Internal command requires a resolved title; T11 owns HTTP omission defaults.
    title: str = Field(min_length=1, max_length=200)
    project_id: UUID | None = None


class ConversationPatch(ArchiveModel):
    expected_conversation_version: int = Field(ge=1)
    project_id: UUID | None = None
    title: str | None = Field(default=None, min_length=1, max_length=200)
    status: Status | None = None

    @model_validator(mode="after")
    def nonnullable_fields(self) -> ConversationPatch:
        if any(
            name in self.model_fields_set and getattr(self, name) is None
            for name in ("title", "status")
        ):
            raise ValueError("Title and status cannot be null")
        return self


class BranchCreate(ArchiveModel):
    mode: BranchMode
    source_revision_id: UUID
    expected_conversation_version: int = Field(ge=1)
    title: str  # Resolved by caller; no invented public omission/null behavior.


@dataclass(frozen=True)
class ListingBoundary:
    timestamp: datetime
    identity: UUID

    def __post_init__(self) -> None:
        if self.timestamp.tzinfo is None:
            raise ValueError("Listing boundary requires an aware timestamp")


@dataclass(frozen=True)
class Page[T, B]:
    items: tuple[T, ...]
    next_boundary: B | None


class IDs(Protocol):
    def new(self) -> UUID: ...


class UUID7IDs:
    def new(self) -> UUID:
        return uuid7()


class Projects(Protocol):
    async def get(self, identity: UUID) -> Project: ...
    async def create(self, identity: UUID, command: ProjectCreate) -> Project: ...
    async def patch(self, identity: UUID, command: ProjectPatch) -> Project: ...
    async def list(
        self, limit: int, before: ListingBoundary | None
    ) -> Page[Project, ListingBoundary]: ...


class Conversations(Protocol):
    async def get(self, identity: UUID) -> ConversationDetail: ...
    async def lock(self, identity: UUID) -> Conversation: ...
    async def create(
        self, identity: UUID, command: ConversationCreate, now: datetime
    ) -> Conversation: ...
    async def patch(
        self, identity: UUID, command: ConversationPatch, now: datetime
    ) -> Conversation: ...
    async def branch(
        self, identity: UUID, parent: Conversation, command: BranchCreate, now: datetime
    ) -> Conversation: ...
    async def list(
        self, limit: int, before: ListingBoundary | None, *, active_only: bool = False
    ) -> Page[Conversation, ListingBoundary]: ...
    async def history(
        self, identity: UUID, limit: int, before: int | None
    ) -> Page[VisibleMessage, int]: ...


class ProjectService:
    def __init__(self, repository: Projects, ids: IDs) -> None:
        self._repository = repository
        self._ids = ids

    async def create(self, command: ProjectCreate) -> Project:
        return await self._repository.create(self._ids.new(), command)

    async def get(self, identity: UUID) -> Project:
        return await self._repository.get(identity)

    async def patch(self, identity: UUID, command: ProjectPatch) -> Project:
        return await self._repository.patch(identity, command)

    async def list(
        self, limit: int = 50, before: ListingBoundary | None = None
    ) -> Page[Project, ListingBoundary]:
        if not 1 <= limit <= 200:
            raise ValueError("Project page limit must be 1..200")
        return await self._repository.list(limit, before)


class ConversationService:
    def __init__(
        self, repository: Conversations, projects: Projects, clock: Clock, ids: IDs
    ) -> None:
        self._repository = repository
        self._projects = projects
        self._clock = clock
        self._ids = ids

    async def create(self, command: ConversationCreate) -> Conversation:
        if command.project_id is not None:
            await self._projects.get(command.project_id)
        return await self._repository.create(
            self._ids.new(), command, self._clock.now()
        )

    async def get(self, identity: UUID) -> ConversationDetail:
        return await self._repository.get(identity)

    async def patch(self, identity: UUID, command: ConversationPatch) -> Conversation:
        current = await self._repository.lock(identity)
        self._expect(current, command.expected_conversation_version)
        if command.project_id is not None:
            await self._projects.get(command.project_id)
        return await self._repository.patch(identity, command, self._clock.now())

    async def branch(self, parent_id: UUID, command: BranchCreate) -> Conversation:
        parent = await self._repository.lock(parent_id)
        self._expect(parent, command.expected_conversation_version)
        return await self._repository.branch(
            self._ids.new(), parent, command, self._clock.now()
        )

    async def list(
        self, limit: int = 50, before: ListingBoundary | None = None
    ) -> Page[Conversation, ListingBoundary]:
        if not 1 <= limit <= 200:
            raise ValueError("Conversation page limit must be 1..200")
        return await self._repository.list(limit, before)

    async def latest_active(self) -> Conversation | None:
        page = await self._repository.list(1, None, active_only=True)
        return page.items[0] if page.items else None

    async def history(
        self, identity: UUID, limit: int = 100, before: int | None = None
    ) -> Page[VisibleMessage, int]:
        # Eligibility precedes boundary validation to conceal inaccessible parents.
        await self._repository.get(identity)
        if not 1 <= limit <= 500 or (before is not None and before < 1):
            raise ValueError("Invalid history boundary or page limit")
        return await self._repository.history(identity, limit, before)

    @staticmethod
    def _expect(current: Conversation, expected: int) -> None:
        if current.conversation_version != expected:
            raise ConversationVersionConflict("Conversation version conflict")
