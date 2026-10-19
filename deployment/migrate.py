"""Validated operator-only T03 provisioning and SQL migration entry point."""

import argparse
import copy
import json
import re
import subprocess
import sys
from pathlib import Path

from validate_compose import (
    ROOT,
    ComposePolicy,
    PolicyError,
    mapping,
    require,
    sequence,
)

ROLES = ("migrator", "api", "worker", "auth", "maintenance")
JOBS = {"role-bootstrap", "flyway", "role-credentials"}
PG_IMAGE = "postgres:18.4@sha256:a02db8cac496f15b094798a38254f14d6e00741f709360e5e00bb6668ea31636"
FLYWAY_IMAGE = "flyway/flyway:12.4.0@sha256:5be18367a9b3979a9f37234c371b80da457a448fb9577bd94d4bf24d923fafac"
FLYWAY_ENV = {
    "FLYWAY_URL": "jdbc:postgresql://postgres:5432/mimir",
    "FLYWAY_USER": "mimir_migrator",
    "FLYWAY_LOCATIONS": "filesystem:/flyway/sql",
    "FLYWAY_DEFAULT_SCHEMA": "public",
    "FLYWAY_CREATE_SCHEMAS": "false",
    "FLYWAY_CLEAN_DISABLED": "true",
    "FLYWAY_BASELINE_ON_MIGRATE": "false",
    "FLYWAY_OUT_OF_ORDER": "false",
    "FLYWAY_VALIDATE_ON_MIGRATE": "true",
    "FLYWAY_VALIDATE_MIGRATION_NAMING": "true",
    "FLYWAY_CONNECT_RETRIES": "0",
    "FLYWAY_REPORT_ENABLED": "false",
    "REDGATE_DISABLE_TELEMETRY": "true",
    "JAVA_ARGS": "-Duser.home=/tmp",
}


class MigrationPolicy:
    """Preserve T02 isolation and constrain privileged one-shot migration jobs."""

    def validate(self, document: object) -> None:
        config = mapping(document)
        services = mapping(config.get("services"))
        secrets = mapping(config.get("secrets"))
        require(
            set(services) == {"backend", "postgres", "qdrant", "ollama"} | JOBS,
            "unexpected migration services",
        )
        require(
            set(secrets)
            == {"postgres_bootstrap_password", "qdrant_api_key"}
            | {f"mimir_{role}_password" for role in ROLES},
            "unexpected migration secrets",
        )
        foundation = copy.deepcopy(config)
        foundation["services"] = {
            key: value for key, value in services.items() if key not in JOBS
        }
        foundation["secrets"] = {
            key: value
            for key, value in secrets.items()
            if key in {"postgres_bootstrap_password", "qdrant_api_key"}
        }
        ComposePolicy().validate(foundation)
        for name in JOBS:
            self._job(name, mapping(services[name]))

    def _job(self, name: str, service: dict[str, object]) -> None:
        require(
            service.get("image") == (FLYWAY_IMAGE if name == "flyway" else PG_IMAGE),
            "migration image differs from approved pin",
        )
        for key, value in {
            "profiles": ["migrations"],
            "user": "0:0",
            "read_only": True,
            "cap_drop": ["ALL"],
            "cap_add": ["DAC_OVERRIDE"],
            "security_opt": ["no-new-privileges:true"],
            "restart": "no",
        }.items():
            require(service.get(key) == value, f"{name}: unsafe {key}")
        require(
            set(mapping(service.get("networks"))) == {"private"},
            "migration jobs must remain private",
        )
        require(
            not any(
                service.get(key)
                for key in (
                    "ports",
                    "network_mode",
                    "privileged",
                    "pid",
                    "devices",
                    "build",
                    "extra_hosts",
                    "env_file",
                )
            ),
            "migration job host access/overrides forbidden",
        )
        require(
            service.get("tmpfs")
            == ["/tmp:rw,noexec,nosuid,size=" + ("64m" if name == "flyway" else "16m")],
            "migration tmpfs must be bounded and non-executable",
        )
        entry = "flyway.sh" if name == "flyway" else "provision.sh"
        require(
            service.get("entrypoint") == ["/bin/sh", f"/mimir/database/{entry}"],
            "migration entrypoint differs",
        )
        require(
            service.get("command")
            == [
                {
                    "flyway": "migrate",
                    "role-bootstrap": "bootstrap",
                    "role-credentials": "credentials",
                }[name]
            ],
            "migration default command differs",
        )
        expected_secrets = {
            "role-bootstrap": {
                "postgres_bootstrap_password",
                "mimir_migrator_password",
            },
            "flyway": {"mimir_migrator_password"},
            "role-credentials": {"postgres_bootstrap_password"}
            | {f"mimir_{role}_password" for role in ROLES if role != "migrator"},
        }[name]
        entries = [mapping(raw) for raw in sequence(service.get("secrets"))]
        require(
            {item.get("source") for item in entries} == expected_secrets
            and len(entries) == len(expected_secrets)
            and all(
                item.get("target")
                in {None, item["source"], f"/run/secrets/{item['source']}"}
                for item in entries
            ),
            "migration credential separation differs",
        )
        mounts = [mapping(raw) for raw in sequence(service.get("volumes"))]
        expected_mounts = (
            {
                (
                    str(ROOT / "deployment/database/flyway.sh"),
                    "/mimir/database/flyway.sh",
                ),
                (str(ROOT / "backend/migrations"), "/flyway/sql"),
            }
            if name == "flyway"
            else {(str(ROOT / "deployment/database"), "/mimir/database")}
        )
        require(
            {(item.get("source"), item.get("target")) for item in mounts}
            == expected_mounts
            and len(mounts) == len(expected_mounts)
            and all(
                item.get("type") == "bind" and item.get("read_only") is True
                for item in mounts
            ),
            "migration mounts must be read-only approved inputs",
        )
        require(
            mapping(service.get("environment", {}))
            == (FLYWAY_ENV if name == "flyway" else {}),
            "migration configuration/credentials may not be overridden",
        )

    def validate_files(self, document: object) -> None:
        ComposePolicy().validate_secret_file(document)
        paths: set[Path] = set()
        for name, raw in mapping(mapping(document)["secrets"]).items():
            path = Path(str(mapping(raw)["file"]))
            require(
                not any(parent.is_symlink() for parent in path.parents),
                "symlinked secret directory",
            )
            require(path not in paths, "database credentials must use separate files")
            paths.add(path)
            if name != "qdrant_api_key":
                require(
                    re.fullmatch(rb"[A-Za-z0-9_-]{32,128}\n?", path.read_bytes()),
                    "database secret must contain 32 to 128 URL-safe characters",
                )


class MigrationRunner:
    """Run only explicit provisioning/migrate/validate/info; never destructive repair."""

    def __init__(self) -> None:
        self.command = [
            "docker",
            "compose",
            "-f",
            str(ROOT / "deployment/compose.yaml"),
            "-f",
            str(ROOT / "deployment/compose.migrations.yaml"),
        ]

    def run(self, operation: str) -> None:
        require(
            operation in {"migrate", "validate", "info", "config"},
            "unsupported migration operation",
        )
        result = subprocess.run(
            [
                *self.command,
                "--profile",
                "backend",
                "--profile",
                "migrations",
                "config",
                "--format",
                "json",
            ],
            capture_output=True,
            text=True,
            check=True,
            timeout=30,
        )
        document: object = json.loads(result.stdout)
        policy = MigrationPolicy()
        policy.validate(document)
        policy.validate_files(document)
        if operation == "config":
            return
        # No automatic retries, rotation, clean, repair, baseline or volume removal.
        if operation == "migrate":
            self._compose("up", "-d", "--wait", "postgres")
            self._compose("run", "--rm", "--no-deps", "-T", "role-bootstrap")
        self._compose("run", "--rm", "--no-deps", "-T", "flyway", operation)
        if operation == "migrate":
            self._compose("run", "--rm", "--no-deps", "-T", "role-credentials")

    def _compose(self, *arguments: str) -> None:
        # Keep diagnostics for operators, but never echo the compiled secret paths/model.
        subprocess.run([*self.command, *arguments], check=True, timeout=300)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("operation", choices=("config", "migrate", "validate", "info"))
    args = parser.parse_args()
    try:
        MigrationRunner().run(args.operation)
    except PolicyError, OSError, ValueError, KeyError, subprocess.SubprocessError:
        print(
            "FAIL: database provisioning/migration; inspect redacted service diagnostics",
            file=sys.stderr,
        )
        return 1
    print(f"PASS: database {args.operation}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
