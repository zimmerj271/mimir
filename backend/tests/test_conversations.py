"""Deterministic service policy checks; PostgreSQL guarantees are tested separately."""

from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid7

import pytest
from pydantic import ValidationError
from sqlalchemy import URL

from mimir_backend.auth import SystemClock
from mimir_backend.conversation_postgres import ArchiveDatabase, ArchiveSQL
from mimir_backend.conversations import (
    ArchiveNotFound,
    ArchiveStorageError,
    BranchCreate,
    Conversation,
    ConversationCreate,
    ConversationPatch,
    ConversationService,
    ConversationVersionConflict,
    ListingBoundary,
    Page,
    ProjectCreate,
    ProjectPatch,
    ProjectService,
    UUID7IDs,
)


@pytest.fixture
def domain():
    current = Conversation(
        conversation_id=uuid7(),
        project_id=None,
        title="synthetic",
        status="active",
        conversation_version=4,
        updated_at=datetime(2026, 1, 1, tzinfo=UTC),
    )
    repository = SimpleNamespace(
        get=AsyncMock(return_value=current),
        lock=AsyncMock(return_value=current),
        create=AsyncMock(),
        patch=AsyncMock(),
        branch=AsyncMock(),
        list=AsyncMock(return_value=Page((), None)),
        history=AsyncMock(),
    )
    projects = SimpleNamespace(get=AsyncMock())
    service = ConversationService(repository, projects, SystemClock(), UUID7IDs())
    return service, repository, projects, current


@pytest.mark.asyncio
async def test_stale_metadata_and_branch_never_write(domain):
    service, repository, projects, current = domain
    with pytest.raises(ConversationVersionConflict):
        await service.patch(
            current.conversation_id,
            ConversationPatch(expected_conversation_version=3, project_id=uuid7()),
        )
    with pytest.raises(ConversationVersionConflict):
        await service.branch(
            current.conversation_id,
            BranchCreate(
                mode="after_revision",
                source_revision_id=uuid7(),
                expected_conversation_version=3,
                title="synthetic branch",
            ),
        )
    projects.get.assert_not_awaited()
    repository.patch.assert_not_awaited()
    repository.branch.assert_not_awaited()


@pytest.mark.asyncio
async def test_foreign_project_rejection_precedes_create_or_patch(domain):
    service, repository, projects, current = domain
    projects.get.side_effect = ArchiveNotFound("Archive resource not found")
    project = uuid7()
    with pytest.raises(ArchiveNotFound):
        await service.create(ConversationCreate(title="synthetic", project_id=project))
    with pytest.raises(ArchiveNotFound):
        await service.patch(
            current.conversation_id,
            ConversationPatch(expected_conversation_version=4, project_id=project),
        )
    repository.create.assert_not_awaited()
    repository.patch.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("limit", [0, 201])
async def test_listing_boundaries_are_bounded(domain, limit):
    service, repository, _, _ = domain
    with pytest.raises(ValueError):
        await service.list(limit)
    project = ProjectService(repository, UUID7IDs())
    with pytest.raises(ValueError):
        await project.list(limit)
    repository.list.assert_not_awaited()


@pytest.mark.asyncio
async def test_history_conceals_inaccessible_parent_before_bad_boundary(domain):
    service, repository, _, current = domain
    repository.get.side_effect = ArchiveNotFound("Archive resource not found")
    with pytest.raises(ArchiveNotFound):
        await service.history(current.conversation_id, 501, 0)
    repository.history.assert_not_awaited()
    repository.get.side_effect = None
    with pytest.raises(ValueError):
        await service.history(current.conversation_id, 501)
    with pytest.raises(ValueError):
        await service.history(current.conversation_id, 1, 0)


@pytest.mark.asyncio
async def test_empty_latest_selection_does_not_create(domain):
    service, repository, _, _ = domain
    assert await service.latest_active() is None
    repository.list.assert_awaited_once_with(1, None, active_only=True)
    repository.create.assert_not_awaited()


@pytest.mark.parametrize(
    "model,fields",
    [
        (ProjectCreate, {"name": ""}),
        (ProjectCreate, {"name": "a" * 201}),
        (ProjectPatch, {"name": None}),
        (ProjectPatch, {"status": None}),
        (ConversationPatch, {"expected_conversation_version": 0}),
        (ConversationPatch, {"expected_conversation_version": 1, "title": None}),
        (ConversationPatch, {"expected_conversation_version": 1, "status": None}),
        (ConversationCreate, {"title": "x", "user_id": uuid7()}),
    ],
)
def test_closed_command_models_preserve_null_and_owner_rules(model, fields):
    with pytest.raises(ValidationError):
        model(**fields)


def test_storage_boundary_redacts_invalid_rows():
    with pytest.raises(ArchiveStorageError) as caught:
        ArchiveSQL.model(Conversation, {"title": "synthetic private text"})
    assert "synthetic private text" not in str(caught.value)


def test_boundary_time_and_archive_role_validation():
    with pytest.raises(ValueError):
        ListingBoundary(datetime(2026, 1, 1), uuid7())
    for role in ("postgres", "mimir_owner", "mimir_worker", "mimir_auth"):
        with pytest.raises(ValueError):
            ArchiveDatabase(
                URL.create("postgresql+psycopg", username=role), SystemClock()
            )
    assert UUID7IDs().new().version == 7
