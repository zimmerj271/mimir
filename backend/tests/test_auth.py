"""T08 deterministic policy, cryptography, rejection and local-admin boundaries."""

import asyncio
import base64
import getpass
import hashlib
import threading
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import patch
from uuid import uuid7

import pytest
from argon2 import extract_parameters
from argon2.low_level import Type
from sqlalchemy import URL
from sqlalchemy.exc import OperationalError

from mimir_backend.auth import (
    AccountAlreadyExists,
    AuthenticationRejected,
    AuthService,
    AuthStorageError,
    ClientType,
    Identity,
    InvalidAuthInput,
    PasswordService,
    ProvisioningService,
    Session,
    SessionTokens,
)
from mimir_backend.auth_postgres import AuthDatabase, AuthRows
from mimir_backend.provision import MaintenanceCommand


class FixedClock:
    def __init__(self):
        self.value = datetime(2026, 11, 22, tzinfo=UTC)

    def now(self):
        return self.value


class FakeRepository:
    def __init__(self, clock):
        self.clock = clock
        self.identities = {}
        self.sessions = {}
        self.renewal_wins = True
        self.issuance_wins = True

    async def provision(
        self, user_id, identity_id, username, password_hash, display_name
    ):
        if username in self.identities:
            raise AccountAlreadyExists()
        self.identities[username] = Identity(
            user_id, identity_id, password_hash, "active"
        )

    async def disable(self, user_id):
        found = False
        for username, identity in self.identities.items():
            if identity.user_id == user_id:
                self.identities[username] = replace(identity, account_status="disabled")
                found = True
        for key, session in self.sessions.items():
            if session.user_id == user_id:
                self.sessions[key] = replace(
                    session, revoked_at=self.clock.now(), account_status="disabled"
                )
        return found

    async def lookup_identity(self, username):
        return self.identities.get(username)

    async def lookup_session(self, token_hash):
        return self.sessions.get(token_hash)

    async def issue_session(self, identity, token_hash, client_type):
        if not self.issuance_wins:
            return None
        now = self.clock.now()
        session = Session(
            identity.user_id,
            identity.identity_id,
            client_type,
            now,
            now + timedelta(days=30),
            now + timedelta(days=90),
            None,
            "active",
        )
        self.sessions[token_hash] = session
        return session

    async def renew_session(self, token_hash):
        session = self.sessions.get(token_hash)
        if not self.renewal_wins or session is None:
            return None
        return session

    async def revoke_session(self, token_hash):
        session = self.sessions.get(token_hash)
        if session is None:
            return False
        self.sessions[token_hash] = replace(session, revoked_at=self.clock.now())
        return True


@pytest.fixture
def services():
    clock = FixedClock()
    repository = FakeRepository(clock)
    passwords = PasswordService()
    tokens = SessionTokens()
    return (
        ProvisioningService(repository, passwords),
        AuthService(repository, passwords, tokens, clock),
        repository,
        clock,
        tokens,
    )


@pytest.mark.asyncio
async def test_password_argon2id_random_salts_and_exact_verification():
    passwords = PasswordService()
    first = await passwords.hash("synthetic test password é")
    second = await passwords.hash("synthetic test password é")
    assert first != second
    params = extract_parameters(first)
    assert (params.type, params.version) == (Type.ID, 19)
    assert (params.memory_cost, params.time_cost, params.parallelism) == (65536, 3, 4)
    assert (params.salt_len, params.hash_len) == (16, 32)
    assert await passwords.verify(first, "synthetic test password é")
    assert not await passwords.verify(first, "synthetic test password e")
    assert not await passwords.verify(first, " synthetic test password é")
    assert not await passwords.verify(None, "unknown account synthetic input")


@pytest.mark.asyncio
async def test_password_resource_bounds_and_invalid_phc_fail_closed():
    passwords = PasswordService()
    for value in ("", "é" * 524289, "\ud800"):
        with pytest.raises(InvalidAuthInput):
            await passwords.hash(value)
        assert not await passwords.verify(None, value)
    assert passwords.valid_password("é" * 524288)
    verifier = await passwords.hash("synthetic password")
    for invalid in (
        "invalid",
        verifier.replace("m=65536", "m=999999999"),
        verifier.replace("m=65536", "m=8192"),
        verifier.replace("t=3", "t=1"),
        verifier.replace("t=3", "t=999"),
        verifier.replace("p=4", "p=999"),
        verifier.replace("argon2id", "argon2i"),
        verifier.replace("v=19", "v=16"),
        verifier[:-10],
    ):
        assert not await passwords.verify(invalid, "synthetic password")
    with pytest.raises(ValueError):
        PasswordService(parallel_operations=0)


@pytest.mark.asyncio
async def test_password_cancellation_keeps_native_work_bounded():
    passwords = PasswordService(parallel_operations=1)
    started = threading.Event()
    release = threading.Event()
    calls = 0

    def blocking_work(*args):
        nonlocal calls
        calls += 1
        started.set()
        assert release.wait(timeout=5)
        return "synthetic verifier"

    with patch.object(passwords, "_perform", side_effect=blocking_work):
        first = asyncio.create_task(passwords.hash("synthetic first"))
        assert await asyncio.to_thread(started.wait, 5)
        first.cancel()
        await asyncio.sleep(0)
        second = asyncio.create_task(passwords.hash("synthetic second"))
        first.cancel()  # Repeated cancellation must not release the slot either.
        await asyncio.sleep(0)
        assert calls == 1
        release.set()
        with pytest.raises(asyncio.CancelledError):
            await first
        await second
        assert calls == 2


def test_token_entropy_hash_and_representations():
    tokens = SessionTokens()
    generated = [tokens.generate() for _ in range(100)]
    assert len(set(generated)) == 100
    assert all(len(base64.urlsafe_b64decode(value + "=")) == 32 for value in generated)
    assert tokens.digest(generated[0]) == hashlib.sha256(generated[0].encode()).digest()
    assert len(tokens.digest(generated[0])) == 32
    identity = Identity(uuid7(), uuid7(), "synthetic-secret-verifier", "active")
    assert "synthetic-secret-verifier" not in repr(identity)


@pytest.mark.parametrize("value", ["", "short", "a" * 44, "é" * 43, "a" * 42 + "\n"])
def test_malformed_tokens_rejected(value):
    with pytest.raises(AuthenticationRejected, match="Authentication rejected"):
        SessionTokens().digest(value)


@pytest.mark.asyncio
async def test_provision_login_case_sensitivity_hash_storage_and_client_types(services):
    admin, auth, repository, clock, tokens = services
    owner = await admin.provision("Alice", "synthetic password", "Synthetic account")
    other = await admin.provision("alice", "synthetic other")
    assert owner.version == 7 and other.version == 7 and owner != other
    assert repository.identities["Alice"].identity_id.version == 7
    with pytest.raises(AccountAlreadyExists):
        await admin.provision("Alice", "synthetic duplicate")
    with pytest.raises(InvalidAuthInput):
        await admin.provision("", "synthetic invalid")
    for kind in ClientType:
        issued = await auth.login("Alice", "synthetic password", kind)
        assert issued.session.user_id == owner and issued.session.client_type == kind
        assert issued.session.expires_at == clock.now() + timedelta(days=30)
        assert issued.session.absolute_expires_at == clock.now() + timedelta(days=90)
        assert issued.token not in repr(issued)
        assert tokens.digest(issued.token) in repository.sessions
        assert all(
            isinstance(key, bytes) and len(key) == 32 for key in repository.sessions
        )
        assert (await auth.authenticate(issued.token)).user_id == owner
    assert len(repository.sessions) == 2


@pytest.mark.asyncio
async def test_login_rejections_do_not_issue_sessions(services):
    admin, auth, repository, _, _ = services
    owner = await admin.provision("Alice", "synthetic password")
    for username, password, kind in (
        ("unknown", "synthetic password", ClientType.CLI),
        ("alice", "synthetic password", ClientType.CLI),
        ("Alice", "wrong synthetic password", ClientType.CLI),
        ("", "synthetic password", ClientType.CLI),
        ("Alice", "synthetic password", "invalid"),
    ):
        with pytest.raises(AuthenticationRejected, match="Authentication rejected"):
            await auth.login(username, password, kind)
    repository.issuance_wins = False
    with pytest.raises(AuthenticationRejected):
        await auth.login("Alice", "synthetic password", ClientType.CLI)
    await admin.disable(owner)
    with pytest.raises(AuthenticationRejected):
        await auth.login("Alice", "synthetic password", ClientType.CLI)
    assert not repository.sessions


@pytest.mark.asyncio
async def test_authenticate_rechecks_atomic_outcome_logout_and_disable(services):
    admin, auth, repository, _, _ = services
    owner = await admin.provision("Alice", "synthetic password")
    issued = await auth.login("Alice", "synthetic password", ClientType.CLI)
    repository.renewal_wins = False
    with pytest.raises(AuthenticationRejected):
        await auth.authenticate(issued.token)
    repository.renewal_wins = True
    await auth.logout(issued.token)
    await auth.logout(issued.token)
    await auth.logout(SessionTokens().generate())
    with pytest.raises(AuthenticationRejected):
        await auth.authenticate(issued.token)
    second = await auth.login("Alice", "synthetic password", ClientType.WEB)
    assert await admin.disable(owner)
    assert await admin.disable(owner)
    assert not await admin.disable(uuid7())
    with pytest.raises(AuthenticationRejected):
        await auth.authenticate(second.token)


@pytest.mark.asyncio
@pytest.mark.parametrize("field", ["expires_at", "absolute_expires_at"])
async def test_expiry_equality_and_past_are_rejected(services, field):
    admin, auth, repository, clock, tokens = services
    await admin.provision("Alice", "synthetic password")
    issued = await auth.login("Alice", "synthetic password", ClientType.CLI)
    key = tokens.digest(issued.token)
    for offset in (0, -1):
        repository.sessions[key] = replace(
            issued.session, **{field: clock.now() + timedelta(seconds=offset)}
        )
        with pytest.raises(AuthenticationRejected):
            await auth.authenticate(issued.token)
    with pytest.raises(AuthenticationRejected):
        await auth.authenticate(tokens.generate())


@pytest.mark.asyncio
async def test_storage_errors_redacted_and_wrong_role_rejected():
    url = URL.create("postgresql+psycopg", username="mimir_auth", host="unused")
    database = AuthDatabase(url)
    assert database.engine.sync_engine.hide_parameters
    assert not database.engine.echo
    with patch.object(
        type(database.engine),
        "begin",
        side_effect=OperationalError(
            "synthetic secret SQL",
            {"token": "synthetic secret token"},
            Exception("raw"),
        ),
    ):
        with pytest.raises(AuthStorageError) as failure:
            await database.call("SELECT 1", {})
        assert str(failure.value) == "Authentication storage operation failed"
        assert failure.value.__suppress_context__
    await database.close()
    with pytest.raises(ValueError):
        AuthDatabase(url.set(username="mimir_migrator"))


def test_invalid_driver_results_fail_closed():
    with pytest.raises(AuthStorageError):
        AuthRows.session({"client_type": "invalid"})
    with pytest.raises(AuthStorageError):
        AuthRows.required({"user_id": "not a UUID"}, "user_id", type(uuid7()))
    assert AuthRows.session(None) is None


def test_admin_secret_file_safety_and_no_password_argument(tmp_path: Path):
    path = tmp_path / "credential"
    path.write_text("s" * 48)
    path.chmod(0o600)
    assert len(MaintenanceCommand.read_secret(path)) == 48
    path.chmod(0o644)
    with pytest.raises(InvalidAuthInput):
        MaintenanceCommand.read_secret(path)
    path.chmod(0o600)
    link = tmp_path / "link"
    link.symlink_to(path)
    with pytest.raises(OSError):
        MaintenanceCommand.read_secret(link)
    path.write_text("s" * 48 + "\nsecond line")
    with pytest.raises(InvalidAuthInput):
        MaintenanceCommand.read_secret(path)
    parser = MaintenanceCommand().parser()
    with pytest.raises(SystemExit):
        parser.parse_args(
            [
                "--credential-file",
                str(path),
                "create-user",
                "--username",
                "Alice",
                "--password",
                "unsafe",
            ]
        )


@pytest.mark.asyncio
async def test_admin_command_rejects_mismatch_and_echo_fallback_before_network(
    tmp_path,
):
    path = tmp_path / "credential"
    path.write_text("s" * 48)
    path.chmod(0o600)
    command = MaintenanceCommand()
    args = command.parser().parse_args(
        ["--credential-file", str(path), "create-user", "--username", "Alice"]
    )
    with patch(
        "mimir_backend.provision.getpass.getpass",
        side_effect=["synthetic first", "synthetic second"],
    ):
        with pytest.raises(InvalidAuthInput, match="Passwords do not match"):
            await command.run(args)
    with patch(
        "mimir_backend.provision.getpass.getpass", side_effect=getpass.GetPassWarning
    ):
        with pytest.raises(InvalidAuthInput, match="non-echoing terminal"):
            await command.run(args)


def test_admin_main_redacts_credential_failures(tmp_path, capsys):
    path = tmp_path / "missing-credential"
    with patch(
        "sys.argv",
        [
            "provision",
            "--credential-file",
            str(path),
            "disable-user",
            "--user-id",
            str(uuid7()),
        ],
    ):
        assert MaintenanceCommand().main() == 1
    output = capsys.readouterr()
    assert output.out == ""
    assert output.err == "Unable to read administrator input or credentials\n"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "issued_kind,presented_kind",
    [(ClientType.CLI, ClientType.WEB), (ClientType.WEB, ClientType.CLI)],
)
async def test_transport_mismatch_cannot_slide_idle_expiry(
    services, issued_kind, presented_kind
):
    admin, auth, repository, _, _ = services
    await admin.provision("Alice", "synthetic password")
    issued = await auth.login("Alice", "synthetic password", issued_kind)
    with patch.object(
        repository, "renew_session", wraps=repository.renew_session
    ) as renewal:
        with pytest.raises(AuthenticationRejected):
            await auth.authenticate(issued.token, presented_kind)
        renewal.assert_not_called()
