"""T03–T08 real PostgreSQL 18/Flyway tests, never skip a missing daemon.

All fixtures use unique labelled disposable volumes. T03 fixtures stop at V001;
T04–T08 exercise real V002–V006 on fresh and upgrade paths.
"""

import asyncio
import concurrent.futures
import contextlib
import hashlib
import json
import os
import secrets
import select
import shutil
import subprocess
import sys
import threading
import time
import uuid
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import patch

import httpx
import pytest
import pytest_asyncio
from sqlalchemy import URL, text
from sqlalchemy.ext.asyncio import create_async_engine

from mimir_backend.auth import (
    AccountAlreadyExists,
    AuthenticationRejected,
    AuthService,
    AuthStorageError,
    ClientType,
    PasswordService,
    ProvisioningService,
    SessionTokens,
    SystemClock,
)
from mimir_backend.auth_http import create_app
from mimir_backend.auth_postgres import (
    AuthDatabase,
    PostgresAuthRepository,
    PostgresProvisioningRepository,
)
from mimir_backend.provision import MaintenanceCommand

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "deployment"))
from migrate import ROLES, MigrationPolicy, MigrationRunner  # noqa: E402

RUNTIME = ("api", "worker", "auth", "maintenance")


class DatabaseStack:
    def __init__(self, directory: Path, through_version: int = 1) -> None:
        self.directory = directory
        directory.chmod(0o700)
        self.token = uuid.uuid4().hex
        self.project = f"mimir-t03-{self.token[:12]}"
        self.env = os.environ.copy()
        self.env["COMPOSE_PROJECT_NAME"] = self.project
        self.passwords = {}
        self.extra_passwords = []
        self.calls = []
        for role in ("postgres_bootstrap", *[f"mimir_{role}" for role in ROLES]):
            path = directory / f"{role}_password"
            path.write_text(secrets.token_urlsafe(48))
            path.chmod(0o600)
            variable = (
                "MIMIR_POSTGRES_PASSWORD_FILE"
                if role == "postgres_bootstrap"
                else f"MIMIR_{role.removeprefix('mimir_').upper()}_PASSWORD_FILE"
            )
            self.env[variable] = str(path)
            self.passwords[role] = path
        key = directory / "qdrant_api_key"
        key.write_text(secrets.token_urlsafe(48))
        key.chmod(0o600)
        self.env["MIMIR_QDRANT_API_KEY_FILE"] = str(key)
        model = json.loads(
            self.docker(
                "compose",
                *MigrationRunner().command[2:],
                "--profile",
                "backend",
                "--profile",
                "migrations",
                "config",
                "--format",
                "json",
            ).stdout
        )
        MigrationPolicy().validate(model)
        MigrationPolicy().validate_files(model)
        # Keep only PostgreSQL/migration jobs; never start the API or providers.
        model["services"] = {
            name: item
            for name, item in model["services"].items()
            if name in {"postgres", "role-bootstrap", "flyway", "role-credentials"}
        }
        model["networks"].pop("api")
        self.volume = model["volumes"]["postgres_data"]["name"]
        model["volumes"] = {"postgres_data": model["volumes"]["postgres_data"]}
        self.migrations = directory / "migrations"
        self.migrations.mkdir()
        for source in (ROOT / "backend/migrations").iterdir():
            if source.suffix == ".sql" and int(source.name[1:4]) > through_version:
                continue
            shutil.copy2(source, self.migrations / source.name)
        for service in model["services"].values():
            service.pop("profiles", None)
            service["labels"] = {"mimir.t03_test": self.token}
        for mount in model["services"]["flyway"]["volumes"]:
            if mount["target"] == "/flyway/sql":
                mount["source"] = str(self.migrations)
        self.file = directory / "test-stack.json"
        self.file.write_text(json.dumps(model))
        self.owns = False

    def docker(
        self, *args: str, input: str | None = None, check: bool = True
    ) -> subprocess.CompletedProcess:
        result = subprocess.run(
            ["docker", *args],
            env=self.env,
            cwd=ROOT,
            input=input,
            capture_output=True,
            text=True,
            timeout=300,
        )
        # Never include credential material from service output in test failures.
        if check and result.returncode:
            raise AssertionError(self.redact(result.stdout + result.stderr))
        return result

    def redact(self, output: str) -> str:
        for path in [*self.passwords.values(), *self.extra_passwords]:
            output = output.replace(path.read_text().strip(), "[REDACTED]")
        return output

    def compose(self, *args: str, **kwargs) -> subprocess.CompletedProcess:
        return self.docker("compose", "-f", str(self.file), *args, **kwargs)

    def sql(
        self,
        statement: str,
        role: str = "postgres",
        check: bool = True,
        database: str = "mimir",
        password: str | None = None,
    ) -> subprocess.CompletedProcess:
        # stdin carries credentials, never host argv/environment or model fields.
        secret_role = "postgres_bootstrap" if role == "postgres" else role
        secret = (
            password
            if password is not None
            else self.passwords[secret_role].read_text().strip()
        )
        command = 'IFS= read -r PGPASSWORD; export PGPASSWORD; exec psql -XAtq -h 127.0.0.1 -U "$1" -d "$2" -v ON_ERROR_STOP=1'
        return self.compose(
            "exec",
            "-T",
            "postgres",
            "sh",
            "-c",
            command,
            "sh",
            role,
            database,
            input=secret + "\n" + statement,
            check=check,
        )

    def start(self) -> None:
        self.docker("info")
        assert not self.docker(
            "ps", "-aq", "--filter", f"label=com.docker.compose.project={self.project}"
        ).stdout.strip(), "test project collision"
        assert (
            self.docker("volume", "inspect", self.volume, check=False).returncode != 0
        ), "test volume collision"
        self.docker(
            "volume", "create", "--label", f"mimir.t03_test={self.token}", self.volume
        )
        details = json.loads(self.docker("volume", "inspect", self.volume).stdout)[0]
        assert details.get("Labels", {}).get("mimir.t03_test") == self.token, (
            "test volume ownership mismatch"
        )
        self.owns = True
        self.compose("up", "-d", "--wait", "postgres")

    def migrate(self) -> None:
        self.compose("run", "--rm", "--no-deps", "-T", "role-bootstrap")
        self.flyway("migrate")
        self.compose("run", "--rm", "--no-deps", "-T", "role-credentials")

    def flyway(self, operation: str, check: bool = True) -> subprocess.CompletedProcess:
        return self.compose(
            "run", "--rm", "--no-deps", "-T", "flyway", operation, check=check
        )

    def credential_variant(self, role: str, password: str, tag: str) -> Path:
        path = self.directory / f"{tag}-password"
        path.write_text(password)
        path.chmod(0o600)
        self.extra_passwords.append(path)
        model = json.loads(self.file.read_text())
        model["secrets"][f"{role}_password"]["file"] = str(path)
        service = "role-bootstrap" if role == "mimir_migrator" else "role-credentials"
        model["services"][service]["environment"] = {"PGAPPNAME": tag}
        file = self.directory / f"{tag}.json"
        file.write_text(json.dumps(model))
        return file

    def start_call(
        self, tag: str, *arguments: str, file: Path | None = None
    ) -> subprocess.Popen:
        name = f"{self.project}-{tag}"
        process = subprocess.Popen(
            [
                "docker",
                "compose",
                "-f",
                str(file or self.file),
                "run",
                "--rm",
                "--no-deps",
                "-T",
                "--name",
                name,
                *arguments,
            ],
            env=self.env,
            cwd=ROOT,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        self.calls.append((process, name))
        return process

    def start_helper(self, role: str, tag: str) -> subprocess.Popen:
        # Actual reviewed helper; no injected SQL or production synchronization hook.
        script = 'export PGAPPNAME="$1"; export PGPASSWORD="$(cat /run/secrets/postgres_bootstrap_password)"; exec psql -XAtq -h postgres -U postgres -d postgres -v ON_ERROR_STOP=1 -v role="$2" -f /mimir/database/provision-password.psql'
        return self.start_call(
            tag,
            "--entrypoint",
            "/bin/sh",
            "role-bootstrap",
            "-c",
            script,
            "sh",
            tag,
            role,
        )

    def credential_digest(self, role: str) -> str:
        # Compare fingerprints so a failing assertion cannot print a SCRAM verifier.
        verifier = self.sql(
            f"SELECT coalesce(rolpassword, '') FROM pg_authid WHERE rolname='{role}'",
            database="postgres",
        ).stdout.strip()
        return hashlib.sha256(verifier.encode()).hexdigest()

    def wait_for_credential_lock(self, tags: list[str], granted: bool) -> None:
        names = ",".join(f"'{tag}'" for tag in tags)
        deadline = time.monotonic() + 20
        while time.monotonic() < deadline:
            count = self.sql(
                "SELECT count(*) FROM pg_locks l JOIN pg_stat_activity a ON a.pid=l.pid "
                "WHERE l.locktype='advisory' AND l.classid=1877347689 AND l.objid=4 "
                f"AND l.objsubid=2 AND l.granted={'true' if granted else 'false'} "
                f"AND a.datname='postgres' AND a.application_name IN ({names})",
                database="postgres",
            ).stdout.strip()
            if count == str(len(tags)):
                return
            time.sleep(0.05)
        raise AssertionError(
            "provisioning callers did not reach the expected database lock state"
        )

    def cleanup(self) -> None:
        for process, name in self.calls:
            if process.poll() is None:
                inspected = self.docker("inspect", name, check=False)
                if (
                    inspected.returncode == 0
                    and json.loads(inspected.stdout)[0]
                    .get("Config", {})
                    .get("Labels", {})
                    .get("mimir.t03_test")
                    == self.token
                ):
                    self.docker("rm", "-f", name)
                try:
                    process.communicate(timeout=10)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.communicate(timeout=10)
        if self.owns:
            self.compose("down", "--remove-orphans", check=False)
            inspected = self.docker("volume", "inspect", self.volume, check=False)
            if (
                inspected.returncode == 0
                and json.loads(inspected.stdout)[0]
                .get("Labels", {})
                .get("mimir.t03_test")
                == self.token
            ):
                self.docker("volume", "rm", self.volume)


@pytest.fixture(scope="module")
def database(tmp_path_factory):
    stack = DatabaseStack(tmp_path_factory.mktemp("database"))
    try:
        stack.start()
        stack.migrate()
        yield stack
    finally:
        stack.cleanup()


@pytest.mark.parametrize("service", ("role-bootstrap", "flyway", "role-credentials"))
def test_optional_jobs_attach_only_to_compose_private_network(
    database: DatabaseStack, service: str
) -> None:
    # A test-only stdin barrier keeps each actual image/configuration available
    # for inspection. Normal entrypoints are exercised by the migration tests.
    tag = f"network-{service}"
    name = f"{database.project}-{tag}"
    process = database.start_call(
        tag, "--entrypoint", "/bin/sh", service, "-c", "cat >/dev/null"
    )
    try:
        deadline = time.monotonic() + 15
        while True:
            result = database.docker("inspect", name, check=False)
            if result.returncode == 0:
                info = json.loads(result.stdout)[0]
                if info["State"]["Running"]:
                    break
            assert time.monotonic() < deadline, "optional network probe did not start"
            time.sleep(0.05)
        model = json.loads(database.file.read_text())
        private = model["networks"]["private"]["name"]
        assert info["Config"]["Labels"]["mimir.t03_test"] == database.token
        assert info["HostConfig"]["NetworkMode"] == private
        assert not info["HostConfig"].get("ExtraHosts")
        assert set(info["NetworkSettings"]["Networks"]) == {private}
        assert not any(info["NetworkSettings"]["Ports"].values())
    finally:
        stdout, stderr = process.communicate(input="", timeout=15)
        assert process.returncode == 0, database.redact(stdout + stderr)


def test_fresh_repeat_validate_and_restart(database: DatabaseStack) -> None:
    assert database.sql("SHOW server_version_num").stdout.startswith("18")
    before = database.sql(
        "SELECT version, checksum, success FROM public.flyway_schema_history WHERE version IS NOT NULL"
    ).stdout
    assert before.startswith("001|") and before.strip().endswith("|t")
    database.migrate()
    database.flyway("validate")
    database.compose("down")
    database.compose("up", "-d", "--wait", "postgres")
    database.flyway("validate")
    assert (
        database.sql(
            "SELECT version, checksum, success FROM public.flyway_schema_history WHERE version IS NOT NULL"
        ).stdout
        == before
    )
    assert database.sql(
        "SELECT nspname || ':' || pg_get_userbyid(nspowner) FROM pg_namespace WHERE nspname IN ('archive','auth','runtime') ORDER BY nspname"
    ).stdout.splitlines() == [
        "archive:mimir_owner",
        "auth:mimir_owner",
        "runtime:mimir_owner",
    ]
    assert (
        database.sql(
            "SELECT count(*) FROM information_schema.tables WHERE table_schema IN ('archive','auth','runtime')"
        ).stdout.strip()
        == "0"
    )
    assert (
        database.sql(
            "SELECT count(*) FROM pg_namespace WHERE nspname='memory'"
        ).stdout.strip()
        == "0"
    )


def test_role_catalog_and_separate_logins(database: DatabaseStack) -> None:
    rows = database.sql(
        "SELECT rolname, rolcanlogin, rolsuper, rolcreatedb, rolcreaterole, rolinherit, rolreplication, rolbypassrls FROM pg_roles WHERE rolname LIKE 'mimir_%' ORDER BY rolname"
    ).stdout.splitlines()
    assert len(rows) == 6
    for row in rows:
        name, login, superuser, createdb, createrole, inherit, replication, bypass = (
            row.split("|")
        )
        assert login == ("f" if name == "mimir_owner" else "t")
        assert (superuser, createdb, inherit, replication, bypass) == ("f",) * 5
        assert createrole == ("t" if name == "mimir_migrator" else "f")
    assert (
        database.sql(
            "SELECT count(*) FROM pg_authid WHERE rolname LIKE 'mimir_%' AND rolpassword LIKE 'SCRAM-SHA-256$%'"
        ).stdout.strip()
        == "5"
    )
    for role in ROLES:
        assert (
            database.sql("SELECT current_user", f"mimir_{role}").stdout.strip()
            == f"mimir_{role}"
        )
    for role in RUNTIME:
        for other in ("postgres", "template1"):
            assert (
                database.sql(
                    "SELECT 1", f"mimir_{role}", check=False, database=other
                ).returncode
                != 0
            )
    # Runtime roles have no memberships, including no implicit owner/migration path.
    assert (
        database.sql(
            "SELECT count(*) FROM pg_auth_members m JOIN pg_roles r ON m.member=r.oid WHERE r.rolname IN ('mimir_api','mimir_worker','mimir_auth','mimir_maintenance')"
        ).stdout.strip()
        == "0"
    )


@pytest.mark.parametrize("role", RUNTIME)
def test_runtime_cannot_ddl_escalate_or_read_history(
    database: DatabaseStack, role: str
) -> None:
    for statement in (
        "CREATE TABLE public.forbidden(id int)",
        "CREATE TABLE archive.forbidden(id int)",
        "CREATE TABLE auth.forbidden(id int)",
        "CREATE TABLE runtime.forbidden(id int)",
        "CREATE TEMP TABLE forbidden(id int)",
        "CREATE SCHEMA forbidden",
        "CREATE ROLE forbidden",
        "SET ROLE mimir_owner",
        "SET ROLE mimir_migrator",
        "ALTER ROLE CURRENT_USER BYPASSRLS",
        "SELECT * FROM public.flyway_schema_history",
    ):
        result = database.sql(statement, f"mimir_{role}", check=False)
        assert result.returncode != 0, f"{role} unexpectedly executed {statement}"
        assert (
            "permission denied" in result.stderr or "must be superuser" in result.stderr
        )


def test_default_grants_and_forced_rls_under_real_logins(
    database: DatabaseStack,
) -> None:
    # Test-only table/function, dropped before returning; no domain schema invented.
    database.sql("""
        SET ROLE mimir_owner;
        CREATE TABLE archive.t03_rls_probe(user_id uuid NOT NULL, value int);
        CREATE FUNCTION archive.t03_private_probe() RETURNS int LANGUAGE sql AS 'SELECT 1';
        ALTER TABLE archive.t03_rls_probe ENABLE ROW LEVEL SECURITY;
        ALTER TABLE archive.t03_rls_probe FORCE ROW LEVEL SECURITY;
        CREATE POLICY tenant ON archive.t03_rls_probe
          USING (user_id = nullif(current_setting('app.current_user_id', true), '')::uuid)
          WITH CHECK (user_id = nullif(current_setting('app.current_user_id', true), '')::uuid);
        GRANT SELECT, INSERT ON archive.t03_rls_probe TO mimir_api, mimir_worker, mimir_auth, mimir_maintenance;
    """)
    try:
        for role in RUNTIME:
            login = f"mimir_{role}"
            assert (
                database.sql(
                    "SELECT has_function_privilege(current_user,'archive.t03_private_probe()','EXECUTE')",
                    login,
                ).stdout.strip()
                == "f"
            )
            assert (
                database.sql(
                    "SELECT count(*) FROM archive.t03_rls_probe", login
                ).stdout.strip()
                == "0"
            )
            assert (
                database.sql(
                    "INSERT INTO archive.t03_rls_probe VALUES ('00000000-0000-0000-0000-000000000001',1)",
                    login,
                    check=False,
                ).returncode
                != 0
            )
            assert (
                database.sql(
                    "SET row_security=off; SELECT * FROM archive.t03_rls_probe",
                    login,
                    check=False,
                ).returncode
                != 0
            )
            forged = database.sql(
                "BEGIN; SET LOCAL app.current_user_id='00000000-0000-0000-0000-000000000001'; INSERT INTO archive.t03_rls_probe VALUES ('00000000-0000-0000-0000-000000000002',1); COMMIT;",
                login,
                check=False,
            )
            assert forged.returncode != 0
            assert "row-level security" in forged.stderr
            # Reuse one session, change actor only transaction-locally, then clear.
            result = database.sql(
                """
                BEGIN;
                SET LOCAL app.current_user_id='00000000-0000-0000-0000-000000000001';
                INSERT INTO archive.t03_rls_probe VALUES ('00000000-0000-0000-0000-000000000001',1);
                SELECT count(*) FROM archive.t03_rls_probe WHERE user_id='00000000-0000-0000-0000-000000000001';
                COMMIT;
                SELECT count(*) FROM archive.t03_rls_probe;
                BEGIN;
                SET LOCAL app.current_user_id='00000000-0000-0000-0000-000000000002';
                SELECT count(*) FROM archive.t03_rls_probe;
                COMMIT;
            """,
                login,
            )
            assert result.stdout.splitlines()[-2:] == ["0", "0"]
            assert int(result.stdout.splitlines()[0]) >= 1
            assert (
                database.sql(
                    "ALTER TABLE archive.t03_rls_probe DISABLE ROW LEVEL SECURITY",
                    login,
                    check=False,
                ).returncode
                != 0
            )
            assert (
                database.sql(
                    "UPDATE archive.t03_rls_probe SET value=3", login, check=False
                ).returncode
                != 0
            )
        assert (
            database.sql(
                "SET ROLE mimir_owner; SELECT count(*) FROM archive.t03_rls_probe"
            ).stdout.strip()
            == "0"
        )
    finally:
        database.sql(
            "SET ROLE mimir_owner; DROP TABLE archive.t03_rls_probe; DROP FUNCTION archive.t03_private_probe()"
        )


def test_concurrent_migration_uses_flyway_lock(database: DatabaseStack) -> None:
    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(database.flyway, "migrate") for _ in range(2)]
        for future in futures:
            assert future.result().returncode == 0
    assert (
        database.sql(
            "SELECT count(*) FROM public.flyway_schema_history WHERE version='001'"
        ).stdout.strip()
        == "1"
    )


def test_checksum_drift_fails_without_repair(database: DatabaseStack) -> None:
    migration = database.migrations / "V001__roles_and_schemas.sql"
    original = migration.read_bytes()
    try:
        migration.write_bytes(original + b"\nSELECT 12345;\n")
        result = database.flyway("validate", check=False)
        assert result.returncode != 0
        assert "checksum" in result.stderr.lower() + result.stdout.lower()
        assert database.flyway("migrate", check=False).returncode != 0
    finally:
        migration.write_bytes(original)
    database.flyway("validate")


def test_incremental_failure_transaction_and_recovery(tmp_path: Path) -> None:
    # A dedicated test-only V002 exercises upgrade; T04 owns the real V002.
    # Destroy the owned test stack afterwards rather than editing successful history.
    database = DatabaseStack(tmp_path)
    try:
        database.start()
        database.migrate()
        probe = database.migrations / "V002__test_only_transaction.sql"
        probe.write_text(
            "SET ROLE mimir_owner; CREATE TABLE runtime.t03_upgrade_probe(id int); SELECT 1/0;"
        )
        assert database.flyway("migrate", check=False).returncode != 0
        assert (
            database.sql(
                "SELECT to_regclass('runtime.t03_upgrade_probe') IS NULL"
            ).stdout.strip()
            == "t"
        )
        assert (
            database.sql(
                "SELECT count(*) FROM public.flyway_schema_history WHERE version='002'"
            ).stdout.strip()
            == "0"
        )
        # Only failed/unapplied SQL is corrected. PostgreSQL rolled back history/DDL.
        probe.write_text(
            "SET ROLE mimir_owner; CREATE TABLE runtime.t03_upgrade_probe(id int); RESET ROLE;"
        )
        database.flyway("migrate")
        database.flyway("validate")
        assert (
            database.sql(
                "SELECT pg_get_userbyid(relowner) FROM pg_class WHERE oid='runtime.t03_upgrade_probe'::regclass"
            ).stdout.strip()
            == "mimir_owner"
        )
        assert database.sql(
            "SELECT version FROM public.flyway_schema_history WHERE version IS NOT NULL ORDER BY installed_rank"
        ).stdout.splitlines() == ["001", "002"]
    finally:
        database.cleanup()


def test_reject_incompatible_role_and_unversioned_schema(tmp_path: Path) -> None:
    database = DatabaseStack(tmp_path)
    try:
        database.start()
        database.sql("CREATE ROLE mimir_migrator SUPERUSER LOGIN", database="postgres")
        result = database.compose(
            "run", "--rm", "--no-deps", "-T", "role-bootstrap", check=False
        )
        assert result.returncode != 0
        assert "incompatible" in result.stderr
        database.sql("DROP ROLE mimir_migrator", database="postgres")
        database.compose("run", "--rm", "--no-deps", "-T", "role-bootstrap")
        database.sql("CREATE TABLE public.t03_unversioned(id int)")
        result = database.flyway("migrate", check=False)
        assert result.returncode != 0
        assert "non-empty" in (result.stdout + result.stderr).lower()
        assert (
            database.sql(
                "SELECT count(*) FROM pg_roles WHERE rolname='mimir_owner'"
            ).stdout.strip()
            == "0"
        )
    finally:
        database.cleanup()


def test_bad_credentials_and_no_silent_rotation(database: DatabaseStack) -> None:
    path = database.passwords["mimir_api"]
    original = path.read_text()
    try:
        path.write_text(secrets.token_urlsafe(48))
        result = database.compose(
            "run", "--rm", "--no-deps", "-T", "role-credentials", check=False
        )
        assert result.returncode != 0
        assert "password authentication failed" in result.stderr
    finally:
        path.write_text(original)
    assert database.sql("SELECT 1", "mimir_api").stdout.strip() == "1"


def test_migrator_wrong_password_and_secret_redaction(database: DatabaseStack) -> None:
    path = database.passwords["mimir_migrator"]
    original = path.read_text()
    try:
        path.write_text(secrets.token_urlsafe(48))
        assert database.flyway("validate", check=False).returncode != 0
    finally:
        path.write_text(original)
    output = database.compose("logs", "--no-color").stdout
    for secret in database.passwords.values():
        assert secret.read_text().strip() not in output
    model = database.file.read_text()
    for secret in database.passwords.values():
        assert secret.read_text().strip() not in model


@contextlib.contextmanager
def credential_lock(database: DatabaseStack):
    """A real controller session makes contention observable rather than probabilistic."""
    tag = f"t03-controller-{uuid.uuid4().hex[:12]}"
    script = 'export PGAPPNAME="$1"; export PGPASSWORD="$(cat /run/secrets/postgres_bootstrap_password)"; exec psql -XAtq -h 127.0.0.1 -U postgres -d postgres -v ON_ERROR_STOP=1'
    controller = subprocess.Popen(
        [
            "docker",
            "compose",
            "-f",
            str(database.file),
            "exec",
            "-T",
            "postgres",
            "sh",
            "-c",
            script,
            "sh",
            tag,
        ],
        env=database.env,
        cwd=ROOT,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    try:
        controller.stdin.write(
            "BEGIN; SELECT pg_advisory_xact_lock(1877347689,4); SELECT 't03-lock-ready';\n"
        )
        controller.stdin.flush()
        output = b""
        deadline = time.monotonic() + 20
        while b"t03-lock-ready" not in output:
            remaining = deadline - time.monotonic()
            assert remaining > 0, "controller lock acquisition timed out"
            readable, _, _ = select.select([controller.stdout], [], [], remaining)
            assert readable, "controller lock acquisition timed out"
            chunk = os.read(controller.stdout.fileno(), 4096)
            assert chunk, "controller exited before acquiring lock"
            output += chunk
        yield
    finally:
        try:
            controller.communicate("COMMIT;\n\\q\n", timeout=10)
        except subprocess.TimeoutExpired, BrokenPipeError:
            database.sql(
                f"SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE application_name='{tag}'",
                database="postgres",
            )
            controller.kill()
            controller.communicate(timeout=10)
        assert controller.returncode == 0, "controller did not release its lock cleanly"


@pytest.fixture(params=("mimir_migrator", "mimir_api"))
def uninitialized_credentials(request, tmp_path: Path):
    database = DatabaseStack(tmp_path)
    try:
        database.start()
        if request.param != "mimir_migrator":
            database.compose("run", "--rm", "--no-deps", "-T", "role-bootstrap")
            database.flyway("migrate")
        yield database, request.param
    finally:
        database.cleanup()


@pytest.mark.parametrize("matching", (False, True), ids=("conflicting", "matching"))
def test_concurrent_credential_initialization(
    uninitialized_credentials, matching: bool
) -> None:
    database, role = uninitialized_credentials
    service = "role-bootstrap" if role == "mimir_migrator" else "role-credentials"
    passwords = [secrets.token_urlsafe(48), secrets.token_urlsafe(48)]
    if matching:
        passwords[1] = passwords[0]
    tags = ["initializer-a", "initializer-b"]
    files = [
        database.credential_variant(role, password, tag)
        for password, tag in zip(passwords, tags, strict=True)
    ]
    with credential_lock(database):
        callers = [
            database.start_call(tag, service, file=file)
            for tag, file in zip(tags, files, strict=True)
        ]
        database.wait_for_credential_lock(tags, granted=False)
        assert all(caller.poll() is None for caller in callers), (
            "initializer bypassed the lock"
        )
        assert (
            database.sql(
                f"SELECT rolpassword IS NULL FROM pg_authid WHERE rolname='{role}'",
                database="postgres",
            ).stdout.strip()
            == "t"
        )
    outputs = [caller.communicate(timeout=60) for caller in callers]
    for stdout, stderr in outputs:
        assert all(password not in stdout + stderr for password in passwords), (
            "plaintext credential leaked"
        )
    successes = [
        index for index, caller in enumerate(callers) if caller.returncode == 0
    ]
    assert len(successes) == (2 if matching else 1), database.redact(str(outputs))
    winner = successes[0]
    digest = database.credential_digest(role)
    assert (
        database.sql("SELECT 1", role, password=passwords[winner]).stdout.strip() == "1"
    )
    for index, password in enumerate(passwords):
        result = database.sql("SELECT 1", role, check=False, password=password)
        assert (result.returncode == 0) == (matching or index == winner)
    retry_index = 1 - winner
    retry = database.start_call("initializer-retry", service, file=files[retry_index])
    stdout, stderr = retry.communicate(timeout=60)
    assert all(password not in stdout + stderr for password in passwords), (
        "plaintext credential leaked"
    )
    assert (retry.returncode == 0) == matching
    assert database.credential_digest(role) == digest
    assert (
        database.sql("SELECT 1", role, password=passwords[winner]).stdout.strip() == "1"
    )
    for file in files:
        assert all(password not in file.read_text() for password in passwords)


def test_credential_lock_timeout_and_recovery(database: DatabaseStack) -> None:
    digest = database.credential_digest("mimir_api")
    password = database.passwords["mimir_api"].read_text().strip()
    with credential_lock(database):
        caller = database.start_helper("mimir_api", "timeout-probe")
        stdout, stderr = caller.communicate(
            password + "\n" + password + "\n", timeout=45
        )
        assert caller.returncode != 0
        assert "lock timeout" in stderr
        assert password not in stdout + stderr
        assert database.credential_digest("mimir_api") == digest
    database.compose("run", "--rm", "--no-deps", "-T", "role-credentials")
    assert database.credential_digest("mimir_api") == digest


def test_missing_credential_role_aborts_and_releases_lock(
    database: DatabaseStack,
) -> None:
    password = secrets.token_urlsafe(48)
    caller = database.start_helper("mimir_t03_absent", "missing-role-probe")
    stdout, stderr = caller.communicate(password + "\n" + password + "\n", timeout=20)
    assert caller.returncode != 0
    assert "no rows returned" in stderr
    assert password not in stdout + stderr
    database.compose("run", "--rm", "--no-deps", "-T", "role-credentials")


def test_interrupted_credential_transaction_rolls_back(database: DatabaseStack) -> None:
    # A disposable role, not a domain migration or normal application privilege.
    role = "mimir_t03_interrupted"
    database.sql(f"CREATE ROLE {role} LOGIN; GRANT CONNECT ON DATABASE mimir TO {role}")
    password = secrets.token_urlsafe(48)
    caller = database.start_helper(role, "interrupted-probe")
    try:
        # Withhold prompt input: the real helper has acquired its lock and checked NULL.
        database.wait_for_credential_lock(["interrupted-probe"], granted=True)
        database.sql(
            "SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE application_name='interrupted-probe'",
            database="postgres",
        )
        stdout, stderr = caller.communicate(
            password + "\n" + password + "\n", timeout=20
        )
        assert caller.returncode != 0
        assert password not in stdout + stderr
        assert (
            database.sql(
                f"SELECT rolpassword IS NULL FROM pg_authid WHERE rolname='{role}'",
                database="postgres",
            ).stdout.strip()
            == "t"
        )
        retry = database.start_helper(role, "interrupted-retry")
        stdout, stderr = retry.communicate(
            password + "\n" + password + "\n", timeout=20
        )
        assert password not in stdout + stderr
        assert retry.returncode == 0, database.redact(stderr)
        assert database.sql("SELECT 1", role, password=password).stdout.strip() == "1"
    finally:
        database.sql(f"REVOKE CONNECT ON DATABASE mimir FROM {role}; DROP ROLE {role}")


# T04 storage tests. No password verification, bearer issuance or HTTP service.
V002 = "V002__users_identities_and_sessions.sql"
PHC = "$argon2id$v=19$m=65536,t=3,p=4$c3ludGhldGljLXN0b3JhZ2Utc2FsdA$c3ludGhldGljLXN0b3JhZ2UtdmVyaWZpZXItMzJieXQ"


class AuthRows:
    """Synthetic storage fixtures; administrative setup is never runtime auth."""

    def __init__(self, database: DatabaseStack) -> None:
        self.database = database
        self.user = str(uuid.uuid7())
        self.identity = str(uuid.uuid7())
        self.username = f"T04_{uuid.uuid4().hex}"
        self.hash = hashlib.sha256(uuid.uuid7().bytes).hexdigest()

    def owned(self, sql: str, check: bool = True) -> subprocess.CompletedProcess:
        return self.database.sql(
            f"BEGIN; SET LOCAL ROLE mimir_owner; "
            f"SET LOCAL app.current_user_id='{self.user}'; {sql}; COMMIT;",
            check=check,
        )

    def seed(self) -> None:
        self.owned(f"""
            INSERT INTO archive.users(user_id,status) VALUES ('{self.user}','active');
            INSERT INTO auth.identities(identity_id,user_id,username,password_hash)
                VALUES ('{self.identity}','{self.user}','{self.username}','{PHC}');
            INSERT INTO auth.sessions(token_hash,user_id,identity_id,client_type,
                expires_at,absolute_expires_at)
                VALUES (decode('{self.hash}','hex'),'{self.user}','{self.identity}',
                    'cli',CURRENT_TIMESTAMP + interval '30 days',
                    CURRENT_TIMESTAMP + interval '90 days')
        """)

    def update_session(self, assignments: str, check: bool = True):
        return self.owned(
            f"UPDATE auth.sessions SET {assignments} "
            f"WHERE token_hash=decode('{self.hash}','hex')",
            check=check,
        )


@pytest.fixture(scope="module")
def auth_database(tmp_path_factory):
    stack = DatabaseStack(tmp_path_factory.mktemp("auth-database"), through_version=2)
    try:
        stack.start()
        stack.migrate()
        yield stack
    finally:
        stack.cleanup()


@pytest.fixture
def auth_rows(auth_database):
    rows = AuthRows(auth_database)
    rows.seed()
    return rows


def test_t04_fresh_repeat_validate_catalog_and_restart(auth_database):
    database = auth_database
    assert database.sql("SHOW server_version_num").stdout.startswith("18")
    history = database.sql(
        "SELECT version,checksum,success FROM public.flyway_schema_history "
        "WHERE version IS NOT NULL ORDER BY installed_rank"
    ).stdout
    assert [line.split("|")[0] for line in history.splitlines()] == ["001", "002"]
    assert all(line.endswith("|t") for line in history.splitlines())
    database.migrate()
    database.flyway("validate")
    database.compose("down")
    database.compose("up", "-d", "--wait", "postgres")
    database.flyway("validate")
    assert (
        database.sql(
            "SELECT version,checksum,success FROM public.flyway_schema_history "
            "WHERE version IS NOT NULL ORDER BY installed_rank"
        ).stdout
        == history
    )
    assert database.sql(
        "SELECT n.nspname,c.relname,pg_get_userbyid(c.relowner),"
        "c.relrowsecurity,c.relforcerowsecurity FROM pg_class c "
        "JOIN pg_namespace n ON n.oid=c.relnamespace "
        "WHERE n.nspname IN ('archive','auth','runtime') AND c.relkind='r' "
        "ORDER BY n.nspname,c.relname"
    ).stdout.splitlines() == [
        "archive|users|mimir_owner|t|t",
        "auth|identities|mimir_owner|t|t",
        "auth|sessions|mimir_owner|t|t",
    ]
    assert (
        database.sql(
            "SELECT count(*) FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace "
            "WHERE n.nspname IN ('archive','auth') AND "
            "(pg_get_userbyid(p.proowner)<>'mimir_owner' OR "
            "EXISTS (SELECT FROM aclexplode(coalesce(p.proacl,acldefault('f',p.proowner))) a "
            "WHERE a.grantee=0 AND a.privilege_type='EXECUTE'))"
        ).stdout.strip()
        == "0"
    )
    assert database.sql(
        "SELECT p.proname,p.prosecdef,array_to_string(p.proconfig,';') "
        "FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace "
        "WHERE n.nspname='auth' AND p.proname LIKE 'lookup_%' ORDER BY p.proname"
    ).stdout.splitlines() == [
        "lookup_identity|t|search_path=pg_catalog, pg_temp;row_security=on",
        "lookup_session|t|search_path=pg_catalog, pg_temp;row_security=on",
    ]
    assert (
        database.sql(
            "SELECT count(*) FROM pg_roles WHERE rolname LIKE 'mimir_%' AND rolbypassrls"
        ).stdout.strip()
        == "0"
    )


def test_t04_upgrade_rollback_recovery_and_drift(tmp_path):
    database = DatabaseStack(tmp_path)
    try:
        database.start()
        database.migrate()
        v001_history = database.sql(
            "SELECT checksum FROM public.flyway_schema_history WHERE version='001'"
        ).stdout
        candidate = database.migrations / V002
        original = (ROOT / "backend/migrations" / V002).read_bytes()
        candidate.write_bytes(original + b"\nSELECT 1/0;\n")
        assert database.flyway("migrate", check=False).returncode != 0
        assert (
            database.sql(
                "SELECT to_regclass('archive.users') IS NULL AND "
                "to_regclass('auth.identities') IS NULL AND "
                "to_regclass('auth.sessions') IS NULL AND "
                "to_regprocedure('auth.lookup_identity(text)') IS NULL AND "
                "to_regprocedure('auth.lookup_session(bytea)') IS NULL"
            ).stdout.strip()
            == "t"
        )
        assert (
            database.sql(
                "SELECT count(*) FROM public.flyway_schema_history WHERE version='002'"
            ).stdout.strip()
            == "0"
        )
        # Correct only the failed/unapplied candidate, then upgrade with real V002.
        candidate.write_bytes(original)
        database.flyway("migrate")
        database.flyway("validate")
        rows = AuthRows(database)
        rows.seed()
        database.migrate()
        assert (
            database.sql(
                f"SELECT count(*) FROM archive.users WHERE user_id='{rows.user}'"
            ).stdout.strip()
            == "1"
        )
        assert (
            database.sql(
                "SELECT checksum FROM public.flyway_schema_history WHERE version='001'"
            ).stdout
            == v001_history
        )
        try:
            candidate.write_bytes(original + b"\nSELECT 12345;\n")
            failed = database.flyway("validate", check=False)
            assert failed.returncode != 0
            assert "checksum" in (failed.stdout + failed.stderr).lower()
            assert database.flyway("migrate", check=False).returncode != 0
        finally:
            candidate.write_bytes(original)
        database.flyway("validate")
    finally:
        database.cleanup()


def test_t04_exact_login_and_lookup_boundaries(auth_rows):
    rows = auth_rows
    database = rows.database
    lower = rows.username.lower()
    other_identity = str(uuid.uuid7())
    rows.owned(
        "INSERT INTO auth.identities(identity_id,user_id,username,password_hash) "
        f"VALUES ('{other_identity}','{rows.user}','{lower}','{PHC}')"
    )
    for name, identity in ((rows.username, rows.identity), (lower, other_identity)):
        result = database.sql(
            f"SELECT user_id,identity_id,account_status "
            f"FROM auth.lookup_identity('{name}')",
            "mimir_auth",
        )
        assert result.stdout.strip() == f"{rows.user}|{identity}|active"
    assert (
        database.sql(
            f"SELECT password_hash='{PHC}' FROM auth.lookup_identity('{rows.username}')",
            "mimir_auth",
        ).stdout.strip()
        == "t"
    )
    duplicate = rows.owned(
        "INSERT INTO auth.identities(identity_id,user_id,username,password_hash) "
        f"VALUES ('{uuid.uuid7()}','{rows.user}','{rows.username}','{PHC}')",
        check=False,
    )
    assert duplicate.returncode != 0 and "unique constraint" in duplicate.stderr
    for value in ("'no-such-login'", "NULL", "''' OR true --'"):
        assert (
            database.sql(
                f"SELECT count(*) FROM auth.lookup_identity({value})", "mimir_auth"
            ).stdout.strip()
            == "0"
        )
    for value in ("NULL", "decode('00','hex')", "decode(repeat('00',32),'hex')"):
        assert (
            database.sql(
                f"SELECT count(*) FROM auth.lookup_session({value})", "mimir_auth"
            ).stdout.strip()
            == "0"
        )
    assert (
        database.sql(
            f"SELECT user_id,identity_id,client_type,account_status "
            f"FROM auth.lookup_session(decode('{rows.hash}','hex'))",
            "mimir_auth",
        ).stdout.strip()
        == f"{rows.user}|{rows.identity}|cli|active"
    )


@pytest.mark.parametrize("role", RUNTIME)
def test_t04_real_runtime_privileges(auth_rows, role):
    database = auth_rows.database
    login = f"mimir_{role}"
    for table in ("auth.identities", "auth.sessions"):
        for context in ("", f"SET app.current_user_id='{auth_rows.user}'; "):
            denied = database.sql(
                context + f"SELECT * FROM {table}", login, check=False
            )
            assert denied.returncode != 0 and "permission denied" in denied.stderr
    for call in (
        f"auth.lookup_identity('{auth_rows.username}')",
        f"auth.lookup_session(decode('{auth_rows.hash}','hex'))",
    ):
        result = database.sql(f"SELECT count(*) FROM {call}", login, check=False)
        assert (result.returncode == 0) == (role == "auth")
    for statement in (
        "SET ROLE mimir_owner",
        "SET ROLE mimir_migrator",
        "ALTER TABLE archive.users DISABLE ROW LEVEL SECURITY",
        "CREATE TABLE archive.t04_forbidden(id int)",
        "CREATE TEMP TABLE t04_forbidden(id int)",
        "SELECT * FROM public.flyway_schema_history",
        "UPDATE archive.users SET status='disabled'",
        "DELETE FROM archive.users",
    ):
        assert database.sql(statement, login, check=False).returncode != 0


def test_t04_lookup_cannot_set_actor_or_be_redirected(auth_rows):
    rows = auth_rows
    other = str(uuid.uuid7())
    result = rows.database.sql(
        f"BEGIN; SET LOCAL app.current_user_id='{other}'; "
        "SET LOCAL search_path=public,pg_temp; SET LOCAL row_security=off; "
        f"SELECT user_id FROM auth.lookup_identity('{rows.username}'); "
        f"SELECT user_id FROM auth.lookup_session(decode('{rows.hash}','hex')); "
        "SELECT current_setting('app.current_user_id'); COMMIT; "
        "SELECT nullif(current_setting('app.current_user_id',true),'') IS NULL; "
        "BEGIN; "
        f"SELECT user_id FROM auth.lookup_identity('{rows.username}'); "
        "SELECT nullif(current_setting('app.current_user_id',true),'') IS NULL; ROLLBACK",
        "mimir_auth",
    )
    assert result.stdout.splitlines() == [
        rows.user,
        rows.user,
        other,
        "t",
        rows.user,
        "t",
    ]


@pytest.mark.parametrize("role", ("api", "worker"))
@pytest.mark.parametrize("end", ("COMMIT", "ROLLBACK"))
def test_t04_actual_tenant_rls_and_connection_reuse(auth_rows, role, end):
    rows = auth_rows
    other = AuthRows(rows.database)
    other.seed()
    result = rows.database.sql(
        "SELECT count(*) FROM archive.users; BEGIN; "
        f"SET LOCAL app.current_user_id='{rows.user}'; "
        "SELECT user_id FROM archive.users; "
        f"SELECT count(*) FROM archive.users WHERE user_id='{other.user}'; "
        f"{end}; SELECT count(*) FROM archive.users; BEGIN; "
        f"SET LOCAL app.current_user_id='{other.user}'; "
        "SELECT user_id FROM archive.users; COMMIT; "
        "SELECT count(*) FROM archive.users;",
        f"mimir_{role}",
    )
    assert result.stdout.splitlines() == ["0", rows.user, "0", "0", other.user, "0"]
    off = rows.database.sql(
        "SET row_security=off; SELECT * FROM archive.users",
        f"mimir_{role}",
        check=False,
    )
    assert off.returncode != 0 and "row-level security" in off.stderr
    malformed = rows.database.sql(
        "SET app.current_user_id='invalid-uuid'; SELECT * FROM archive.users",
        f"mimir_{role}",
        check=False,
    )
    assert malformed.returncode != 0
    if role == "api":
        result = rows.database.sql(
            f"BEGIN; SET LOCAL app.current_user_id='{rows.user}'; "
            "UPDATE archive.users SET display_name='synthetic' RETURNING user_id; "
            f"UPDATE archive.users SET display_name='forged' WHERE user_id='{other.user}' "
            "RETURNING user_id; COMMIT",
            "mimir_api",
        )
        assert result.stdout.strip() == rows.user


def test_t04_cross_owner_foreign_keys_and_stable_identity(auth_rows):
    rows = auth_rows
    other = AuthRows(rows.database)
    other.seed()
    for identity in (other.identity, str(uuid.uuid7())):
        result = rows.owned(
            "INSERT INTO auth.sessions(token_hash,user_id,identity_id,client_type,"
            "expires_at,absolute_expires_at) VALUES "
            f"(decode('{hashlib.sha256(identity.encode()).hexdigest()}','hex'),"
            f"'{rows.user}','{identity}','cli',now()+interval '30 days',"
            "now()+interval '90 days')",
            check=False,
        )
        assert result.returncode != 0 and "foreign key constraint" in result.stderr
    invalid_user = rows.database.sql(
        "INSERT INTO auth.identities(identity_id,user_id,username,password_hash) "
        f"VALUES ('{uuid.uuid7()}','{uuid.uuid7()}','absent_{uuid.uuid4().hex}','{PHC}')",
        check=False,
    )
    assert (
        invalid_user.returncode != 0 and "foreign key constraint" in invalid_user.stderr
    )
    forged = rows.owned(
        f"INSERT INTO archive.users(user_id,status) VALUES ('{uuid.uuid7()}','active')",
        check=False,
    )
    assert forged.returncode != 0 and "row-level security" in forged.stderr
    for table, predicate, assignments in (
        ("archive.users", f"user_id='{rows.user}'", f"user_id='{uuid.uuid7()}'"),
        (
            "archive.users",
            f"user_id='{rows.user}'",
            "created_at=created_at-interval '1 day'",
        ),
        (
            "auth.identities",
            f"identity_id='{rows.identity}'",
            f"user_id='{other.user}'",
        ),
        (
            "auth.identities",
            f"identity_id='{rows.identity}'",
            f"identity_id='{uuid.uuid7()}'",
        ),
    ):
        rejected = rows.database.sql(
            f"UPDATE {table} SET {assignments} WHERE {predicate}", check=False
        )
        assert rejected.returncode != 0 and "immutable" in rejected.stderr
    assert (
        rows.owned(
            f"DELETE FROM archive.users WHERE user_id='{rows.user}'", check=False
        ).returncode
        != 0
    )


@pytest.mark.parametrize(
    "assignments",
    [
        "absolute_expires_at=absolute_expires_at+interval '1 day'",
        "created_at=created_at-interval '1 day'",
        "token_hash=decode(repeat('ff',32),'hex')",
        "user_id=uuidv7()",
        "identity_id=uuidv7()",
        "client_type='web'",
        "expires_at=absolute_expires_at+interval '1 second'",
        "expires_at=expires_at-interval '1 second'",
        "last_used_at=last_used_at-interval '1 second'",
        "last_used_at=expires_at",
        "revoked_at=created_at-interval '1 second'",
        "expires_at='infinity'",
    ],
)
def test_t04_reject_session_boundary_updates(auth_rows, assignments):
    assert auth_rows.update_session(assignments, check=False).returncode != 0


@pytest.mark.parametrize("state", ("revoked", "idle_expired", "absolute_expired"))
def test_t04_terminal_session_never_reactivates(auth_rows, state):
    rows = auth_rows
    if state == "revoked":
        rows.update_session("revoked_at=clock_timestamp()")
        assert rows.update_session("revoked_at=NULL", check=False).returncode != 0
        assert (
            rows.update_session(
                "revoked_at=revoked_at+interval '1 second'", check=False
            ).returncode
            != 0
        )
    else:
        # Insert historical expired state directly, rather than wait on the clock
        # or alter an already issued session's immutable absolute expiration.
        rows.hash = hashlib.sha256(uuid.uuid7().bytes).hexdigest()
        absolute = "1 day" if state == "idle_expired" else "-1 day"
        rows.owned(
            "INSERT INTO auth.sessions(token_hash,user_id,identity_id,client_type,"
            "created_at,last_used_at,expires_at,absolute_expires_at) VALUES "
            f"(decode('{rows.hash}','hex'),'{rows.user}','{rows.identity}','cli',"
            "now()-interval '40 days',now()-interval '35 days',"
            f"now()-interval '2 days',now()+interval '{absolute}')"
        )
    rejected = rows.update_session(
        "expires_at=least(clock_timestamp()+interval '30 days',absolute_expires_at),"
        "last_used_at=clock_timestamp()",
        check=False,
    )
    assert rejected.returncode != 0 and "cannot be renewed" in rejected.stderr
    # Lookups expose state for T08's decision; they never authenticate it.
    assert (
        rows.database.sql(
            f"SELECT count(*) FROM auth.lookup_session(decode('{rows.hash}','hex'))",
            "mimir_auth",
        ).stdout.strip()
        == "1"
    )


def test_t04_session_ceiling_and_account_status(auth_rows):
    rows = auth_rows
    rows.update_session("expires_at=absolute_expires_at,last_used_at=clock_timestamp()")
    assert (
        rows.database.sql(
            f"SELECT expires_at=absolute_expires_at FROM auth.lookup_session("
            f"decode('{rows.hash}','hex'))",
            "mimir_auth",
        ).stdout.strip()
        == "t"
    )
    rows.owned(
        f"UPDATE archive.users SET status='disabled' WHERE user_id='{rows.user}'"
    )
    for lookup in (
        f"auth.lookup_identity('{rows.username}')",
        f"auth.lookup_session(decode('{rows.hash}','hex'))",
    ):
        assert (
            rows.database.sql(
                f"SELECT account_status FROM {lookup}", "mimir_auth"
            ).stdout.strip()
            == "disabled"
        )


@pytest.mark.parametrize("bad_hash", ("plaintext", "$argon2i$v=19$m=1,t=1,p=1$a$b", ""))
def test_t04_password_storage_rejects_non_argon2id(auth_rows, bad_hash):
    rows = auth_rows
    result = rows.owned(
        f"UPDATE auth.identities SET password_hash='{bad_hash}' "
        f"WHERE identity_id='{rows.identity}'",
        check=False,
    )
    assert result.returncode != 0 and "check constraint" in result.stderr


@pytest.mark.parametrize("size", (0, 31, 33))
def test_t04_session_hash_storage_is_sha256_sized(auth_rows, size):
    rows = auth_rows
    result = rows.owned(
        "INSERT INTO auth.sessions(token_hash,user_id,identity_id,client_type,"
        "expires_at,absolute_expires_at) VALUES "
        f"(decode(repeat('00',{size}),'hex'),'{rows.user}','{rows.identity}','cli',"
        "now()+interval '30 days',now()+interval '90 days')",
        check=False,
    )
    assert result.returncode != 0 and "check constraint" in result.stderr


@pytest.mark.parametrize(
    ("column", "value"),
    [
        ("expires_at", "CURRENT_TIMESTAMP"),
        ("absolute_expires_at", "CURRENT_TIMESTAMP"),
        ("absolute_expires_at", "'infinity'"),
        ("absolute_expires_at", "NULL"),
        ("created_at", "CURRENT_TIMESTAMP + interval '1 day'"),
        ("last_used_at", "CURRENT_TIMESTAMP + interval '31 days'"),
        ("client_type", "'unknown'"),
    ],
)
def test_t04_invalid_issued_session_rejected(auth_rows, column, value):
    rows = auth_rows
    fields = {
        "token_hash": f"decode('{hashlib.sha256(uuid.uuid7().bytes).hexdigest()}','hex')",
        "user_id": f"'{rows.user}'",
        "identity_id": f"'{rows.identity}'",
        "client_type": "'cli'",
        "created_at": "CURRENT_TIMESTAMP",
        "last_used_at": "CURRENT_TIMESTAMP",
        "expires_at": "CURRENT_TIMESTAMP + interval '30 days'",
        "absolute_expires_at": "CURRENT_TIMESTAMP + interval '90 days'",
    }
    fields[column] = value
    result = rows.owned(
        f"INSERT INTO auth.sessions({','.join(fields)}) "
        f"VALUES ({','.join(fields.values())})",
        check=False,
    )
    assert result.returncode != 0 and "constraint" in result.stderr


def test_t04_revocation_wins_over_concurrent_renewal(auth_rows):
    rows = auth_rows
    barrier = threading.Barrier(2)

    def update(assignments):
        barrier.wait(timeout=15)
        return rows.update_session(assignments, check=False)

    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
        revoked = pool.submit(update, "revoked_at=clock_timestamp()")
        renewed = pool.submit(
            update,
            "expires_at=created_at+interval '60 days',last_used_at=clock_timestamp()",
        )
        assert revoked.result().returncode == 0
        result = renewed.result()
        assert result.returncode == 0 or "cannot be renewed" in result.stderr
    assert (
        rows.database.sql(
            f"SELECT revoked_at IS NOT NULL FROM auth.lookup_session("
            f"decode('{rows.hash}','hex'))",
            "mimir_auth",
        ).stdout.strip()
        == "t"
    )
    assert (
        rows.update_session("last_used_at=clock_timestamp()", check=False).returncode
        != 0
    )


def test_t04_concurrent_login_uniqueness_and_session_updates(auth_rows):
    rows = auth_rows
    barrier = threading.Barrier(2)
    username = "race_" + uuid.uuid4().hex

    def insert():
        barrier.wait(timeout=15)
        return rows.owned(
            "INSERT INTO auth.identities(identity_id,user_id,username,password_hash) "
            f"VALUES ('{uuid.uuid7()}','{rows.user}','{username}','{PHC}')",
            check=False,
        )

    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
        results = [
            future.result() for future in [pool.submit(insert) for _ in range(2)]
        ]
    assert sorted(result.returncode == 0 for result in results) == [False, True]
    assert all(
        "unique constraint" in result.stderr for result in results if result.returncode
    )
    assert (
        rows.database.sql(
            f"SELECT count(*) FROM auth.lookup_identity('{username}')", "mimir_auth"
        ).stdout.strip()
        == "1"
    )

    def renew(days):
        barrier.wait(timeout=15)
        return rows.update_session(
            f"expires_at=greatest(expires_at,created_at+interval '{days} days'),"
            "last_used_at=greatest(last_used_at,clock_timestamp())"
        )

    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(renew, days) for days in (45, 60)]
        assert all(future.result().returncode == 0 for future in futures)
    assert (
        rows.database.sql(
            "SELECT count(*),bool_and(expires_at=created_at+interval '60 days' "
            "AND absolute_expires_at=created_at+interval '90 days') FROM auth.sessions "
            f"WHERE token_hash=decode('{rows.hash}','hex')"
        ).stdout.strip()
        == "1|t"
    )


# T05 archive-local SQL guarantees. No runtime, branch, submission or HTTP code.
V003 = "V003__projects_conversations_and_messages.sql"
ARCHIVE_TABLES = ("projects", "conversations", "messages", "message_revisions")


class ArchiveRows:
    """Synthetic owners/content; actors here model already-authenticated code."""

    def __init__(self, database):
        self.database = database
        self.user = str(uuid.uuid7())
        self.project = str(uuid.uuid7())
        self.conversation = str(uuid.uuid7())
        self.message = str(uuid.uuid7())
        self.revision = str(uuid.uuid7())

    def actor(self, sql, role="api", check=True):
        return self.database.sql(
            f"BEGIN; SET LOCAL app.current_user_id='{self.user}'; {sql}; COMMIT;",
            f"mimir_{role}",
            check=check,
        )

    def owner(self, sql, check=True):
        return self.database.sql(
            f"BEGIN; SET LOCAL ROLE mimir_owner; "
            f"SET LOCAL app.current_user_id='{self.user}'; {sql}; COMMIT;",
            check=check,
        )

    def message_sql(
        self,
        ordinal=2,
        message=None,
        revision=None,
        pointer_first=False,
        role="user",
        text="'synthetic content'",
        revision_number=1,
        status_sql="DEFAULT",
    ):
        message = message or str(uuid.uuid7())
        revision = revision or str(uuid.uuid7())
        pointer = f"'{revision}'" if pointer_first else "NULL"
        return f"""
            INSERT INTO archive.messages(message_id,user_id,conversation_id,ordinal,
                role,status,current_revision_id) VALUES ('{message}','{self.user}',
                    '{self.conversation}',{ordinal},'{role}',{status_sql},{pointer});
            INSERT INTO archive.message_revisions(revision_id,user_id,message_id,
                revision_number,content_text) VALUES ('{revision}','{self.user}',
                    '{message}',{revision_number},{text});
            UPDATE archive.messages SET current_revision_id='{revision}'
                WHERE message_id='{message}'
        """

    def seed(self):
        self.owner(
            f"INSERT INTO archive.users(user_id,status) VALUES ('{self.user}','active')"
        )
        self.actor(f"""
            INSERT INTO archive.projects(project_id,user_id,name,instructions)
                VALUES ('{self.project}','{self.user}','synthetic project','synthetic instructions');
            INSERT INTO archive.conversations(conversation_id,user_id,project_id,title)
                VALUES ('{self.conversation}','{self.user}','{self.project}','synthetic conversation');
            {self.message_sql(1, self.message, self.revision)}
        """)

    def incomplete_sql(self, message=None):
        return f"""
            INSERT INTO archive.messages(message_id,user_id,conversation_id,ordinal,role)
                VALUES ('{message or uuid.uuid7()}','{self.user}','{self.conversation}',2,'user')
        """


@pytest.fixture(scope="module")
def archive_database(tmp_path_factory):
    stack = DatabaseStack(
        tmp_path_factory.mktemp("archive-database"), through_version=3
    )
    try:
        stack.start()
        stack.migrate()
        yield stack
    finally:
        stack.cleanup()


@pytest.fixture
def archive_rows(archive_database):
    rows = ArchiveRows(archive_database)
    rows.seed()
    return rows


def test_t05_fresh_repeat_validate_restart_and_catalog(archive_rows):
    rows = archive_rows
    database = rows.database
    assert database.sql("SHOW server_version_num").stdout.startswith("18")
    query = "SELECT version,checksum,success FROM public.flyway_schema_history ORDER BY installed_rank"
    history = database.sql(query).stdout
    assert [line.split("|")[0] for line in history.splitlines()] == [
        "001",
        "002",
        "003",
    ]
    database.migrate()
    database.flyway("validate")
    database.compose("restart", "postgres")
    database.compose("up", "-d", "--wait", "postgres")
    database.flyway("validate")
    assert database.sql(query).stdout == history
    assert (
        rows.actor(
            f"SELECT current_revision_id FROM archive.messages WHERE message_id='{rows.message}'"
        ).stdout.strip()
        == rows.revision
    )
    assert database.sql("""
        SELECT c.relname,pg_get_userbyid(c.relowner),c.relrowsecurity,c.relforcerowsecurity
        FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace
        WHERE n.nspname='archive' AND c.relname IN
            ('projects','conversations','messages','message_revisions') ORDER BY c.relname
    """).stdout.splitlines() == [
        f"{table}|mimir_owner|t|t" for table in sorted(ARCHIVE_TABLES)
    ]
    assert (
        database.sql("""
        SELECT conname,condeferrable,condeferred FROM pg_constraint
        WHERE conname='message_current_revision_fk'
    """).stdout.strip()
        == "message_current_revision_fk|t|t"
    )
    assert (
        database.sql("""
        SELECT tgdeferrable,tginitdeferred FROM pg_trigger
        WHERE tgname='message_revision_required'
    """).stdout.strip()
        == "t|t"
    )
    assert (
        database.sql("""
        SELECT count(*) FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace
        WHERE n.nspname='archive' AND (pg_get_userbyid(p.proowner)<>'mimir_owner' OR
            EXISTS (SELECT FROM aclexplode(coalesce(p.proacl,acldefault('f',p.proowner))) a
            WHERE a.grantee=0 AND a.privilege_type='EXECUTE'))
    """).stdout.strip()
        == "0"
    )


def test_t05_upgrade_failure_rollback_recovery_and_drift(tmp_path):
    database = DatabaseStack(tmp_path, through_version=2)
    try:
        database.start()
        database.migrate()
        rows = AuthRows(database)
        rows.seed()
        history = database.sql(
            "SELECT version,checksum FROM public.flyway_schema_history ORDER BY installed_rank"
        ).stdout
        candidate = database.migrations / V003
        original = (ROOT / "backend/migrations" / V003).read_bytes()
        candidate.write_bytes(original + b"\nSELECT 1/0;\n")
        assert database.flyway("migrate", check=False).returncode != 0
        assert (
            database.sql(
                "SELECT count(*) FROM public.flyway_schema_history WHERE version='003'"
            ).stdout.strip()
            == "0"
        )
        for table in ARCHIVE_TABLES:
            assert (
                database.sql(
                    f"SELECT to_regclass('archive.{table}') IS NULL"
                ).stdout.strip()
                == "t"
            )
        assert (
            database.sql(
                "SELECT to_regprocedure('archive.require_message_revision()') IS NULL"
            ).stdout.strip()
            == "t"
        )
        assert (
            database.sql(
                "SELECT version,checksum FROM public.flyway_schema_history ORDER BY installed_rank"
            ).stdout
            == history
        )
        candidate.write_bytes(original)
        database.flyway("migrate")
        database.flyway("validate")
        assert (
            database.sql(
                f"SELECT count(*) FROM archive.users WHERE user_id='{rows.user}'"
            ).stdout.strip()
            == "1"
        )
        assert (
            database.sql(
                "SELECT version,checksum FROM public.flyway_schema_history WHERE version<>'003' ORDER BY installed_rank"
            ).stdout
            == history
        )
        archive = ArchiveRows(database)
        archive.seed()
        try:
            candidate.write_bytes(original + b"\nSELECT 12345;\n")
            failed = database.flyway("validate", check=False)
            assert (
                failed.returncode != 0
                and "checksum" in (failed.stdout + failed.stderr).lower()
            )
            assert database.flyway("migrate", check=False).returncode != 0
        finally:
            candidate.write_bytes(original)
        database.flyway("validate")
    finally:
        database.cleanup()


@pytest.mark.parametrize("role", ("api", "worker"))
@pytest.mark.parametrize("pointer_first", (False, True))
def test_t05_atomic_creation_and_consecutive_user_messages(
    archive_rows, role, pointer_first
):
    rows = archive_rows
    rows.actor(rows.message_sql(pointer_first=pointer_first), role)
    assert rows.actor(
        f"""
        SELECT m.ordinal,m.role,m.status,r.revision_number,r.content_text,
            r.content_hash=sha256(convert_to(r.content_text,'UTF8'))
        FROM archive.messages m JOIN archive.message_revisions r
            ON (r.user_id,r.message_id,r.revision_id)=(m.user_id,m.message_id,m.current_revision_id)
        WHERE m.conversation_id='{rows.conversation}' ORDER BY m.ordinal
    """,
        role,
    ).stdout.splitlines() == [
        f"{ordinal}|user|completed|1|synthetic content|t" for ordinal in (1, 2)
    ]


@pytest.mark.parametrize(
    "context",
    (
        "",
        "SET LOCAL app.current_user_id='';",
        f"SET LOCAL app.current_user_id='{uuid.uuid7()}';",
    ),
)
def test_t05_commit_guard_survives_actor_context_changes(archive_rows, context):
    rows = archive_rows
    message = str(uuid.uuid7())
    failed = rows.actor(rows.incomplete_sql(message) + ";" + context, check=False)
    assert failed.returncode != 0 and "selected revision at commit" in failed.stderr
    assert (
        rows.actor(
            f"SELECT count(*) FROM archive.messages WHERE message_id='{message}'"
        ).stdout.strip()
        == "0"
    )
    rows.actor(rows.message_sql(message=message))


@pytest.mark.parametrize(
    "status", ("pending", "failed", "cancelled", "interrupted", "NULL")
)
def test_t05_unsupported_message_states_rejected(archive_rows, status):
    rows = archive_rows
    message = str(uuid.uuid7())
    revision = str(uuid.uuid7())
    value = status if status == "NULL" else f"'{status}'"
    # The pair is otherwise valid: removing the status CHECK must let the four
    # non-null invalid states commit, making this regression test fail.
    failed = rows.actor(
        rows.message_sql(message=message, revision=revision, status_sql=value),
        check=False,
    )
    assert failed.returncode != 0
    if status == "NULL":
        expected = 'null value in column "status" of relation "messages" violates not-null constraint'
    else:
        expected = 'violates check constraint "messages_status_check"'
    assert expected in failed.stderr
    assert (
        rows.actor(
            f"SELECT count(*) FROM archive.messages WHERE message_id='{message}'"
        ).stdout.strip()
        == "0"
    )
    assert (
        rows.actor(
            f"SELECT count(*) FROM archive.message_revisions WHERE revision_id='{revision}'"
        ).stdout.strip()
        == "0"
    )
    # Reusing the exact identities/ordinal with completed status is a positive
    # control and confirms the rejected transaction left no conflicting rows.
    rows.actor(
        rows.message_sql(message=message, revision=revision, status_sql="'completed'")
    )


@pytest.mark.parametrize("foreign_owner", (False, True))
def test_t05_selected_revision_must_match_message_and_owner(
    archive_rows, foreign_owner
):
    rows = archive_rows
    target = ArchiveRows(rows.database) if foreign_owner else rows
    if foreign_owner:
        target.seed()
    failed = rows.actor(
        rows.incomplete_sql()
        .replace("ordinal,role)", "ordinal,role,current_revision_id)")
        .replace("2,'user')", f"2,'user','{target.revision}')"),
        check=False,
    )
    assert failed.returncode != 0 and "message_current_revision_fk" in failed.stderr


@pytest.mark.parametrize(
    "assignment",
    (
        "current_revision_id=NULL",
        "current_revision_id='{replacement}'",
        "message_id='{replacement}'",
        "user_id='{replacement}'",
        "conversation_id='{replacement}'",
        "role='assistant'",
        "ordinal=2",
        "created_at=created_at+interval '1 second'",
    ),
)
def test_t05_committed_message_fields_are_immutable_even_for_owner(
    archive_rows, assignment
):
    rows = archive_rows
    assignment = assignment.format(replacement=uuid.uuid7())
    failed = rows.owner(
        f"UPDATE archive.messages SET {assignment} WHERE message_id='{rows.message}'",
        check=False,
    )
    assert failed.returncode != 0 and "immutable" in failed.stderr


def test_t05_pointer_cannot_switch_to_another_valid_revision(archive_rows):
    rows = archive_rows
    revision = str(uuid.uuid7())
    sql = f"""
        INSERT INTO archive.message_revisions(revision_id,user_id,message_id,revision_number,content_text)
            VALUES ('{revision}','{rows.user}','{rows.message}',2,'synthetic alternative');
        UPDATE archive.messages SET current_revision_id='{revision}' WHERE message_id='{rows.message}'
    """
    failed = rows.actor(sql, check=False)
    assert failed.returncode != 0 and "immutable" in failed.stderr
    assert (
        rows.actor(
            f"SELECT count(*) FROM archive.message_revisions WHERE message_id='{rows.message}'"
        ).stdout.strip()
        == "1"
    )


@pytest.mark.parametrize(
    "assignment",
    (
        "content_text='changed'",
        "content_format='text/v1'",
        "revision_number=2",
        "message_id='{replacement}'",
        "user_id='{replacement}'",
        "revision_id='{replacement}'",
        "created_at=created_at+interval '1 second'",
    ),
)
def test_t05_revision_updates_rejected_even_for_owner(archive_rows, assignment):
    rows = archive_rows
    assignment = assignment.format(replacement=uuid.uuid7())
    failed = rows.owner(
        f"UPDATE archive.message_revisions SET {assignment} WHERE revision_id='{rows.revision}'",
        check=False,
    )
    assert failed.returncode != 0 and "immutable" in failed.stderr


@pytest.mark.parametrize("role", ("api", "worker"))
@pytest.mark.parametrize("end", ("COMMIT", "ROLLBACK"))
def test_t05_actual_rls_and_reused_connection(archive_rows, role, end):
    rows = archive_rows
    other = ArchiveRows(rows.database)
    other.seed()
    for table in ARCHIVE_TABLES:
        result = rows.database.sql(
            f"""
            SELECT count(*) FROM archive.{table}; BEGIN;
            SET LOCAL app.current_user_id='{rows.user}';
            SELECT count(*) FROM archive.{table};
            SELECT count(*) FROM archive.{table} WHERE user_id='{other.user}';
            {end}; SELECT count(*) FROM archive.{table}; BEGIN;
            SET LOCAL app.current_user_id='{other.user}';
            SELECT count(*) FROM archive.{table}; COMMIT;
            SELECT count(*) FROM archive.{table}
        """,
            f"mimir_{role}",
        )
        assert result.stdout.splitlines() == ["0", "1", "0", "0", "1", "0"]
        for context, error in (
            ("SET row_security=off", "row-level security"),
            ("SET app.current_user_id='invalid'", "uuid"),
        ):
            failed = rows.database.sql(
                f"{context}; SELECT * FROM archive.{table}",
                f"mimir_{role}",
                check=False,
            )
            assert failed.returncode != 0 and error in failed.stderr
        failed = rows.actor(
            f"UPDATE archive.messages SET current_revision_id=NULL WHERE user_id='{other.user}' RETURNING message_id",
            role,
        )
        assert failed.stdout.strip() == ""
    forged = rows.actor(
        rows.message_sql().replace(f"'{rows.user}'", f"'{other.user}'"),
        role,
        check=False,
    )
    assert forged.returncode != 0
    # Normal logins cannot invoke the owner commit-guard routine directly.
    assert (
        rows.database.sql(
            "SELECT has_function_privilege(current_user,'archive.require_message_revision()','EXECUTE')",
            f"mimir_{role}",
        ).stdout.strip()
        == "f"
    )


def test_t05_cross_owner_relationships_rejected(archive_rows):
    rows = archive_rows
    other = ArchiveRows(rows.database)
    other.seed()
    for statement in (
        f"INSERT INTO archive.conversations(conversation_id,user_id,project_id,title) VALUES ('{uuid.uuid7()}','{rows.user}','{other.project}','synthetic')",
        rows.message_sql().replace(f"'{rows.conversation}'", f"'{other.conversation}'"),
        f"INSERT INTO archive.message_revisions(revision_id,user_id,message_id,revision_number,content_text) VALUES ('{uuid.uuid7()}','{rows.user}','{other.message}',1,'synthetic')",
    ):
        failed = rows.actor(statement, check=False)
        assert failed.returncode != 0 and (
            "foreign key" in failed.stderr or "owned" in failed.stderr
        )


@pytest.mark.parametrize("role", RUNTIME)
def test_t05_least_privilege_and_no_unrestricted_purge(archive_rows, role):
    rows = archive_rows
    for table in ARCHIVE_TABLES:
        for statement in (
            f"DELETE FROM archive.{table}",
            f"TRUNCATE archive.{table} CASCADE",
            f"ALTER TABLE archive.{table} DISABLE ROW LEVEL SECURITY",
        ):
            assert rows.actor(statement, role, check=False).returncode != 0
        if role in ("auth", "maintenance"):
            assert (
                rows.actor(
                    f"SELECT * FROM archive.{table}", role, check=False
                ).returncode
                != 0
            )
    for statement in (
        "UPDATE archive.message_revisions SET content_text='changed'",
        "UPDATE archive.messages SET role='assistant'",
        "SET ROLE mimir_owner",
        "SET ROLE mimir_migrator",
    ):
        assert rows.actor(statement, role, check=False).returncode != 0


@pytest.mark.parametrize("role", ("user", "assistant"))
def test_t05_utf8_content_limit_and_hash(archive_rows, role):
    rows = archive_rows
    rows.actor(rows.message_sql(role=role, text="repeat('é',524288)"))
    assert (
        rows.actor(
            f"SELECT octet_length(content_text),content_hash=sha256(convert_to(content_text,'UTF8')) FROM archive.message_revisions r JOIN archive.messages m USING(user_id,message_id) WHERE m.conversation_id='{rows.conversation}' AND m.ordinal=2"
        ).stdout.strip()
        == "1048576|t"
    )
    failed = rows.actor(
        rows.message_sql(ordinal=3, role=role, text="repeat('é',524288)||'x'"),
        check=False,
    )
    assert failed.returncode != 0 and "check constraint" in failed.stderr
    assert (
        rows.actor(
            f"SELECT count(*) FROM archive.messages WHERE conversation_id='{rows.conversation}'"
        ).stdout.strip()
        == "2"
    )


def test_t05_dense_ordinals_revisions_and_savepoint_rollback(archive_rows):
    rows = archive_rows
    for sql in (
        rows.message_sql(ordinal=0),
        rows.message_sql(ordinal=3),
        rows.message_sql(revision_number=2),
    ):
        failed = rows.actor(sql, check=False)
        assert failed.returncode != 0 and "dense" in failed.stderr
    rows.actor(
        f"SAVEPOINT candidate; {rows.incomplete_sql()}; ROLLBACK TO candidate; {rows.message_sql()}"
    )
    failed = rows.actor(
        f"""
        INSERT INTO archive.message_revisions(revision_id,user_id,message_id,revision_number,content_text)
            VALUES ('{uuid.uuid7()}','{rows.user}','{rows.message}',3,'synthetic')
    """,
        check=False,
    )
    assert failed.returncode != 0 and "dense" in failed.stderr


def test_t05_project_reassignment_archive_and_version_cas(archive_rows):
    rows = archive_rows
    failed = rows.actor(
        f"UPDATE archive.conversations SET project_id=NULL WHERE conversation_id='{rows.conversation}'",
        check=False,
    )
    assert failed.returncode != 0 and "increasing version" in failed.stderr
    rows.actor(
        f"UPDATE archive.conversations SET project_id=NULL,status='archived',conversation_version=2 WHERE conversation_id='{rows.conversation}'"
    )
    assert (
        rows.actor(
            f"SELECT current_revision_id FROM archive.messages WHERE message_id='{rows.message}'"
        ).stdout.strip()
        == rows.revision
    )
    failed = rows.actor(
        f"UPDATE archive.conversations SET conversation_version=1 WHERE conversation_id='{rows.conversation}'",
        check=False,
    )
    assert failed.returncode != 0
    barrier = threading.Barrier(2)

    def change(title):
        barrier.wait(timeout=15)
        return rows.actor(
            f"UPDATE archive.conversations SET title='{title}',conversation_version=3 WHERE conversation_id='{rows.conversation}' AND conversation_version=2 RETURNING conversation_version"
        )

    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
        futures = [
            pool.submit(change, title) for title in ("synthetic A", "synthetic B")
        ]
        assert sorted(future.result().stdout.strip() for future in futures) == ["", "3"]


def test_t05_competing_appends_and_revision_numbers(archive_rows):
    rows = archive_rows
    barrier = threading.Barrier(2)

    def insert(sql):
        barrier.wait(timeout=15)
        return rows.actor(sql, check=False)

    for statements in (
        [rows.message_sql() for _ in range(2)],
        [
            f"INSERT INTO archive.message_revisions(revision_id,user_id,message_id,revision_number,content_text) VALUES ('{uuid.uuid7()}','{rows.user}','{rows.message}',2,'synthetic')"
            for _ in range(2)
        ],
    ):
        with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
            futures = [pool.submit(insert, sql) for sql in statements]
            results = [future.result() for future in futures]
        assert sorted(result.returncode == 0 for result in results) == [False, True]
        assert all("dense" in result.stderr for result in results if result.returncode)
    assert (
        rows.actor(
            f"SELECT string_agg(ordinal::text,',' ORDER BY ordinal) FROM archive.messages WHERE conversation_id='{rows.conversation}'"
        ).stdout.strip()
        == "1,2"
    )


def test_t05_deferred_pair_validation_and_full_transaction_rollback(archive_rows):
    rows = archive_rows
    # Neither a missing row behind a non-null pointer nor forcing deferred
    # constraints early can leave an incomplete canonical message committed.
    for sql in (
        rows.incomplete_sql()
        .replace("ordinal,role)", "ordinal,role,current_revision_id)")
        .replace("2,'user')", f"2,'user','{uuid.uuid7()}')"),
        rows.incomplete_sql() + "; SET CONSTRAINTS ALL IMMEDIATE",
        rows.message_sql() + "; SELECT 1/0",
    ):
        assert rows.actor(sql, check=False).returncode != 0
        assert (
            rows.actor(
                f"SELECT count(*) FROM archive.messages WHERE conversation_id='{rows.conversation}'"
            ).stdout.strip()
            == "1"
        )
        assert (
            rows.actor("SELECT count(*) FROM archive.message_revisions").stdout.strip()
            == "1"
        )
    rows.actor(rows.message_sql())


@pytest.mark.parametrize(
    "table,key",
    (
        ("projects", "project_id"),
        ("conversations", "conversation_id"),
    ),
)
def test_t05_metadata_identity_ownership_and_status_guards(archive_rows, table, key):
    rows = archive_rows
    identity = rows.project if table == "projects" else rows.conversation
    for assignment in (
        f"{key}='{uuid.uuid7()}'",
        f"user_id='{uuid.uuid7()}'",
        "created_at=created_at-interval '1 day'",
        "status='invalid'",
    ):
        assert (
            rows.owner(
                f"UPDATE archive.{table} SET {assignment} WHERE {key}='{identity}'",
                check=False,
            ).returncode
            != 0
        )
    if table == "conversations":
        assert (
            rows.owner(
                f"UPDATE archive.conversations SET updated_at=updated_at-interval '1 second' WHERE conversation_id='{identity}'",
                check=False,
            ).returncode
            != 0
        )
    else:
        rows.actor(
            f"UPDATE archive.projects SET status='archived' WHERE project_id='{identity}'"
        )
        assert rows.actor("SELECT count(*) FROM archive.messages").stdout.strip() == "1"


def test_t05_governed_deletion_has_no_immutability_update_bypass(archive_rows):
    rows = archive_rows
    # Schema-owner deletion is a storage capability for future reviewed purge
    # routines, not a runtime grant or an implemented T35/T36 lifecycle service.
    failed = rows.owner(
        f"DELETE FROM archive.message_revisions WHERE revision_id='{rows.revision}'",
        check=False,
    )
    assert failed.returncode != 0 and "foreign key" in failed.stderr
    rows.owner(f"""
        DELETE FROM archive.message_revisions WHERE message_id='{rows.message}';
        DELETE FROM archive.messages WHERE message_id='{rows.message}'
    """)
    assert rows.actor("SELECT count(*) FROM archive.messages").stdout.strip() == "0"
    assert (
        rows.actor("SELECT count(*) FROM archive.message_revisions").stdout.strip()
        == "0"
    )


V004 = "V004__immutable_branch_snapshots.sql"


class BranchRows(ArchiveRows):
    def seed_prefix(self, count):
        self.actor(
            ";".join(self.message_sql(ordinal) for ordinal in range(2, count + 1))
        )
        return self.actor(f"""
            SELECT revision_id FROM archive.conversation_visible_revisions
            WHERE conversation_id='{self.conversation}' AND ordinal={count}
        """).stdout.strip()

    def validation_calls_sql(self):
        return """
            SELECT coalesce(sum(calls),0) FROM pg_stat_xact_user_functions
            WHERE funcid='archive.require_exact_branch_snapshot()'::regprocedure
        """

    def branch_sql(self, child, parent=None, point=None, mode="after_revision"):
        parent = parent or self.conversation
        point = point or self.revision
        return f"""
            INSERT INTO archive.conversations(conversation_id,user_id,project_id,title,
                parent_conversation_id,branch_point_revision_id,branch_mode)
            SELECT '{child}','{self.user}',project_id,'synthetic branch',
                '{parent}','{point}','{mode}' FROM archive.conversations
                WHERE conversation_id='{parent}'
        """

    def snapshot_sql(self, child, parent=None):
        return f"""
            INSERT INTO archive.conversation_branch_revisions
                (user_id,conversation_id,ordinal,message_id,revision_id)
            SELECT p.user_id,'{child}',p.ordinal,p.message_id,p.revision_id
            FROM archive.conversation_visible_revisions p
            JOIN archive.conversations c ON c.user_id=p.user_id
                AND c.conversation_id='{child}'
            WHERE p.conversation_id='{parent or self.conversation}'
                AND p.ordinal<=c.inherited_revision_count
        """

    def create_branch(self, child=None, parent=None, point=None, mode="after_revision"):
        child = child or str(uuid.uuid7())
        self.actor(
            self.branch_sql(child, parent, point, mode)
            + ";"
            + self.snapshot_sql(child, parent)
        )
        return child

    def visible(self, conversation):
        return self.actor(f"""
            SELECT ordinal,message_id,revision_id FROM archive.conversation_visible_revisions
            WHERE conversation_id='{conversation}' ORDER BY ordinal
        """).stdout.splitlines()


@pytest.fixture(scope="module")
def branch_database(tmp_path_factory):
    stack = DatabaseStack(tmp_path_factory.mktemp("branch-database"), through_version=4)
    try:
        stack.start()
        stack.migrate()
        yield stack
    finally:
        stack.cleanup()


@pytest.fixture
def branch_rows(branch_database):
    rows = BranchRows(branch_database)
    rows.seed()
    return rows


@pytest.fixture
def tracked_branch_rows(branch_rows):
    # Test-only superuser configuration; transactions still use the actual API
    # login and its unchanged grants. Transaction-local counters need no polling.
    branch_rows.database.sql("ALTER ROLE mimir_api SET track_functions='pl'")
    try:
        yield branch_rows
    finally:
        branch_rows.database.sql("ALTER ROLE mimir_api RESET track_functions")


@pytest.mark.parametrize("count", (1000, 2000, 5000))
@pytest.mark.parametrize("nested", (False, True), ids=("root", "nested"))
def test_t06_large_prefix_validated_once(tracked_branch_rows, count, nested):
    rows = tracked_branch_rows
    point = rows.seed_prefix(count)
    expected = rows.visible(rows.conversation)
    parent = rows.conversation
    if nested:
        parent = rows.create_branch(point=point)
    child = str(uuid.uuid7())
    started = time.monotonic()
    result = rows.actor(
        rows.branch_sql(child, parent=parent, point=point)
        + ";"
        + rows.snapshot_sql(child, parent=parent)
        + ";"
        + rows.validation_calls_sql()
        + "; SET CONSTRAINTS ALL IMMEDIATE;"
        + rows.validation_calls_sql()
        + "; SET CONSTRAINTS ALL IMMEDIATE;"
        + rows.validation_calls_sql()
    )
    elapsed = time.monotonic() - started  # Includes successful COMMIT, excludes seed.
    print(f"T06 prefix={count} nested={nested} commit_seconds={elapsed:.3f}")
    assert result.stdout.splitlines() == ["0", "1", "1"]
    assert rows.visible(child) == expected


def test_t06_split_inserts_and_multiple_branches_validate_once_each(
    tracked_branch_rows,
):
    rows = tracked_branch_rows
    point = rows.seed_prefix(3)
    children = [str(uuid.uuid7()), str(uuid.uuid7())]
    statements = []
    for child in children:
        statements.append(rows.branch_sql(child, point=point))
        for ordinal in range(1, 4):
            statements.append(rows.snapshot_sql(child) + f" AND p.ordinal={ordinal}")
    statements.extend(
        (
            rows.validation_calls_sql(),
            "SET CONSTRAINTS ALL IMMEDIATE",
            rows.validation_calls_sql(),
        )
    )
    assert rows.actor(";".join(statements)).stdout.splitlines() == ["0", "2"]
    for child in children:
        assert rows.visible(child) == rows.visible(rows.conversation)


@pytest.mark.parametrize("complete", (False, True))
def test_t06_explicit_constraint_check_preserves_completeness(branch_rows, complete):
    rows = branch_rows
    child = str(uuid.uuid7())
    sql = rows.branch_sql(child)
    if complete:
        sql += ";" + rows.snapshot_sql(child)
    result = rows.actor(sql + "; SET CONSTRAINTS ALL IMMEDIATE", check=False)
    if complete:
        assert result.returncode == 0
        assert rows.visible(child) == rows.visible(rows.conversation)
    else:
        assert result.returncode != 0 and "dense exact parent prefix" in result.stderr
        assert (
            rows.actor(
                f"SELECT count(*) FROM archive.conversations WHERE conversation_id='{child}'"
            ).stdout.strip()
            == "0"
        )


@pytest.mark.parametrize("empty", (False, True))
@pytest.mark.parametrize("committed", (False, True))
def test_t06_snapshot_cannot_extend_after_successful_validation(
    branch_rows, empty, committed
):
    rows = branch_rows
    child = str(uuid.uuid7())
    create = (
        rows.branch_sql(child, mode="replace_message" if empty else "after_revision")
        + ";"
        + rows.snapshot_sql(child)
    )
    point = rows.seed_prefix(2)
    insert = f"""
        INSERT INTO archive.conversation_branch_revisions
            (user_id,conversation_id,ordinal,message_id,revision_id)
        SELECT user_id,'{child}',{1 if empty else 2},message_id,revision_id
        FROM archive.conversation_visible_revisions
        WHERE conversation_id='{rows.conversation}' AND revision_id='{point}'
    """
    if committed:
        rows.actor(create)
        result = rows.actor(insert, check=False)
    else:
        result = rows.actor(
            create + "; SET CONSTRAINTS ALL IMMEDIATE;" + insert, check=False
        )
    assert result.returncode != 0 and "dense exact parent prefix" in result.stderr
    if committed:
        assert rows.visible(child) == (
            [] if empty else [f"1|{rows.message}|{rows.revision}"]
        )
    else:
        assert (
            rows.actor(
                f"SELECT count(*) FROM archive.conversations WHERE conversation_id='{child}'"
            ).stdout.strip()
            == "0"
        )


def test_t06_fresh_repeat_validate_restart_and_catalog(branch_rows):
    rows = branch_rows
    child = rows.create_branch()
    database = rows.database
    history_sql = "SELECT version,checksum,success FROM public.flyway_schema_history ORDER BY installed_rank"
    history = database.sql(history_sql).stdout
    assert [line.split("|")[0] for line in history.splitlines()] == [
        "001",
        "002",
        "003",
        "004",
    ]
    assert database.sql("SHOW server_version_num").stdout.startswith("18")
    database.migrate()
    database.flyway("validate")
    database.compose("restart", "postgres")
    database.compose("up", "-d", "--wait", "postgres")
    database.flyway("validate")
    assert database.sql(history_sql).stdout == history
    assert rows.visible(child) == [f"1|{rows.message}|{rows.revision}"]
    assert (
        database.sql("""
        SELECT pg_get_userbyid(relowner),relrowsecurity,relforcerowsecurity
        FROM pg_class WHERE oid='archive.conversation_branch_revisions'::regclass
    """).stdout.strip()
        == "mimir_owner|t|t"
    )
    assert database.sql("""
        SELECT tgname,tgdeferrable,tginitdeferred FROM pg_trigger
        WHERE tgname IN ('branch_snapshot_required','branch_revision_insert_guard',
            'branch_snapshot_exact') ORDER BY tgname
    """).stdout.splitlines() == [
        "branch_revision_insert_guard|f|f",
        "branch_snapshot_required|t|t",
    ]
    assert (
        database.sql("""
        SELECT count(*) FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace
        WHERE n.nspname='archive' AND (pg_get_userbyid(p.proowner)<>'mimir_owner' OR
            EXISTS (SELECT FROM aclexplode(coalesce(p.proacl,acldefault('f',p.proowner))) a
                WHERE a.grantee=0 AND a.privilege_type='EXECUTE'))
    """).stdout.strip()
        == "0"
    )


def test_t06_upgrade_rollback_recovery_and_drift(tmp_path):
    database = DatabaseStack(tmp_path, through_version=3)
    try:
        database.start()
        database.migrate()
        rows = BranchRows(database)
        rows.seed()
        history_sql = "SELECT version,checksum FROM public.flyway_schema_history ORDER BY installed_rank"
        history = database.sql(history_sql).stdout
        candidate = database.migrations / V004
        original = (ROOT / "backend/migrations" / V004).read_bytes()
        candidate.write_bytes(original + b"\nSELECT 1/0;\n")
        assert database.flyway("migrate", check=False).returncode != 0
        assert database.sql(history_sql).stdout == history
        assert (
            database.sql("""
            SELECT to_regclass('archive.conversation_branch_revisions') IS NULL,
                to_regclass('archive.conversation_visible_revisions') IS NULL,
                to_regprocedure('archive.prepare_branch_link()') IS NULL
        """).stdout.strip()
            == "t|t|t"
        )
        assert (
            database.sql("""
            SELECT count(*) FROM information_schema.columns WHERE table_schema='archive'
                AND table_name='conversations' AND column_name='parent_conversation_id'
        """).stdout.strip()
            == "0"
        )
        candidate.write_bytes(original)
        database.flyway("migrate")
        database.flyway("validate")
        assert database.sql(history_sql + " LIMIT 3").stdout == history
        assert rows.visible(rows.conversation) == [f"1|{rows.message}|{rows.revision}"]
        rows.create_branch()
        try:
            candidate.write_bytes(original + b"\nSELECT 12345;\n")
            failed = database.flyway("validate", check=False)
            assert (
                failed.returncode != 0
                and "checksum" in (failed.stdout + failed.stderr).lower()
            )
            assert database.flyway("migrate", check=False).returncode != 0
        finally:
            candidate.write_bytes(original)
        database.flyway("validate")
    finally:
        database.cleanup()


@pytest.mark.parametrize("mode", ("after_revision", "replace_message"))
def test_t06_nested_flattened_prefix_and_local_ordinals(branch_rows, mode):
    rows = branch_rows
    second_message, second_revision = str(uuid.uuid7()), str(uuid.uuid7())
    rows.actor(rows.message_sql(2, second_message, second_revision))
    parent_history = rows.visible(rows.conversation)
    child = rows.create_branch(point=second_revision, mode=mode)
    length = 2 if mode == "after_revision" else 1
    assert rows.visible(child) == parent_history[:length]
    # Append new local content at local ordinal one, after inherited history.
    root = rows.conversation
    rows.conversation = child
    local_message, local_revision = str(uuid.uuid7()), str(uuid.uuid7())
    rows.actor(rows.message_sql(1, local_message, local_revision))
    grandchild = rows.create_branch(point=local_revision)
    assert rows.visible(grandchild) == parent_history[:length] + [
        f"{length + 1}|{local_message}|{local_revision}"
    ]
    # Branch from an inherited point in a nested parent, excluding later history.
    nested = rows.create_branch(parent=grandchild, point=rows.revision)
    assert rows.visible(nested) == parent_history[:1]
    assert (
        rows.actor(
            f"SELECT count(*) FROM archive.messages WHERE conversation_id='{grandchild}'"
        ).stdout.strip()
        == "0"
    )
    rows.conversation = root
    rows.actor(rows.message_sql(3))
    extra_revision = str(uuid.uuid7())
    rows.actor(f"""
        INSERT INTO archive.message_revisions(revision_id,user_id,message_id,revision_number,content_text)
        VALUES ('{extra_revision}','{rows.user}','{rows.message}',2,'synthetic later revision')
    """)
    for sql in (
        f"UPDATE archive.message_revisions SET content_text='changed' WHERE revision_id='{rows.revision}'",
        f"UPDATE archive.messages SET current_revision_id='{extra_revision}' WHERE message_id='{rows.message}'",
    ):
        assert rows.owner(sql, check=False).returncode != 0
    assert rows.visible(nested) == parent_history[:1]
    assert rows.visible(grandchild) == parent_history[:length] + [
        f"{length + 1}|{local_message}|{local_revision}"
    ]
    # Parent and child project association become independent after creation.
    rows.actor(
        f"UPDATE archive.conversations SET project_id=NULL,conversation_version=2 WHERE conversation_id='{root}'"
    )
    assert (
        rows.actor(
            f"SELECT project_id FROM archive.conversations WHERE conversation_id='{child}'"
        ).stdout.strip()
        == rows.project
    )


def test_t06_replace_first_revision_has_empty_prefix(branch_rows):
    rows = branch_rows
    child = rows.create_branch(mode="replace_message")
    assert rows.visible(child) == []
    assert (
        rows.actor(
            f"SELECT inherited_revision_count FROM archive.conversations WHERE conversation_id='{child}'"
        ).stdout.strip()
        == "0"
    )


@pytest.mark.parametrize(
    "context",
    (
        "RESET app.current_user_id",
        "SET LOCAL app.current_user_id='00000000-0000-0000-0000-000000000001'",
    ),
)
def test_t06_complete_snapshot_survives_actor_change_and_savepoint_recovery(
    branch_rows, context
):
    rows = branch_rows
    child = str(uuid.uuid7())
    rows.actor(rows.branch_sql(child) + ";" + rows.snapshot_sql(child) + ";" + context)
    assert rows.visible(child) == [f"1|{rows.message}|{rows.revision}"]
    # A rolled-back attempted branch leaves no rows and does not consume ordinals.
    aborted = str(uuid.uuid7())
    rows.actor(
        "SAVEPOINT branch_attempt;"
        + rows.branch_sql(aborted)
        + ";"
        + rows.snapshot_sql(aborted)
        + "; ROLLBACK TO SAVEPOINT branch_attempt"
    )
    assert rows.visible(aborted) == []
    assert (
        rows.actor(
            f"SELECT count(*) FROM archive.conversations WHERE conversation_id='{aborted}'"
        ).stdout.strip()
        == "0"
    )
    rows.create_branch(child=aborted)
    assert rows.visible(aborted) == rows.visible(child)


@pytest.mark.parametrize("ordinal", (0, -1, 1))
def test_t06_snapshot_ordinal_boundaries_and_duplicate_rejected(branch_rows, ordinal):
    rows = branch_rows
    child = rows.create_branch()
    failed = rows.actor(
        f"INSERT INTO archive.conversation_branch_revisions VALUES ('{rows.user}','{child}',{ordinal},'{rows.message}','{rows.revision}')",
        check=False,
    )
    assert failed.returncode != 0
    assert (
        "check constraint" in failed.stderr
        if ordinal <= 0
        else "duplicate key" in failed.stderr
    )


def test_t06_incomplete_branch_and_missing_actor_fail_closed(branch_rows):
    rows = branch_rows
    child = str(uuid.uuid7())
    failed = rows.actor(rows.branch_sql(child), check=False)
    assert failed.returncode != 0 and "dense exact parent prefix" in failed.stderr
    assert (
        rows.actor(
            f"SELECT count(*) FROM archive.conversations WHERE conversation_id='{child}'"
        ).stdout.strip()
        == "0"
    )
    failed = rows.database.sql(
        f"INSERT INTO archive.conversations(conversation_id,user_id,title) VALUES ('{child}','{rows.user}','synthetic')",
        "mimir_api",
        check=False,
    )
    assert failed.returncode != 0 and "row-level security" in failed.stderr


@pytest.mark.parametrize(
    "fault", ("missing", "gap", "swapped", "wrong_revision", "extra", "root")
)
@pytest.mark.parametrize("clear_actor", (False, True))
def test_t06_malformed_snapshot_rejected_and_rolled_back(
    branch_rows, fault, clear_actor
):
    rows = branch_rows
    rows.actor(rows.message_sql(2))
    point = rows.actor(
        f"SELECT revision_id FROM archive.conversation_visible_revisions WHERE conversation_id='{rows.conversation}' AND ordinal=2"
    ).stdout.strip()
    extra_revision = str(uuid.uuid7())
    rows.actor(
        f"INSERT INTO archive.message_revisions(revision_id,user_id,message_id,revision_number,content_text) VALUES ('{extra_revision}','{rows.user}','{rows.message}',2,'synthetic')"
    )
    child = str(uuid.uuid7())
    create = rows.branch_sql(child, point=point)
    select_sql = f"SELECT user_id,'{child}',ordinal,message_id,revision_id FROM archive.conversation_visible_revisions WHERE conversation_id='{rows.conversation}'"
    if fault == "missing":
        select_sql += " AND ordinal=1"
    elif fault == "gap":
        select_sql = select_sql.replace(",ordinal,", ",ordinal+1,")
    elif fault == "swapped":
        select_sql = select_sql.replace(",ordinal,", ",3-ordinal,")
    elif fault == "wrong_revision":
        select_sql = select_sql.replace(
            ",revision_id FROM",
            f",CASE WHEN ordinal=1 THEN '{extra_revision}'::uuid ELSE revision_id END FROM",
        )
    elif fault == "extra":
        create = rows.branch_sql(child)  # boundary one, snapshot two
    else:
        create = f"INSERT INTO archive.conversations(conversation_id,user_id,title) VALUES ('{child}','{rows.user}','synthetic root')"
    result = rows.actor(
        create
        + "; INSERT INTO archive.conversation_branch_revisions(user_id,conversation_id,ordinal,message_id,revision_id) "
        + select_sql
        + "; SELECT 'reference_inserts_finished'"
        + ("; RESET app.current_user_id" if clear_actor else ""),
        check=False,
    )
    assert result.returncode != 0 and "dense exact parent prefix" in result.stderr
    # Missing/substituted/reordered content fails at the deferred commit guard;
    # an upper-bound violation must fail immediately before this marker.
    assert ("reference_inserts_finished" in result.stdout) == (
        fault in ("missing", "swapped", "wrong_revision")
    )
    assert (
        rows.actor(
            f"SELECT count(*) FROM archive.conversations WHERE conversation_id='{child}'"
        ).stdout.strip()
        == "0"
    )
    assert (
        rows.actor(
            f"SELECT count(*) FROM archive.conversation_branch_revisions WHERE conversation_id='{child}'"
        ).stdout.strip()
        == "0"
    )


@pytest.mark.parametrize(
    "fault",
    (
        "unselected",
        "unrelated",
        "foreign_parent",
        "foreign_point",
        "project",
        "self",
        "mode",
    ),
)
def test_t06_invalid_linkage_and_cross_owner_foreign_keys(branch_rows, fault):
    rows = branch_rows
    other = BranchRows(rows.database)
    other.seed()
    child = str(uuid.uuid7())
    sql = rows.branch_sql(child)
    if fault == "unselected":
        point = str(uuid.uuid7())
        rows.actor(
            f"INSERT INTO archive.message_revisions(revision_id,user_id,message_id,revision_number,content_text) VALUES ('{point}','{rows.user}','{rows.message}',2,'synthetic')"
        )
        sql = rows.branch_sql(child, point=point)
    elif fault == "unrelated":
        sibling = rows.create_branch(mode="replace_message")
        root = rows.conversation
        rows.conversation = sibling
        point = str(uuid.uuid7())
        rows.actor(rows.message_sql(1, revision=point))
        rows.conversation = root
        sql = rows.branch_sql(child, point=point)
    elif fault == "foreign_parent":
        # Direct VALUES avoids an RLS-filtered INSERT SELECT silently inserting zero rows.
        sql = f"INSERT INTO archive.conversations(conversation_id,user_id,title,parent_conversation_id,branch_point_revision_id,branch_mode) VALUES ('{child}','{rows.user}','synthetic','{other.conversation}','{other.revision}','after_revision')"
    elif fault == "foreign_point":
        sql = rows.branch_sql(child, point=other.revision)
    elif fault == "project":
        sql = sql.replace("project_id,'synthetic branch'", "NULL,'synthetic branch'")
    elif fault == "self":
        sql = sql.replace(
            f"'{rows.conversation}','{rows.revision}'", f"'{child}','{rows.revision}'"
        )
    else:
        sql = rows.branch_sql(child, mode="unknown")
    assert rows.actor(sql, check=False).returncode != 0
    child = rows.create_branch()
    # Composite owner FK rejects a same-owner message paired with a foreign revision.
    assert (
        rows.owner(
            f"INSERT INTO archive.conversation_branch_revisions VALUES ('{rows.user}','{child}',2,'{rows.message}','{other.revision}')",
            check=False,
        ).returncode
        != 0
    )
    assert (
        rows.owner(
            f"INSERT INTO archive.conversation_branch_revisions VALUES ('{other.user}','{child}',2,'{other.message}','{other.revision}')",
            check=False,
        ).returncode
        != 0
    )


@pytest.mark.parametrize("role", RUNTIME)
def test_t06_runtime_privileges_rls_and_view_scope(branch_rows, role):
    rows = branch_rows
    child = rows.create_branch()
    for statement in (
        f"UPDATE archive.conversation_branch_revisions SET ordinal=2 WHERE conversation_id='{child}'",
        f"DELETE FROM archive.conversation_branch_revisions WHERE conversation_id='{child}'",
        "TRUNCATE archive.conversation_branch_revisions",
        "SELECT archive.require_exact_branch_snapshot()",
        f"UPDATE archive.conversations SET parent_conversation_id=NULL WHERE conversation_id='{child}'",
    ):
        assert rows.actor(statement, role=role, check=False).returncode != 0
    if role in ("auth", "maintenance"):
        assert (
            rows.actor(
                "SELECT * FROM archive.conversation_visible_revisions",
                role=role,
                check=False,
            ).returncode
            != 0
        )
        return
    other = BranchRows(rows.database)
    other.seed()
    for end in ("COMMIT", "ROLLBACK"):
        result = rows.database.sql(
            f"""
            BEGIN; SET LOCAL app.current_user_id='{rows.user}';
            SELECT count(*) FROM archive.conversation_branch_revisions;
            SELECT count(*) FROM archive.conversation_visible_revisions WHERE user_id<>'{rows.user}';
            {end};
            SELECT count(*) FROM archive.conversation_branch_revisions;
            SELECT count(*) FROM archive.conversation_visible_revisions;
        """,
            f"mimir_{role}",
        )
        assert result.stdout.splitlines() == ["1", "0", "0", "0"]
    assert (
        rows.actor(
            "SET LOCAL row_security=off; SELECT * FROM archive.conversation_branch_revisions",
            role=role,
            check=False,
        ).returncode
        != 0
    )
    assert (
        rows.actor(
            "SET LOCAL app.current_user_id='malformed'; SELECT * FROM archive.conversation_visible_revisions",
            role=role,
            check=False,
        ).returncode
        != 0
    )
    assert (
        rows.actor(
            f"INSERT INTO archive.conversation_branch_revisions VALUES ('{other.user}','{child}',2,'{other.message}','{other.revision}')",
            role=role,
            check=False,
        ).returncode
        != 0
    )
    if role == "worker":
        assert (
            rows.actor(
                rows.branch_sql(str(uuid.uuid7())), role=role, check=False
            ).returncode
            != 0
        )


def test_t06_committed_snapshot_and_linkage_are_immutable(branch_rows):
    rows = branch_rows
    child = rows.create_branch()
    for assignment in (
        "parent_conversation_id=NULL",
        "branch_point_revision_id=NULL",
        "branch_mode='replace_message'",
        "inherited_revision_count=0",
    ):
        result = rows.owner(
            f"UPDATE archive.conversations SET {assignment} WHERE conversation_id='{child}'",
            check=False,
        )
        assert result.returncode != 0 and "immutable" in result.stderr
    assert (
        rows.owner(
            f"UPDATE archive.conversation_branch_revisions SET ordinal=ordinal WHERE conversation_id='{child}'",
            check=False,
        ).returncode
        != 0
    )
    rows.actor(rows.message_sql())
    # Adding valid later parent history to an already committed child is forbidden.
    assert (
        rows.actor(
            f"INSERT INTO archive.conversation_branch_revisions SELECT user_id,'{child}',ordinal,message_id,revision_id FROM archive.conversation_visible_revisions WHERE conversation_id='{rows.conversation}' AND ordinal=2",
            check=False,
        ).returncode
        != 0
    )
    assert (
        rows.owner(
            f"DELETE FROM archive.conversations WHERE conversation_id='{rows.conversation}'",
            check=False,
        ).returncode
        != 0
    )
    assert (
        rows.owner(
            f"DELETE FROM archive.message_revisions WHERE revision_id='{rows.revision}'",
            check=False,
        ).returncode
        != 0
    )
    assert rows.visible(child) == [f"1|{rows.message}|{rows.revision}"]


def test_t06_competing_branches_and_parent_appends(branch_rows):
    rows = branch_rows
    barrier = threading.Barrier(3)
    children = [str(uuid.uuid7()), str(uuid.uuid7())]

    def execute(sql):
        barrier.wait(timeout=15)
        return rows.actor(sql)

    statements = [
        rows.branch_sql(child) + ";" + rows.snapshot_sql(child) for child in children
    ]
    statements.append(rows.message_sql())
    with concurrent.futures.ThreadPoolExecutor(max_workers=3) as pool:
        results = list(pool.map(execute, statements))
    assert all(result.returncode == 0 for result in results)
    for child in children:
        assert rows.visible(child) == [f"1|{rows.message}|{rows.revision}"]
    assert len(rows.visible(rows.conversation)) == 2


def test_t06_branch_and_parent_delete_preserve_foreign_keys(branch_rows):
    rows = branch_rows
    child = str(uuid.uuid7())
    barrier = threading.Barrier(2)

    def branch():
        barrier.wait(timeout=15)
        return rows.actor(
            rows.branch_sql(child) + ";" + rows.snapshot_sql(child), check=False
        )

    def delete():
        barrier.wait(timeout=15)
        return rows.owner(
            # Storage-only candidate purge, under the privileged test owner.
            # Removing all source rows avoids relying on pre-existing message
            # FKs to make every deletion fail regardless of the branch race.
            f"""
                SELECT 1 FROM archive.conversations
                    WHERE conversation_id='{rows.conversation}' FOR UPDATE;
                DELETE FROM archive.message_revisions WHERE message_id='{rows.message}';
                DELETE FROM archive.messages WHERE message_id='{rows.message}';
                DELETE FROM archive.conversations WHERE conversation_id='{rows.conversation}'
            """,
            check=False,
        )

    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
        created = pool.submit(branch)
        deleted = pool.submit(delete)
        branch_result, purge_result = created.result(), deleted.result()
    remaining = rows.actor(
        f"SELECT count(*) FROM archive.conversations WHERE conversation_id IN ('{child}','{rows.conversation}')"
    ).stdout.strip()
    if purge_result.returncode == 0:
        assert remaining == "0"
        assert rows.visible(child) == []
    else:
        assert branch_result.returncode == 0
        assert remaining == "2"
        assert rows.visible(child) == [f"1|{rows.message}|{rows.revision}"]


V005 = "V005__runtime_jobs_and_operations.sql"
RUNTIME_TABLES = (
    "model_runs",
    "model_run_attempts",
    "jobs",
    "memory_ingestions",
    "operation_ledger",
    "outbox_events",
    "context_assembly_records",
    "deletion_controls",
)


class RuntimeRows(ArchiveRows):
    """SQL protocol probes for T07 storage, not a production queue/executor."""

    def __init__(self, database):
        super().__init__(database)
        self.run = str(uuid.uuid7())
        self.job = str(uuid.uuid7())
        self.token = str(uuid.uuid4())

    def run_sql(self, run=None, kind="conversation", extra_columns="", extra_values=""):
        conversation = f"'{self.conversation}'" if kind == "conversation" else "NULL"
        message = f"'{self.message}'" if kind == "conversation" else "NULL"
        return f"""
            INSERT INTO runtime.model_runs(run_id,user_id,kind,conversation_id,input_message_id,
                model_config_id{extra_columns}) VALUES ('{run or self.run}','{self.user}',
                '{kind}',{conversation},{message},'synthetic-model'{extra_values})
        """

    def job_sql(self, job=None, job_type="memory_ingest"):
        return f"""
            INSERT INTO runtime.jobs(job_id,user_id,job_type)
                VALUES ('{job or self.job}','{self.user}','{job_type}')
        """

    def running_sql(self, run=None, token=None, generation=1):
        return f"""
            UPDATE runtime.model_runs SET status='running',started_at=created_at,
                execution_token='{token or self.token}',execution_generation={generation},
                lease_expires_at=clock_timestamp()+interval '2 minutes'
                WHERE run_id='{run or self.run}'
        """

    def finish_sql(self, outcome="cancelled", run=None, extra=""):
        return f"""
            UPDATE runtime.model_runs SET status='{outcome}',completed_at=clock_timestamp(),
                execution_token=NULL,lease_expires_at=NULL {extra}
                WHERE run_id='{run or self.run}'
        """

    def operation_sql(self, key="synthetic-key", run=None, kind="message_submit"):
        standalone = kind == "standalone_submit"
        conversation = "NULL" if standalone else f"'{self.conversation}'"
        message = "NULL" if standalone else f"'{self.message}'"
        version = "NULL" if standalone else "1"
        return f"""
            INSERT INTO runtime.operation_ledger(user_id,operation_type,idempotency_key,
                request_fingerprint,run_id,conversation_id,message_id,conversation_version,
                retained_until,tombstone_until) VALUES ('{self.user}','{kind}','{key}',
                sha256('synthetic fingerprint'::bytea),'{run or self.run}',{conversation},
                {message},{version},CURRENT_TIMESTAMP+interval '720 hours',
                CURRENT_TIMESTAMP+interval '1440 hours')
        """

    def claim_sql(self, token=None, predicate=None):
        return f"""
            WITH next AS (SELECT job_id FROM runtime.jobs
                WHERE user_id='{self.user}' AND status IN ('pending','retry_wait')
                    AND available_at <= clock_timestamp() {predicate or ""}
                ORDER BY created_at,job_id FOR UPDATE SKIP LOCKED LIMIT 1)
            UPDATE runtime.jobs j SET status='running',lease_token='{token or self.token}',
                lease_generation=lease_generation+1,attempt_count=attempt_count+1,
                heartbeat_at=clock_timestamp(),lease_expires_at=clock_timestamp()+interval '2 minutes'
                FROM next WHERE j.job_id=next.job_id RETURNING j.job_id
        """

    def success_sql(self):
        assistant, revision = str(uuid.uuid7()), str(uuid.uuid7())
        return (
            self.message_sql(2, assistant, revision, role="assistant")
            + ";"
            + self.finish_sql(
                "completed", extra=f",assistant_message_id='{assistant}'"
            ),
            revision,
        )

    def ingestion_sql(self, assistant_revision, job=None):
        return f"""
            INSERT INTO runtime.memory_ingestions(ingestion_id,user_id,job_id,run_id,
                conversation_id,project_id,user_revision_id,assistant_revision_id,
                provider_config_id,committed_at) VALUES ('{uuid.uuid7()}','{self.user}',
                '{job or self.job}','{self.run}','{self.conversation}','{self.project}',
                '{self.revision}','{assistant_revision}','synthetic-memory',CURRENT_TIMESTAMP)
        """


@pytest.fixture(scope="module")
def runtime_database(tmp_path_factory):
    stack = DatabaseStack(
        tmp_path_factory.mktemp("runtime-database"), through_version=5
    )
    try:
        stack.start()
        stack.migrate()
        yield stack
    finally:
        stack.cleanup()


@pytest.fixture
def runtime_rows(runtime_database):
    rows = RuntimeRows(runtime_database)
    rows.seed()
    rows.actor(rows.run_sql() + ";" + rows.job_sql())
    return rows


def test_t07_fresh_repeat_validate_restart_catalog(runtime_rows):
    rows = runtime_rows
    database = rows.database
    history_sql = "SELECT version,checksum,success FROM public.flyway_schema_history ORDER BY installed_rank"
    history = database.sql(history_sql).stdout
    assert [line.split("|")[0] for line in history.splitlines()] == [
        "001",
        "002",
        "003",
        "004",
        "005",
    ]
    assert database.sql("SHOW server_version_num").stdout.startswith("18")
    database.migrate()
    database.flyway("validate")
    database.compose("restart", "postgres")
    database.compose("up", "-d", "--wait", "postgres")
    database.flyway("validate")
    assert database.sql(history_sql).stdout == history
    tables = database.sql("""
        SELECT relname,pg_get_userbyid(relowner),relrowsecurity,relforcerowsecurity
        FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace
        WHERE n.nspname='runtime' AND relkind='r' ORDER BY relname
    """).stdout.splitlines()
    assert tables == [f"{name}|mimir_owner|t|t" for name in sorted(RUNTIME_TABLES)]
    indexes = database.sql("""
        SELECT indexname FROM pg_indexes WHERE schemaname='runtime'
            AND indexname IN ('model_runs_one_active_conversation','model_runs_pending',
                'model_runs_leases','model_runs_expiry','model_run_attempts_one_open',
                'jobs_claimable','jobs_owner_fifo','jobs_expired_leases',
                'operation_ledger_cleanup','outbox_events_unpublished')
    """).stdout.splitlines()
    assert len(indexes) == 10
    assert (
        database.sql("""
        SELECT count(*) FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace
        WHERE n.nspname='runtime' AND (p.prosecdef OR EXISTS
            (SELECT FROM aclexplode(coalesce(p.proacl,acldefault('f',p.proowner))) a
                WHERE a.grantee=0 AND a.privilege_type='EXECUTE'))
    """).stdout.strip()
        == "0"
    )
    assert (
        rows.actor(
            f"SELECT status FROM runtime.model_runs WHERE run_id='{rows.run}'"
        ).stdout.strip()
        == "pending"
    )


def test_t07_upgrade_rollback_recovery_and_checksum_drift(tmp_path):
    database = DatabaseStack(tmp_path, through_version=4)
    try:
        database.start()
        database.migrate()
        rows = BranchRows(database)
        rows.seed()
        child = rows.create_branch()
        visible = rows.visible(child)
        history_sql = "SELECT version,checksum FROM public.flyway_schema_history ORDER BY installed_rank"
        history = database.sql(history_sql).stdout
        candidate = database.migrations / V005
        original = (ROOT / "backend/migrations" / V005).read_bytes()
        candidate.write_bytes(original + b"\nSELECT 1/0;\n")
        assert database.flyway("migrate", check=False).returncode != 0
        assert database.sql(history_sql).stdout == history
        assert (
            database.sql("""
            SELECT count(*) FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace
                WHERE n.nspname='runtime'
        """).stdout.strip()
            == "0"
        )
        assert (
            database.sql(
                "SELECT count(*) FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace WHERE n.nspname='runtime'"
            ).stdout.strip()
            == "0"
        )
        candidate.write_bytes(original)
        database.flyway("migrate")
        database.flyway("validate")
        assert database.sql(history_sql + " LIMIT 4").stdout == history
        assert rows.visible(child) == visible
        try:
            candidate.write_bytes(original + b"\nSELECT 12345;\n")
            failed = database.flyway("validate", check=False)
            assert (
                failed.returncode != 0
                and "checksum" in (failed.stdout + failed.stderr).lower()
            )
            assert database.flyway("migrate", check=False).returncode != 0
        finally:
            candidate.write_bytes(original)
        database.flyway("validate")
    finally:
        database.cleanup()


def test_t07_active_run_uniqueness_competing_transactions(runtime_rows):
    rows = runtime_rows
    assert rows.actor(rows.run_sql(str(uuid.uuid7())), check=False).returncode != 0
    rows.actor(rows.finish_sql(), role="worker")
    barrier = threading.Barrier(2)

    def submit():
        barrier.wait(timeout=15)
        return rows.actor(rows.run_sql(str(uuid.uuid7())), check=False)

    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
        results = [
            future.result() for future in [pool.submit(submit), pool.submit(submit)]
        ]
    assert sorted(result.returncode == 0 for result in results) == [False, True]
    assert "unique constraint" in next(
        result.stderr for result in results if result.returncode
    )


def test_t07_standalone_parallel_runs_and_protected_input(runtime_rows):
    rows = runtime_rows
    before = rows.actor(
        "SELECT count(*) FROM archive.messages; SELECT count(*) FROM archive.conversations"
    ).stdout
    standalone = str(uuid.uuid7())
    rows.actor(
        rows.run_sql(
            standalone,
            "standalone",
            ",input_text",
            ",'synthetic prompt'",
        )
        + ";"
        + rows.run_sql(str(uuid.uuid7()), "standalone")
    )
    assert (
        rows.actor(
            f"SELECT input_text='synthetic prompt' FROM runtime.model_runs WHERE run_id='{standalone}'"
        ).stdout.strip()
        == "t"
    )
    rows.actor(rows.running_sql(standalone), role="worker")
    # Use one timestamp for exact completed_at/expiry, as a future finalizer must.
    rows.actor(
        f"""
        UPDATE runtime.model_runs SET status='completed',completed_at=CURRENT_TIMESTAMP,
            execution_token=NULL,lease_expires_at=NULL,final_text='synthetic result',
            result_expires_at=CURRENT_TIMESTAMP+interval '168 hours' WHERE run_id='{standalone}'
    """,
        role="worker",
    )
    assert (
        rows.actor(
            "SELECT count(*) FROM archive.messages; SELECT count(*) FROM archive.conversations"
        ).stdout
        == before
    )
    assert (
        rows.actor("SELECT count(*) FROM runtime.memory_ingestions").stdout.strip()
        == "0"
    )
    assert (
        rows.actor(
            f"SELECT result_expires_at-completed_at FROM runtime.model_runs WHERE run_id='{standalone}'"
        ).stdout.strip()
        == "7 days"
    )


@pytest.mark.parametrize("characters,accepted", ((524288, True), (524289, False)))
def test_t07_standalone_input_utf8_limit(runtime_rows, characters, accepted):
    rows = runtime_rows
    run = str(uuid.uuid7())
    result = rows.actor(
        rows.run_sql(run, "standalone", ",input_text", f",repeat('é',{characters})"),
        check=False,
    )
    assert (result.returncode == 0) is accepted
    if accepted:
        assert (
            rows.actor(
                f"SELECT octet_length(convert_to(input_text,'UTF8')) FROM runtime.model_runs WHERE run_id='{run}'"
            ).stdout.strip()
            == "1048576"
        )
    else:
        assert (
            rows.actor(
                f"SELECT count(*) FROM runtime.model_runs WHERE run_id='{run}'"
            ).stdout.strip()
            == "0"
        )


@pytest.mark.parametrize("role", ("api", "worker"))
def test_t07_standalone_input_scope_and_runtime_write_denial(runtime_rows, role):
    rows = runtime_rows
    run = str(uuid.uuid7())
    rows.actor(rows.run_sql(run, "standalone", ",input_text", ",'synthetic prompt'"))
    other = RuntimeRows(rows.database)
    other.seed()
    query = f"SELECT count(*) FROM runtime.model_runs WHERE run_id='{run}' AND input_text IS NOT NULL"
    assert rows.actor(query, role=role).stdout.strip() == "1"
    assert other.actor(query, role=role).stdout.strip() == "0"
    # Reuse the same real connection across COMMIT; no prompt text is emitted.
    assert rows.database.sql(
        f"BEGIN; SET LOCAL app.current_user_id='{rows.user}'; {query}; COMMIT; {query}",
        f"mimir_{role}",
    ).stdout.splitlines() == ["1", "0"]
    assert (
        rows.actor(
            f"UPDATE runtime.model_runs SET input_text='changed' WHERE run_id='{run}'",
            role=role,
            check=False,
        ).returncode
        != 0
    )
    assert rows.database.sql("""
        SELECT column_name,data_type FROM information_schema.columns
        WHERE table_schema='runtime' AND table_name='model_runs'
            AND column_name IN ('input_text','input_ciphertext','input_key_id')
        ORDER BY column_name
    """).stdout.splitlines() == ["input_text|text"]


@pytest.mark.parametrize(
    "fault",
    (
        "kind",
        "missing_input",
        "standalone_conversation",
        "wrong_conversation",
        "assistant_input",
        "standalone_input_on_conversation",
        "initial_terminal",
    ),
)
def test_t07_invalid_run_shapes_and_message_roles(runtime_rows, fault):
    rows = runtime_rows
    rows.actor(rows.finish_sql(), role="worker")
    sql = rows.run_sql(str(uuid.uuid7()))
    if fault == "kind":
        sql = sql.replace("'conversation'", "'unknown'")
    elif fault == "missing_input":
        sql = sql.replace(f"'{rows.message}'", "NULL")
    elif fault == "standalone_conversation":
        sql = sql.replace("'conversation'", "'standalone'")
    elif fault == "wrong_conversation":
        other = str(uuid.uuid7())
        rows.actor(
            f"INSERT INTO archive.conversations(conversation_id,user_id,title) VALUES ('{other}','{rows.user}','synthetic')"
        )
        sql = sql.replace(f"'{rows.conversation}'", f"'{other}'")
    elif fault == "assistant_input":
        message = str(uuid.uuid7())
        rows.actor(rows.message_sql(2, message=message, role="assistant"))
        sql = sql.replace(f"'{rows.message}'", f"'{message}'")
    elif fault == "standalone_input_on_conversation":
        sql = rows.run_sql(
            str(uuid.uuid7()),
            extra_columns=",input_text",
            extra_values=",'synthetic prompt'",
        )
    else:
        sql = rows.run_sql(
            str(uuid.uuid7()), extra_columns=",status", extra_values=",'completed'"
        )
    assert rows.actor(sql, check=False).returncode != 0


@pytest.mark.parametrize(
    "fault",
    (
        "unclaimed",
        "skip_running",
        "sequence",
        "new_token_same_generation",
        "backwards_state",
        "no_assistant",
        "standalone_expiry",
    ),
)
def test_t07_run_state_checkpoint_and_fencing_constraints(runtime_rows, fault):
    rows = runtime_rows
    if fault in ("unclaimed", "skip_running"):
        sql = (
            "status='running'"
            if fault == "unclaimed"
            else "status='completed',completed_at=CURRENT_TIMESTAMP"
        )
    else:
        rows.actor(rows.running_sql(), role="worker")
        rows.actor(
            f"UPDATE runtime.model_runs SET checkpoint_text='synthetic partial',checkpoint_sequence=1 WHERE run_id='{rows.run}'",
            role="worker",
        )
        sql = {
            "sequence": "checkpoint_text='changed',checkpoint_sequence=1",
            "new_token_same_generation": f"execution_token='{uuid.uuid4()}'",
            "backwards_state": "status='pending',execution_token=NULL,lease_expires_at=NULL",
            "no_assistant": "status='completed',completed_at=CURRENT_TIMESTAMP,execution_token=NULL,lease_expires_at=NULL",
            "standalone_expiry": "result_expires_at=CURRENT_TIMESTAMP",
        }[fault]
    assert (
        rows.actor(
            f"UPDATE runtime.model_runs SET {sql} WHERE run_id='{rows.run}'",
            role="worker",
            check=False,
        ).returncode
        != 0
    )


def test_t07_successful_pair_ingestion_atomicity_and_deduplication(runtime_rows):
    rows = runtime_rows
    rows.actor(rows.running_sql(), role="worker")
    success, revision = rows.success_sql()
    failed = rows.actor(
        success + ";" + rows.ingestion_sql(revision) + ";SELECT 1/0",
        role="worker",
        check=False,
    )
    assert failed.returncode != 0
    assert rows.actor(
        f"SELECT status FROM runtime.model_runs WHERE run_id='{rows.run}'; SELECT count(*) FROM archive.messages WHERE conversation_id='{rows.conversation}'"
    ).stdout.splitlines() == ["running", "1"]
    rows.actor(success + ";" + rows.ingestion_sql(revision), role="worker")
    duplicate_job = str(uuid.uuid7())
    rows.actor(rows.job_sql(duplicate_job))
    assert (
        rows.actor(
            rows.ingestion_sql(revision, duplicate_job), role="worker", check=False
        ).returncode
        != 0
    )
    assert (
        rows.actor(rows.finish_sql("failed"), role="worker", check=False).returncode
        != 0
    )
    assert (
        rows.actor(
            f"UPDATE runtime.model_runs SET checkpoint_text='rewrite',checkpoint_sequence=1 WHERE run_id='{rows.run}'",
            role="worker",
            check=False,
        ).returncode
        != 0
    )


@pytest.mark.parametrize("fault", ("wrong_pair", "wrong_job_type"))
def test_t07_ingestion_cannot_bypass_exact_pair_or_serial_job_type(runtime_rows, fault):
    rows = runtime_rows
    rows.actor(rows.running_sql(), role="worker")
    success, revision = rows.success_sql()
    rows.actor(success, role="worker")
    if fault == "wrong_pair":
        sql = rows.ingestion_sql(rows.revision)
    else:
        job = str(uuid.uuid7())
        rows.actor(rows.job_sql(job, "synthetic"))
        sql = rows.ingestion_sql(revision, job)
    result = rows.actor(sql, role="worker", check=False)
    assert result.returncode != 0 and "ingestion requires" in result.stderr


@pytest.mark.parametrize(
    "outcome", ("pending", "cancelled", "failed", "interrupted", "standalone")
)
def test_t07_ineligible_ingestion_sources_rejected(runtime_rows, outcome):
    rows = runtime_rows
    if outcome == "standalone":
        rows.actor(rows.finish_sql(), role="worker")
        rows.run = str(uuid.uuid7())
        rows.actor(rows.run_sql(kind="standalone"))
    elif outcome != "pending":
        rows.actor(rows.finish_sql(outcome), role="worker")
    assert (
        rows.actor(
            rows.ingestion_sql(rows.revision), role="worker", check=False
        ).returncode
        != 0
    )


def test_t07_attempt_identity_usage_and_terminal_evidence(runtime_rows):
    rows = runtime_rows
    rows.actor(rows.running_sql(), role="worker")
    attempt = str(uuid.uuid7())
    insert = f"""
        INSERT INTO runtime.model_run_attempts(attempt_id,user_id,run_id,attempt_number,
            execution_generation,execution_token,provider_config_id) VALUES
            ('{attempt}','{rows.user}','{rows.run}',1,1,'{rows.token}','synthetic-provider')
    """
    rows.actor(insert, role="worker")
    assert (
        rows.actor(
            insert.replace(attempt, str(uuid.uuid7())).replace(",1,1,", ",2,1,"),
            role="worker",
            check=False,
        ).returncode
        != 0
    )
    assert (
        rows.actor(
            f"UPDATE runtime.model_run_attempts SET output_tokens=-1 WHERE attempt_id='{attempt}'",
            role="worker",
            check=False,
        ).returncode
        != 0
    )
    rows.actor(
        f"UPDATE runtime.model_run_attempts SET output_visible=true,outcome='interrupted',completed_at=CURRENT_TIMESTAMP WHERE attempt_id='{attempt}'",
        role="worker",
    )
    assert (
        rows.actor(
            f"UPDATE runtime.model_run_attempts SET outcome='completed' WHERE attempt_id='{attempt}'",
            role="worker",
            check=False,
        ).returncode
        != 0
    )


def test_t07_competing_skip_locked_claims_and_stale_job_token(runtime_rows):
    rows = runtime_rows
    rows.job = str(uuid.uuid7())
    second_job = str(uuid.uuid7())
    rows.actor(
        rows.job_sql(job_type="synthetic") + ";" + rows.job_sql(second_job, "synthetic")
    )
    barrier = threading.Barrier(2)

    def claim():
        barrier.wait(timeout=15)
        # Hold row locks long enough for the competing worker to skip them.
        return rows.actor(
            rows.claim_sql(str(uuid.uuid4()), "AND job_type='synthetic'")
            + ";SELECT pg_sleep(0.2)",
            role="worker",
        ).stdout.strip()

    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
        results = [
            future.result() for future in [pool.submit(claim), pool.submit(claim)]
        ]
    assert {result.splitlines()[0] for result in results} == {rows.job, second_job}
    old_token = rows.actor(
        f"SELECT lease_token FROM runtime.jobs WHERE job_id='{rows.job}'"
    ).stdout.strip()
    rows.actor(
        f"""
        UPDATE runtime.jobs SET heartbeat_at=CURRENT_TIMESTAMP-interval '3 minutes',
            lease_expires_at=CURRENT_TIMESTAMP-interval '1 minute' WHERE job_id='{rows.job}';
        UPDATE runtime.jobs SET status='reconciliation_required',lease_token=NULL,
            lease_expires_at=NULL,heartbeat_at=NULL
            WHERE job_id='{rows.job}' AND lease_expires_at < CURRENT_TIMESTAMP;
    """,
        role="worker",
    )
    # Unknown external outcomes cannot be directly reclaimed as retry_wait/running.
    assert (
        rows.actor(
            f"UPDATE runtime.jobs SET status='retry_wait' WHERE job_id='{rows.job}'",
            role="worker",
            check=False,
        ).returncode
        != 0
    )
    rows.actor(
        f"UPDATE runtime.jobs SET status='pending' WHERE job_id='{rows.job}'",
        role="worker",
    )
    rows.actor(rows.claim_sql(predicate=f"AND job_id='{rows.job}'"), role="worker")
    finish = f"""
        UPDATE runtime.jobs SET status='completed',completed_at=CURRENT_TIMESTAMP,
            lease_token=NULL,lease_expires_at=NULL,heartbeat_at=NULL
            WHERE job_id='{rows.job}' AND status='running' AND lease_token='{{token}}'
            RETURNING job_id
    """
    assert (
        rows.actor(finish.format(token=old_token), role="worker").stdout.strip() == ""
    )
    assert (
        rows.actor(finish.format(token=rows.token), role="worker").stdout.strip()
        == rows.job
    )
    assert (
        rows.actor(
            f"UPDATE runtime.jobs SET status='pending',completed_at=NULL WHERE job_id='{rows.job}'",
            role="worker",
            check=False,
        ).returncode
        != 0
    )


def test_t07_job_attempt_bound_and_retry_lease_shape(runtime_rows):
    rows = runtime_rows
    for count in range(1, 6):
        token = str(uuid.uuid4())
        rows.actor(rows.claim_sql(token), role="worker")
        assert rows.actor(
            f"SELECT attempt_count FROM runtime.jobs WHERE job_id='{rows.job}'"
        ).stdout.strip() == str(count)
        rows.actor(
            f"UPDATE runtime.jobs SET status='retry_wait',lease_token=NULL,lease_expires_at=NULL,heartbeat_at=NULL WHERE job_id='{rows.job}'",
            role="worker",
        )
    assert (
        rows.actor(
            rows.claim_sql(str(uuid.uuid4())), role="worker", check=False
        ).returncode
        != 0
    )
    assert (
        rows.actor(
            f"UPDATE runtime.jobs SET lease_token='{uuid.uuid4()}' WHERE job_id='{rows.job}'",
            role="worker",
            check=False,
        ).returncode
        != 0
    )


def test_t07_operation_replay_identity_race_and_retention(runtime_rows):
    rows = runtime_rows
    barrier = threading.Barrier(2)

    def accept():
        barrier.wait(timeout=15)
        return rows.actor(rows.operation_sql(), check=False)

    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
        results = [
            future.result() for future in [pool.submit(accept), pool.submit(accept)]
        ]
    assert sorted(result.returncode == 0 for result in results) == [False, True]
    assert (
        rows.actor(
            f"SELECT run_id,conversation_id,retained_until-accepted_at,tombstone_until-retained_until,resolved_at IS NULL FROM runtime.operation_ledger WHERE user_id='{rows.user}'"
        ).stdout.strip()
        == f"{rows.run}|{rows.conversation}|30 days|30 days|t"
    )
    for assignment in (
        "request_fingerprint=sha256('changed'::bytea)",
        "retained_until=retained_until+interval '1 day'",
        f"run_id='{uuid.uuid7()}'",
    ):
        assert (
            rows.owner(
                f"UPDATE runtime.operation_ledger SET {assignment} WHERE user_id='{rows.user}'",
                check=False,
            ).returncode
            != 0
        )
    # Replay can query the same fingerprint/identity without storing prompt bodies.
    assert (
        rows.actor(
            f"SELECT run_id FROM runtime.operation_ledger WHERE user_id='{rows.user}' AND request_fingerprint=sha256('synthetic fingerprint'::bytea)"
        ).stdout.strip()
        == rows.run
    )
    assert (
        rows.actor(
            f"SELECT run_id FROM runtime.operation_ledger WHERE user_id='{rows.user}' AND request_fingerprint=sha256('changed'::bytea)"
        ).stdout.strip()
        == ""
    )


@pytest.mark.parametrize(
    "fault",
    (
        "short_key",
        "long_key",
        "invalid_key",
        "unknown_type",
        "short_hash",
        "bad_retention",
        "wrong_run_kind",
    ),
)
def test_t07_operation_identity_boundaries(runtime_rows, fault):
    rows = runtime_rows
    sql = rows.operation_sql()
    if fault == "short_key":
        sql = sql.replace("synthetic-key", "short")
    elif fault == "long_key":
        sql = sql.replace("synthetic-key", "x" * 129)
    elif fault == "invalid_key":
        sql = sql.replace("synthetic-key", "invalid key")
    elif fault == "unknown_type":
        sql = sql.replace("message_submit", "unknown")
    elif fault == "short_hash":
        sql = sql.replace("sha256('synthetic fingerprint'::bytea)", "'short'::bytea")
    elif fault == "bad_retention":
        sql = sql.replace("720 hours", "721 hours")
    else:
        sql = rows.operation_sql(kind="standalone_submit")
    assert rows.actor(sql, check=False).returncode != 0


@pytest.mark.parametrize("role", ("api", "worker"))
def test_t07_all_operational_tables_rls_context_and_owner_integrity(runtime_rows, role):
    rows = runtime_rows
    other = RuntimeRows(rows.database)
    other.seed()
    other.actor(other.run_sql() + ";" + other.job_sql())
    rows.actor(rows.operation_sql())
    rows.owner(
        f"INSERT INTO runtime.deletion_controls(deletion_id,user_id) VALUES ('{uuid.uuid7()}','{rows.user}')"
    )
    rows.actor(rows.running_sql(), role="worker")
    success, revision = rows.success_sql()
    rows.actor(success + ";" + rows.ingestion_sql(revision), role="worker")
    rows.actor(
        f"""
        INSERT INTO runtime.model_run_attempts(attempt_id,user_id,run_id,attempt_number,
            execution_generation,execution_token,provider_config_id) VALUES
            ('{uuid.uuid7()}','{rows.user}','{rows.run}',1,1,'{rows.token}','synthetic');
        INSERT INTO runtime.outbox_events(event_id,user_id,event_type,event_version,payload)
            VALUES ('{uuid.uuid7()}','{rows.user}','synthetic',1,'{{}}');
        INSERT INTO runtime.context_assembly_records(context_id,user_id,run_id,policy_version,
            model_policy_version,estimated_input_tokens,reserved_output_tokens,budget_mode)
            VALUES ('{uuid.uuid7()}','{rows.user}','{rows.run}','synthetic','synthetic',1,1,'synthetic')
    """,
        role="worker",
    )
    for table in RUNTIME_TABLES:
        assert (
            int(
                rows.actor(
                    f"SELECT count(*) FROM runtime.{table}", role=role
                ).stdout.strip()
            )
            >= 1
        )
        assert (
            other.actor(
                f"SELECT count(*) FROM runtime.{table} WHERE user_id='{rows.user}'",
                role=role,
            ).stdout.strip()
            == "0"
        )
        assert (
            rows.database.sql(
                f"BEGIN; SELECT count(*) FROM runtime.{table}; COMMIT", f"mimir_{role}"
            ).stdout.strip()
            == "0"
        )
        assert (
            rows.database.sql(
                f"BEGIN; SET LOCAL row_security=off; SELECT * FROM runtime.{table}; COMMIT",
                f"mimir_{role}",
                check=False,
            ).returncode
            != 0
        )
    # Same-user actor with a foreign user's run/job references is rejected too.
    assert (
        rows.actor(
            rows.operation_sql(key="foreign-key", run=other.run), check=False
        ).returncode
        != 0
    )
    assert (
        rows.actor(
            rows.ingestion_sql(revision, job=other.job), role="worker", check=False
        ).returncode
        != 0
    )
    forged = rows.job_sql(str(uuid.uuid7())).replace(rows.user, other.user)
    assert rows.actor(forged, role=role, check=False).returncode != 0
    assert rows.database.sql(
        f"BEGIN; SET LOCAL app.current_user_id='{rows.user}'; SELECT count(*) FROM runtime.jobs; COMMIT; SELECT count(*) FROM runtime.jobs",
        f"mimir_{role}",
    ).stdout.splitlines() == ["1", "0"]


@pytest.mark.parametrize("role", ("api", "worker", "auth", "maintenance"))
def test_t07_least_privilege_and_no_unrestricted_purge(runtime_rows, role):
    rows = runtime_rows
    for table in RUNTIME_TABLES:
        for operation in (
            f"DELETE FROM runtime.{table}",
            f"TRUNCATE runtime.{table}",
            f"ALTER TABLE runtime.{table} DISABLE ROW LEVEL SECURITY",
        ):
            assert rows.actor(operation, role=role, check=False).returncode != 0
    if role in ("auth", "maintenance"):
        for table in RUNTIME_TABLES:
            assert (
                rows.actor(
                    f"SELECT * FROM runtime.{table}", role=role, check=False
                ).returncode
                != 0
            )
    if role == "api":
        assert rows.actor(rows.running_sql(), check=False).returncode != 0
    assert (
        rows.actor(
            f"INSERT INTO runtime.deletion_controls(deletion_id,user_id) VALUES ('{uuid.uuid7()}','{rows.user}')",
            role=role,
            check=False,
        ).returncode
        != 0
    )
    for routine in (
        "guard_run",
        "guard_job",
        "guard_attempt",
        "guard_operation",
        "require_ingestion_source",
    ):
        assert (
            rows.actor(f"SELECT runtime.{routine}()", role=role, check=False).returncode
            != 0
        )


def test_t07_standalone_expiry_preserves_ledger_identity_and_fails_closed(runtime_rows):
    rows = runtime_rows
    run = str(uuid.uuid7())
    rows.actor(
        rows.run_sql(
            run,
            "standalone",
            ",created_at,input_text",
            ",CURRENT_TIMESTAMP-interval '9 days','synthetic prompt'",
        )
    )
    rows.actor(rows.operation_sql(run=run, kind="standalone_submit"))
    rows.actor(rows.running_sql(run), role="worker")
    rows.actor(
        f"""
        UPDATE runtime.model_runs SET status='completed',completed_at=CURRENT_TIMESTAMP-interval '8 days',
            result_expires_at=CURRENT_TIMESTAMP-interval '1 day',final_text='synthetic result',
            execution_token=NULL,lease_expires_at=NULL WHERE run_id='{run}'
    """,
        role="worker",
    )
    purge = f"""
        UPDATE runtime.model_runs SET artifacts_purged_at=CURRENT_TIMESTAMP,
            input_text=NULL,checkpoint_text='',checkpoint_sequence=0,
            final_text=NULL WHERE run_id='{run}'
    """
    assert rows.actor(purge, role="worker", check=False).returncode != 0
    assert (
        rows.owner(purge.replace("input_text=NULL,", ""), check=False).returncode != 0
    )
    rows.owner(purge)
    assert (
        rows.actor(
            f"SELECT run_id,status,final_text IS NULL,input_text IS NULL FROM runtime.model_runs WHERE run_id='{run}'"
        ).stdout.strip()
        == f"{run}|completed|t|t"
    )
    assert (
        rows.actor(
            f"SELECT run_id FROM runtime.operation_ledger WHERE user_id='{rows.user}'"
        ).stdout.strip()
        == run
    )
    assert (
        rows.owner(
            f"UPDATE runtime.model_runs SET final_text='resurrected' WHERE run_id='{run}'",
            check=False,
        ).returncode
        != 0
    )


def test_t07_operation_evidence_outlives_owned_source_deletion(runtime_rows):
    rows = runtime_rows
    run = str(uuid.uuid7())
    rows.actor(rows.run_sql(run, "standalone"))
    rows.actor(rows.operation_sql(run=run, kind="standalone_submit"))
    # Privileged storage probe only. No application purge is implemented here.
    rows.owner(f"DELETE FROM runtime.model_runs WHERE run_id='{run}'")
    assert (
        rows.actor(
            f"SELECT run_id FROM runtime.operation_ledger WHERE user_id='{rows.user}'"
        ).stdout.strip()
        == run
    )
    assert (
        rows.actor(
            rows.operation_sql(key="invented-key", run=run, kind="standalone_submit"),
            check=False,
        ).returncode
        != 0
    )


@pytest.mark.parametrize("kind", ("conversation_create", "branch_create"))
def test_t07_created_resource_operation_identity_survives_archive_delete(
    runtime_rows, kind
):
    rows = BranchRows(runtime_rows.database)
    rows.seed()
    conversation = (
        rows.conversation
        if kind == "conversation_create"
        else rows.create_branch(mode="replace_message")
    )
    sql = f"""
        INSERT INTO runtime.operation_ledger(user_id,operation_type,idempotency_key,
            request_fingerprint,conversation_id,conversation_version,retained_until,tombstone_until)
            VALUES ('{rows.user}','{kind}','synthetic-key',sha256('synthetic'::bytea),
                '{conversation}',1,CURRENT_TIMESTAMP+interval '720 hours',CURRENT_TIMESTAMP+interval '1440 hours')
    """
    rows.actor(sql)
    if kind == "conversation_create":
        rows.owner(
            f"DELETE FROM archive.message_revisions WHERE user_id='{rows.user}'; DELETE FROM archive.messages WHERE user_id='{rows.user}'"
        )
    rows.owner(
        f"DELETE FROM archive.conversations WHERE conversation_id='{conversation}'"
    )
    assert (
        rows.actor(
            f"SELECT conversation_id FROM runtime.operation_ledger WHERE user_id='{rows.user}'"
        ).stdout.strip()
        == conversation
    )
    assert (
        rows.actor(sql.replace("synthetic-key", "invented-key"), check=False).returncode
        != 0
    )


def test_t07_memory_ingestion_serial_per_owner_cross_owner_parallel(runtime_rows):
    rows = runtime_rows
    rows.actor(rows.job_sql(str(uuid.uuid7())))
    barrier = threading.Barrier(2)

    def claim():
        barrier.wait(timeout=15)
        return rows.actor(rows.claim_sql(str(uuid.uuid4())), role="worker", check=False)

    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
        results = [
            future.result() for future in [pool.submit(claim), pool.submit(claim)]
        ]
    assert sorted(result.returncode == 0 for result in results) == [False, True]
    assert "unique constraint" in next(
        result.stderr for result in results if result.returncode
    )
    other = RuntimeRows(rows.database)
    other.seed()
    other.actor(other.job_sql())
    assert other.actor(other.claim_sql(), role="worker").stdout.strip() == other.job


def test_t07_stale_executor_token_and_terminal_race(runtime_rows):
    rows = runtime_rows
    rows.actor(rows.running_sql(), role="worker")
    replacement = str(uuid.uuid4())
    rows.actor(rows.running_sql(token=replacement, generation=2), role="worker")
    assert (
        rows.actor(
            f"""
        UPDATE runtime.model_runs SET checkpoint_text='stale',checkpoint_sequence=1
            WHERE run_id='{rows.run}' AND status='running' AND execution_token='{rows.token}'
                AND execution_generation=1 RETURNING run_id
    """,
            role="worker",
        ).stdout.strip()
        == ""
    )
    barrier = threading.Barrier(2)

    def finish(status):
        barrier.wait(timeout=15)
        return rows.actor(
            f"""
            UPDATE runtime.model_runs SET status='{status}',completed_at=CURRENT_TIMESTAMP,
                execution_token=NULL,lease_expires_at=NULL
                WHERE run_id='{rows.run}' AND status='running'
                    AND execution_token='{replacement}' AND execution_generation=2 RETURNING status
        """,
            role="worker",
        ).stdout.strip()

    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
        results = [
            future.result()
            for future in [
                pool.submit(finish, "failed"),
                pool.submit(finish, "cancelled"),
            ]
        ]
    assert results.count("") == 1
    assert (set(results) - {""}) <= {"failed", "cancelled"}


def test_t07_standalone_final_utf8_limit(runtime_rows):
    rows = runtime_rows
    run = str(uuid.uuid7())
    rows.actor(rows.run_sql(run, "standalone"))
    rows.actor(rows.running_sql(run), role="worker")
    finish = f"""
        UPDATE runtime.model_runs SET status='completed',completed_at=CURRENT_TIMESTAMP,
            execution_token=NULL,lease_expires_at=NULL,final_text=repeat('é',{{count}}),
            result_expires_at=CURRENT_TIMESTAMP+interval '168 hours' WHERE run_id='{run}'
    """
    assert (
        rows.actor(finish.format(count=524289), role="worker", check=False).returncode
        != 0
    )
    rows.actor(finish.format(count=524288), role="worker")
    assert (
        rows.actor(
            f"SELECT octet_length(convert_to(final_text,'UTF8')) FROM runtime.model_runs WHERE run_id='{run}'"
        ).stdout.strip()
        == "1048576"
    )


def test_t07_unresolved_operation_evidence_survives_cleanup_boundary(runtime_rows):
    rows = runtime_rows
    # Probe the future cleanup predicate; no deletion/cleanup worker is installed.
    sql = rows.operation_sql().replace(
        "retained_until,tombstone_until", "accepted_at,retained_until,tombstone_until"
    )
    sql = sql.replace(
        "CURRENT_TIMESTAMP+interval '720 hours'",
        "CURRENT_TIMESTAMP-interval '61 days',CURRENT_TIMESTAMP-interval '31 days'",
    )
    sql = sql.replace(
        "CURRENT_TIMESTAMP+interval '1440 hours'", "CURRENT_TIMESTAMP-interval '1 day'"
    )
    rows.actor(sql)
    eligible = f"SELECT count(*) FROM runtime.operation_ledger WHERE user_id='{rows.user}' AND resolved_at IS NOT NULL AND tombstone_until<CURRENT_TIMESTAMP"
    assert rows.actor(eligible).stdout.strip() == "0"
    rows.actor(
        f"UPDATE runtime.operation_ledger SET status='cancelled',resolved_at=CURRENT_TIMESTAMP WHERE user_id='{rows.user}'",
        role="worker",
    )
    assert rows.actor(eligible).stdout.strip() == "1"
    assert (
        rows.actor(
            f"UPDATE runtime.operation_ledger SET status='accepted',resolved_at=NULL WHERE user_id='{rows.user}'",
            role="worker",
            check=False,
        ).returncode
        != 0
    )


def test_t07_deletion_control_metadata_requires_fence_and_verified_state_sequence(
    runtime_rows,
):
    rows = runtime_rows
    deletion = str(uuid.uuid7())
    rows.owner(
        f"INSERT INTO runtime.deletion_controls(deletion_id,user_id) VALUES ('{deletion}','{rows.user}')"
    )
    update = f"UPDATE runtime.deletion_controls SET {{assignment}} WHERE deletion_id='{deletion}'"
    assert (
        rows.owner(update.format(assignment="status='purging'"), check=False).returncode
        != 0
    )
    assert (
        rows.owner(update.format(assignment="status='fenced'"), check=False).returncode
        != 0
    )
    rows.owner(
        update.format(
            assignment=f"status='fenced',fence_token='{uuid.uuid4()}',fenced_at=CURRENT_TIMESTAMP,journal_reference='synthetic-journal-reference'"
        )
    )
    assert (
        rows.owner(
            update.format(assignment=f"fence_token='{uuid.uuid4()}'"), check=False
        ).returncode
        != 0
    )
    rows.owner(update.format(assignment="status='purging'"))
    rows.owner(update.format(assignment="status='verification_required'"))
    rows.owner(
        update.format(assignment="status='complete',completed_at=CURRENT_TIMESTAMP")
    )
    assert (
        rows.owner(
            update.format(assignment="status='purging',completed_at=NULL"), check=False
        ).returncode
        != 0
    )


# T08 uses the actual services/SQLAlchemy adapters against the private test DB.
# Linux Docker bridge IPs are reachable from this test host; no published DB port
# or production topology change is necessary. CI's real-engine gate is Linux.
V006 = "V006__restricted_auth_operations.sql"


@pytest.fixture(scope="module")
def session_database(tmp_path_factory):
    stack = DatabaseStack(
        tmp_path_factory.mktemp("session-database"), through_version=6
    )
    try:
        stack.start()
        stack.migrate()
        yield stack
    finally:
        stack.cleanup()


class SessionServices:
    def __init__(self, stack):
        self.stack = stack
        info = json.loads(stack.compose("ps", "--format", "json").stdout)
        # Compose emits one JSON object for the sole long-running service.
        if isinstance(info, list):
            info = info[0]
        container = json.loads(stack.docker("inspect", info["ID"]).stdout)[0]
        networks = container["NetworkSettings"]["Networks"]
        self.host = next(iter(networks.values()))["IPAddress"]
        self.auth_database = self.database("mimir_auth")
        self.maintenance_database = self.database("mimir_maintenance")
        self.repository = PostgresAuthRepository(self.auth_database)
        self.passwords = PasswordService()
        self.tokens = SessionTokens()
        self.auth = AuthService(
            self.repository, self.passwords, self.tokens, SystemClock()
        )
        self.admin = ProvisioningService(
            PostgresProvisioningRepository(self.maintenance_database), self.passwords
        )
        self.username = f"synthetic_t08_{uuid.uuid7()}"
        self.password = "synthetic T08 test password"
        self.user = None

    def database(self, role):
        return AuthDatabase(
            URL.create(
                "postgresql+psycopg",
                username=role,
                password=self.stack.passwords[role].read_text().strip(),
                host=self.host,
                database="mimir",
            )
        )

    async def seed(self):
        self.user = await self.admin.provision(self.username, self.password)

    async def login(self):
        return await self.auth.login(self.username, self.password, ClientType.CLI)

    async def close(self):
        await self.auth_database.close()
        await self.maintenance_database.close()

    def seed_session(
        self, *, created="-1 day", used="-1 day", idle="+1 day", absolute="+89 days"
    ):
        token = self.tokens.generate()
        key = self.tokens.digest(token).hex()
        self.stack.sql(f"""
            INSERT INTO auth.sessions(token_hash,user_id,identity_id,client_type,
                created_at,last_used_at,expires_at,absolute_expires_at)
            SELECT decode('{key}','hex'),user_id,identity_id,'cli',
                now()+interval '{created}',now()+interval '{used}',
                now()+interval '{idle}',now()+interval '{absolute}'
            FROM auth.identities WHERE user_id='{self.user}'
        """)
        return token

    async def wait_for_auth_lock(self, minimum=1):
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            count = int(
                self.stack.sql("""
                SELECT count(*) FROM pg_stat_activity
                WHERE usename='mimir_auth' AND wait_event_type='Lock'
            """).stdout.strip()
            )
            if count >= minimum:
                return
            await asyncio.sleep(0.02)
        raise AssertionError("Auth callers did not reach the account lock")


@pytest_asyncio.fixture
async def session_services(session_database):
    services = SessionServices(session_database)
    try:
        await services.seed()
        yield services
    finally:
        await services.close()


def test_t08_fresh_repeat_restart_validate_and_routine_catalog(session_database):
    database = session_database
    sql = "SELECT version,checksum,success FROM public.flyway_schema_history ORDER BY installed_rank"
    before = database.sql(sql).stdout
    assert [line.split("|")[0] for line in before.splitlines()] == [
        "001",
        "002",
        "003",
        "004",
        "005",
        "006",
    ]
    database.migrate()
    database.flyway("validate")
    database.compose("restart", "postgres")
    database.compose("up", "-d", "--wait", "postgres")
    database.flyway("validate")
    assert database.sql(sql).stdout == before
    assert (
        database.sql("""
        SELECT count(*) FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace
        WHERE (n.nspname,c.relname) IN
            (('archive','users'),('auth','identities'),('auth','sessions'))
            AND c.relrowsecurity AND c.relforcerowsecurity
            AND pg_get_userbyid(c.relowner)='mimir_owner'
    """).stdout.strip()
        == "3"
    )
    assert (
        database.sql("""
        SELECT count(*) FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace
        WHERE n.nspname='auth' AND p.proname IN
            ('provision_user','disable_account','issue_session','renew_session','revoke_session')
            AND p.prosecdef AND pg_get_userbyid(p.proowner)='mimir_owner'
            AND p.proconfig @> ARRAY['search_path=pg_catalog, pg_temp','row_security=on']
            AND NOT EXISTS (SELECT FROM aclexplode(p.proacl) a WHERE a.grantee=0)
    """).stdout.strip()
        == "5"
    )


def test_t08_v005_upgrade_failure_rollback_recovery_and_drift(tmp_path):
    database = DatabaseStack(tmp_path, through_version=5)
    try:
        database.start()
        database.migrate()
        rows = RuntimeRows(database)
        rows.seed()
        rows.actor(rows.run_sql())
        legacy = AuthRows(database)
        legacy.seed()
        legacy_sql = f"SELECT user_id,identity_id,absolute_expires_at FROM auth.sessions WHERE token_hash=decode('{legacy.hash}','hex')"
        legacy_before = database.sql(legacy_sql).stdout
        history_sql = "SELECT version,checksum FROM public.flyway_schema_history ORDER BY installed_rank"
        before = database.sql(history_sql).stdout
        candidate = database.migrations / V006
        original = (ROOT / "backend/migrations" / V006).read_bytes()
        candidate.write_bytes(original + b"\nSELECT 1/0;\n")
        assert database.flyway("migrate", check=False).returncode != 0
        assert database.sql(history_sql).stdout == before
        assert (
            database.sql(
                "SELECT to_regprocedure('auth.renew_session(bytea)') IS NULL"
            ).stdout.strip()
            == "t"
        )
        assert (
            database.sql(
                "SELECT count(*) FROM pg_proc WHERE proname='provision_user'"
            ).stdout.strip()
            == "0"
        )
        candidate.write_bytes(original)
        database.flyway("migrate")
        database.flyway("validate")
        assert database.sql(history_sql + " LIMIT 5").stdout == before
        assert database.sql(legacy_sql).stdout == legacy_before
        assert (
            database.sql(
                f"SELECT user_id FROM auth.renew_session(decode('{legacy.hash}','hex'))",
                "mimir_auth",
            ).stdout.strip()
            == legacy.user
        )
        assert (
            rows.actor(
                f"SELECT status FROM runtime.model_runs WHERE run_id='{rows.run}'"
            ).stdout.strip()
            == "pending"
        )
        try:
            candidate.write_bytes(original + b"\nSELECT 12345;\n")
            failed = database.flyway("validate", check=False)
            assert (
                failed.returncode != 0
                and "checksum" in (failed.stdout + failed.stderr).lower()
            )
            assert database.flyway("migrate", check=False).returncode != 0
        finally:
            candidate.write_bytes(original)
        database.flyway("validate")
    finally:
        database.cleanup()


@pytest.mark.asyncio
async def test_t08_provision_login_hash_only_exact_case_injection_and_rejection(
    session_services, caplog
):
    services = session_services
    assert services.user.version == 7
    issued = await services.login()
    assert issued.session.user_id == services.user
    key = services.tokens.digest(issued.token)
    row = services.stack.sql(f"""
        SELECT octet_length(token_hash),expires_at-s.created_at,absolute_expires_at-s.created_at,
            password_hash LIKE '$argon2id$v=19$%'
        FROM auth.sessions s JOIN auth.identities USING (user_id,identity_id)
        WHERE token_hash=decode('{key.hex()}','hex')
    """).stdout.strip()
    assert row == "32|30 days|90 days|t"
    assert issued.token not in repr(issued) and issued.token not in caplog.text
    # No normalization, and SQLAlchemy bind parameters preserve hostile-looking text.
    other_name = services.username.upper() + "'); SELECT 1; --"
    other = await services.admin.provision(other_name, services.password)
    assert other != services.user
    await services.auth.login(other_name, services.password, ClientType.WEB)
    with pytest.raises(AccountAlreadyExists):
        await services.admin.provision(services.username, services.password)
    assert (
        services.stack.sql(
            f"SELECT count(*) FROM archive.users WHERE user_id='{services.user}'"
        ).stdout.strip()
        == "1"
    )
    for username, password in (
        (services.username.upper(), services.password),
        (services.username, "wrong synthetic password"),
        ("unknown synthetic username", services.password),
    ):
        with pytest.raises(AuthenticationRejected):
            await services.auth.login(username, password, ClientType.CLI)
    assert (
        services.stack.sql(
            f"SELECT count(*) FROM auth.sessions WHERE user_id='{services.user}'"
        ).stdout.strip()
        == "1"
    )
    identity = await services.repository.lookup_identity(services.username)
    assert identity is not None
    assert not await services.repository.issue_session(
        replace(identity, password_hash="stale synthetic verifier"),
        services.tokens.digest(services.tokens.generate()),
        ClientType.CLI,
    )


@pytest.mark.asyncio
async def test_t08_competing_provision_is_atomic_without_orphan_accounts(
    session_services,
):
    services = session_services
    before = services.stack.sql("SELECT count(*) FROM archive.users").stdout
    username = f"synthetic_duplicate_{uuid.uuid7()}"
    results = await asyncio.gather(
        services.admin.provision(username, services.password),
        services.admin.provision(username, services.password),
        return_exceptions=True,
    )
    assert sum(isinstance(value, uuid.UUID) for value in results) == 1
    assert sum(isinstance(value, AccountAlreadyExists) for value in results) == 1
    assert (
        int(services.stack.sql("SELECT count(*) FROM archive.users").stdout)
        == int(before) + 1
    )


@pytest.mark.asyncio
async def test_t08_concurrent_renewal_keeps_token_and_monotonic_lifetime(
    session_services,
):
    services = session_services
    token = services.seed_session(
        created="-20 days", used="-2 days", idle="+10 days", absolute="+70 days"
    )
    first, second = await asyncio.gather(
        services.auth.authenticate(token), services.auth.authenticate(token)
    )
    assert first.user_id == second.user_id == services.user
    assert max(first.expires_at, second.expires_at) > datetime.now(UTC) + timedelta(
        days=29
    )
    assert first.absolute_expires_at == second.absolute_expires_at
    assert (
        services.stack.sql(
            f"SELECT count(*) FROM auth.sessions WHERE user_id='{services.user}'"
        ).stdout.strip()
        == "1"
    )
    identity = await services.repository.lookup_identity(services.username)
    assert identity is not None
    # A separate session remains valid when the first session logs out.
    other = await services.login()
    await services.auth.logout(token)
    await services.auth.logout(token)
    with pytest.raises(AuthenticationRejected):
        await services.auth.authenticate(token)
    assert (await services.auth.authenticate(other.token)).user_id == services.user


@pytest.mark.asyncio
async def test_t08_expiry_cap_and_never_reactivate_expired_or_revoked(session_services):
    services = session_services
    capped = services.seed_session(
        created="-89 days", used="-1 day", idle="+12 hours", absolute="+1 day"
    )
    renewed = await services.auth.authenticate(capped)
    assert renewed.expires_at == renewed.absolute_expires_at
    for kwargs in (
        {"idle": "0 seconds"},
        {"idle": "-1 hour", "absolute": "0 seconds"},
    ):
        token = services.seed_session(**kwargs)
        key = services.tokens.digest(token)
        before = await services.repository.lookup_session(key)
        assert before is not None
        with pytest.raises(AuthenticationRejected):
            await services.auth.authenticate(token)
        assert await services.repository.renew_session(key) is None
        assert await services.repository.lookup_session(key) == before
    await services.auth.logout(capped)
    assert (
        await services.repository.renew_session(services.tokens.digest(capped)) is None
    )
    await services.auth.logout(services.tokens.generate())


@pytest.mark.asyncio
async def test_t08_disable_wins_waiting_issuance_and_renewal_races(session_services):
    services = session_services
    issued = await services.login()
    second = await services.login()
    other_name = services.username + "_other_owner"
    other_owner = await services.admin.provision(other_name, services.password)
    other = await services.auth.login(other_name, services.password, ClientType.WEB)
    # The real disable operation holds the account lock until commit. Other
    # connections see the old state for lookup, then block at their final check.
    async with services.maintenance_database.engine.begin() as connection:
        await connection.execute(
            text("SELECT auth.disable_account(:user)"), {"user": services.user}
        )
        login = asyncio.create_task(services.login())
        renewal = asyncio.create_task(services.auth.authenticate(issued.token))
        await services.wait_for_auth_lock(minimum=2)
    results = await asyncio.gather(login, renewal, return_exceptions=True)
    assert all(isinstance(result, AuthenticationRejected) for result in results)
    assert (
        services.stack.sql(
            f"SELECT count(*) FROM auth.sessions WHERE user_id='{services.user}' AND revoked_at IS NULL"
        ).stdout.strip()
        == "0"
    )
    assert await services.admin.disable(services.user)
    assert not await services.admin.disable(uuid.uuid7())
    with pytest.raises(AuthenticationRejected):
        await services.auth.authenticate(second.token)
    assert (await services.auth.authenticate(other.token)).user_id == other_owner
    with pytest.raises(AuthenticationRejected):
        await services.login()


@pytest.mark.asyncio
async def test_t08_revocation_wins_waiting_renewal(session_services):
    services = session_services
    issued = await services.login()
    async with services.auth_database.engine.begin() as connection:
        await connection.execute(
            text("SELECT auth.revoke_session(:hash)"),
            {"hash": services.tokens.digest(issued.token)},
        )
        renewal = asyncio.create_task(services.auth.authenticate(issued.token))
        await services.wait_for_auth_lock()
    with pytest.raises(AuthenticationRejected):
        await renewal


@pytest.mark.asyncio
async def test_t08_routines_restore_actor_on_pooled_commit_rollback_and_hostile_settings(
    session_services,
):
    services = session_services
    issued = await services.login()
    key = services.tokens.digest(issued.token)
    for end in ("commit", "rollback"):
        async with services.auth_database.engine.connect() as connection:
            transaction = await connection.begin()
            await connection.execute(
                text("SET LOCAL app.current_user_id='malformed-untrusted-context'")
            )
            await connection.execute(text("SET LOCAL row_security=off"))
            await connection.execute(text("SET LOCAL search_path=pg_temp"))
            await connection.execute(text("SET LOCAL timezone='America/New_York'"))
            row = (
                (
                    await connection.execute(
                        text("SELECT * FROM auth.renew_session(:hash)"), {"hash": key}
                    )
                )
                .mappings()
                .one()
            )
            assert row["user_id"] == services.user
            assert (
                await connection.execute(
                    text("SELECT current_setting('app.current_user_id')")
                )
            ).scalar_one() == "malformed-untrusted-context"
            await getattr(transaction, end)()
            context = (
                await connection.execute(
                    text("SELECT current_setting('app.current_user_id',true)")
                )
            ).scalar_one()
            assert context in ("", None)
    # Provision/disable also restore administrator context instead of leaking the
    # selected account to the next operation on the same pooled connection.
    async with services.maintenance_database.engine.begin() as connection:
        await connection.execute(
            text("SET LOCAL app.current_user_id='previous-admin-context'")
        )
        await connection.execute(
            text("SELECT auth.disable_account(:user)"), {"user": services.user}
        )
        assert (
            await connection.execute(
                text("SELECT current_setting('app.current_user_id')")
            )
        ).scalar_one() == "previous-admin-context"


@pytest.mark.parametrize("role", RUNTIME)
def test_t08_actual_role_least_privilege_and_execution_allowlist(
    session_database, role
):
    database = session_database
    for table in ("auth.identities", "auth.sessions"):
        for operation in ("SELECT * FROM", "DELETE FROM", "UPDATE"):
            sql = f"{operation} {table}"
            if operation == "UPDATE":
                sql += " SET user_id=user_id"
            result = database.sql(sql, f"mimir_{role}", check=False)
            assert result.returncode != 0 and "permission denied" in result.stderr
        assert (
            database.sql(
                f"INSERT INTO {table} DEFAULT VALUES", f"mimir_{role}", check=False
            ).returncode
            != 0
        )
    result = database.sql(
        "UPDATE archive.users SET status='active'", f"mimir_{role}", check=False
    )
    assert result.returncode != 0 and "permission denied" in result.stderr
    for name, arguments, allowed in (
        ("provision_user", "NULL,NULL,NULL,NULL,NULL", "maintenance"),
        ("disable_account", f"'{uuid.uuid7()}'::uuid", "maintenance"),
        ("issue_session", "NULL,NULL,NULL,NULL", "auth"),
        ("renew_session", "NULL::bytea", "auth"),
        ("revoke_session", "NULL::bytea", "auth"),
    ):
        if role == allowed:
            continue
        result = database.sql(
            f"SELECT auth.{name}({arguments})", f"mimir_{role}", check=False
        )
        assert result.returncode != 0 and "permission denied" in result.stderr
    assert (
        database.sql(
            f"SELECT rolsuper,rolbypassrls,pg_has_role('mimir_{role}','mimir_owner','SET') FROM pg_roles WHERE rolname='mimir_{role}'"
        ).stdout.strip()
        == "f|f|f"
    )


@pytest.mark.asyncio
async def test_t08_malformed_parameters_storage_failure_and_transaction_recovery(
    session_services,
):
    services = session_services
    # No successful principal is returned if persistence fails; the next pooled
    # operation must start cleanly after the failed transaction rolls back.
    identity = await services.repository.lookup_identity(services.username)
    assert identity is not None
    with pytest.raises(AuthStorageError):
        await services.repository.issue_session(identity, b"short", ClientType.CLI)
    assert await services.login()
    with pytest.raises(AuthStorageError):
        await PostgresProvisioningRepository(services.auth_database).disable(
            services.user
        )
    assert await services.login()


@pytest.mark.asyncio
async def test_t08_expiration_while_waiting_for_lock_cannot_renew(session_services):
    services = session_services
    token = services.seed_session(idle="+3 seconds")
    key = services.tokens.digest(token)
    original = await services.repository.lookup_session(key)
    assert original is not None
    # Privileged connection is confined to this uniquely labelled disposable DB.
    engine = create_async_engine(
        URL.create(
            "postgresql+psycopg",
            username="postgres",
            host=services.host,
            password=services.stack.passwords["postgres_bootstrap"].read_text().strip(),
            database="mimir",
        ),
        hide_parameters=True,
    )
    task = None
    try:
        async with engine.begin() as connection:
            await connection.execute(
                text("SELECT FROM archive.users WHERE user_id=:user FOR UPDATE"),
                {"user": services.user},
            )
            task = asyncio.create_task(services.auth.authenticate(token))
            await services.wait_for_auth_lock()
            await asyncio.sleep(
                max(0, (original.expires_at - datetime.now(UTC)).total_seconds()) + 0.02
            )
        with pytest.raises(AuthenticationRejected):
            await task
        assert await services.repository.lookup_session(key) == original
    finally:
        if task is not None and not task.done():
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task
        await engine.dispose()


@pytest.mark.asyncio
async def test_t08_local_admin_command_real_create_disable(session_services, capsys):
    services = session_services
    command = MaintenanceCommand()
    common = [
        "--host",
        services.host,
        "--credential-file",
        str(services.stack.passwords["mimir_maintenance"]),
    ]
    username = f"synthetic_command_{uuid.uuid7()}"
    args = command.parser().parse_args([*common, "create-user", "--username", username])
    with patch(
        "mimir_backend.provision.getpass.getpass", return_value=services.password
    ):
        assert await command.run(args) == 0
    identity = await services.repository.lookup_identity(username)
    assert identity is not None
    issued = await services.auth.login(username, services.password, ClientType.CLI)
    args = command.parser().parse_args(
        [*common, "disable-user", "--user-id", str(identity.user_id)]
    )
    assert await command.run(args) == 0
    with pytest.raises(AuthenticationRejected):
        await services.auth.authenticate(issued.token)
    output = capsys.readouterr()
    assert services.password not in output.out + output.err
    assert issued.token not in output.out + output.err


V007 = "V007__session_profile_username.sql"


def test_t09_upgrade_profile_lookup_and_rollback(tmp_path):
    database = DatabaseStack(tmp_path, through_version=6)
    try:
        database.start()
        database.migrate()
        owner = AuthRows(database)
        other = AuthRows(database)
        owner.seed()
        other.seed()
        original = (ROOT / "backend/migrations" / V007).read_bytes()
        candidate = database.migrations / V007
        candidate.write_bytes(original + b"\nSELECT 1/0;\n")
        assert database.flyway("migrate", check=False).returncode != 0
        assert (
            database.sql(
                "SELECT to_regprocedure('archive.session_username(uuid)') IS NULL"
            ).stdout.strip()
            == "t"
        )
        candidate.write_bytes(original)
        database.flyway("migrate")
        database.flyway("validate")
        lookup = f"SELECT archive.session_username('{owner.identity}')"
        assert database.sql(lookup, "mimir_api").stdout.strip() == ""
        assert (
            database.sql(
                f"BEGIN; SET LOCAL app.current_user_id='{owner.user}'; {lookup}; COMMIT",
                "mimir_api",
            ).stdout.strip()
            == owner.username
        )
        assert (
            database.sql(
                f"BEGIN; SET LOCAL app.current_user_id='{other.user}'; {lookup}; COMMIT",
                "mimir_api",
            ).stdout.strip()
            == ""
        )
        assert (
            database.sql(
                "SELECT username FROM auth.identities", "mimir_api", check=False
            ).returncode
            != 0
        )
        for role in ("mimir_worker", "mimir_auth", "mimir_maintenance"):
            assert database.sql(lookup, role, check=False).returncode != 0
        assert (
            database.sql(
                f"BEGIN; SET LOCAL app.current_user_id='{owner.user}'; "
                f"SET LOCAL row_security=off; SELECT user_id FROM archive.users; COMMIT",
                "mimir_api",
                check=False,
            ).returncode
            != 0
        )
    finally:
        database.cleanup()


@pytest.mark.asyncio
async def test_t09_http_real_roles_renewal_profile_logout(tmp_path):
    database = DatabaseStack(tmp_path, through_version=7)
    try:
        database.start()
        database.migrate()
        database.flyway("validate")
        services = SessionServices(database)
        try:
            await services.seed()
            second = await services.admin.provision(
                "other", services.password, "private"
            )
            auth_url = services.auth_database.engine.url
            app = create_app(
                auth_url=auth_url,
                api_url=auth_url.set(
                    username="mimir_api",
                    password=database.passwords["mimir_api"].read_text().strip(),
                ),
                cli_authority="127.0.0.1:8000",
                browser_origin="https://browser.invalid",
            )
            transport = httpx.ASGITransport(app=app)
            async with (
                app.router.lifespan_context(app),
                httpx.AsyncClient(
                    base_url="http://127.0.0.1:8000/api/v1/",
                    transport=transport,
                ) as cli,
                httpx.AsyncClient(
                    base_url="https://browser.invalid/api/v1/", transport=transport
                ) as web,
            ):
                login = {"username": services.username, "password": services.password}
                browser = {"Origin": "https://browser.invalid", "X-Mimir-CSRF": "1"}
                issued = await cli.post(
                    "auth/login", json={**login, "client_type": "cli"}
                )
                assert issued.status_code == 201
                assert issued.headers["cache-control"] == "no-store"
                assert "set-cookie" not in issued.headers
                token = issued.json()["access_token"]
                bearer = {"Authorization": f"Bearer {token}"}
                issued_web = await web.post(
                    "auth/login", json={**login, "client_type": "web"}, headers=browser
                )
                assert issued_web.status_code == 201
                assert set(issued_web.json()) == {"session"}
                cookie = issued_web.headers["set-cookie"].lower()
                assert all(
                    attr in cookie
                    for attr in [
                        "httponly",
                        "secure",
                        "samesite=strict",
                        "path=/",
                        "max-age=",
                    ]
                )
                assert "domain=" not in cookie
                assert (await cli.get("users/me")).status_code == 401
                old = services.seed_session()
                old_header = {"Authorization": f"Bearer {old}"}
                reads = await asyncio.gather(
                    *(cli.get("auth/session", headers=old_header) for _ in range(4))
                )
                for res in reads:
                    assert res.status_code == 200
                    metadata = res.json()
                    assert set(metadata) == {
                        "user_id",
                        "client_type",
                        "expires_at",
                        "absolute_expires_at",
                    }
                    assert datetime.fromisoformat(
                        metadata["expires_at"]
                    ) > datetime.now(UTC) + timedelta(days=29)
                profile = {
                    "user_id": str(services.user),
                    "username": services.username,
                    "display_name": None,
                }
                assert (await web.get("users/me")).json() == profile
                assert (await cli.get("users/me", headers=bearer)).json() == profile
                assert (
                    await web.patch("users/me", json={"display_name": "wrong"})
                ).status_code == 403
                profile["display_name"] = "updated"
                assert (
                    await web.patch(
                        "users/me", json={"display_name": "updated"}, headers=browser
                    )
                ).json() == profile
                assert (
                    await cli.patch("users/me", json={}, headers=bearer)
                ).json() == profile
                assert (
                    await cli.patch(
                        "users/me", json={"user_id": str(second)}, headers=bearer
                    )
                ).status_code == 422
                profile["display_name"] = None
                assert (
                    await cli.patch(
                        "users/me", json={"display_name": None}, headers=bearer
                    )
                ).json() == profile
                assert (
                    database.sql(
                        f"SELECT display_name FROM archive.users WHERE user_id='{second}'"
                    ).stdout.strip()
                    == "private"
                )
                assert (
                    await web.get("auth/session", headers=bearer)
                ).status_code == 400
                logged_out = await web.post("auth/logout", headers=browser)
                assert logged_out.status_code == 204 and not logged_out.content
                assert "max-age=0" in logged_out.headers["set-cookie"].lower()
                assert (await web.get("auth/session")).status_code == 401
                assert (
                    await cli.post("auth/logout", headers=bearer)
                ).status_code == 204
                assert (
                    await cli.get("auth/session", headers=bearer)
                ).status_code == 401
        finally:
            await services.close()
    finally:
        database.cleanup()
