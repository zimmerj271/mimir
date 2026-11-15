"""T03/T04/T05 real PostgreSQL 18/Flyway tests, never skip a missing daemon.

All fixtures use unique labelled disposable volumes. T03 fixtures stop at V001;
T04/T05 exercise real V002/V003 on fresh and upgrade paths.
"""

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
from pathlib import Path

import pytest

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
    ):
        message = message or str(uuid.uuid7())
        revision = revision or str(uuid.uuid7())
        pointer = f"'{revision}'" if pointer_first else "NULL"
        return f"""
            INSERT INTO archive.messages(message_id,user_id,conversation_id,ordinal,
                role,current_revision_id) VALUES ('{message}','{self.user}',
                    '{self.conversation}',{ordinal},'{role}',{pointer});
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
    value = status if status == "NULL" else f"'{status}'"
    sql = (
        rows.incomplete_sql()
        .replace("ordinal,role)", "ordinal,role,status)")
        .replace("2,'user')", f"2,'user',{value})")
    )
    assert rows.actor(sql, check=False).returncode != 0


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
