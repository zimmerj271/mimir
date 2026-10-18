"""T03 real PostgreSQL 18/Flyway tests, never skipped for a missing daemon.

All fixtures use unique labelled disposable volumes and test-only SQL. Synthetic
upgrade/failure migrations and RLS tables do not implement later task schemas.
"""

import concurrent.futures
import json
import os
import secrets
import shutil
import subprocess
import sys
import uuid
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "deployment"))
from migrate import ROLES, MigrationPolicy, MigrationRunner  # noqa: E402

RUNTIME = ("api", "worker", "auth", "maintenance")


class DatabaseStack:
    def __init__(self, directory: Path) -> None:
        self.directory = directory
        directory.chmod(0o700)
        self.token = uuid.uuid4().hex
        self.project = f"mimir-t03-{self.token[:12]}"
        self.env = os.environ.copy()
        self.env["COMPOSE_PROJECT_NAME"] = self.project
        self.passwords = {}
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
        shutil.copytree(ROOT / "backend/migrations", self.migrations)
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
        for path in self.passwords.values():
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
    ) -> subprocess.CompletedProcess:
        # stdin carries credentials, never host argv/environment or model fields.
        secret_role = "postgres_bootstrap" if role == "postgres" else role
        secret = self.passwords[secret_role].read_text().strip()
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

    def cleanup(self) -> None:
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
