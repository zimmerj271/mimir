"""T03 operator policy, secret boundaries and destructive-operation failures."""

import copy
import json
import secrets
import subprocess
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "deployment"))
from migrate import (  # noqa: E402
    FLYWAY_ENV,
    FLYWAY_IMAGE,
    PG_IMAGE,
    ROLES,
    MigrationPolicy,
    MigrationRunner,
    PolicyError,
)


@pytest.fixture
def migration_config(config: dict, tmp_path: Path) -> dict:
    # Deterministic unit model; integration compiles the actual Compose sources.
    config = copy.deepcopy(config)
    for role in ("postgres_bootstrap", *[f"mimir_{role}" for role in ROLES]):
        name = f"{role}_password"
        path = tmp_path / name
        path.write_text(secrets.token_urlsafe(48))
        path.chmod(0o600)
        config["secrets"][name] = {"file": str(path)}
    for name in ("role-bootstrap", "flyway", "role-credentials"):
        role_secrets = {
            "role-bootstrap": [
                "postgres_bootstrap_password",
                "mimir_migrator_password",
            ],
            "flyway": ["mimir_migrator_password"],
            "role-credentials": [
                "postgres_bootstrap_password",
                *[f"mimir_{role}_password" for role in ROLES if role != "migrator"],
            ],
        }[name]
        flyway = name == "flyway"
        config["services"][name] = {
            "profiles": ["migrations"],
            "image": FLYWAY_IMAGE if flyway else PG_IMAGE,
            "restart": "no",
            "user": "0:0",
            "read_only": True,
            "cap_drop": ["ALL"],
            "cap_add": ["DAC_OVERRIDE"],
            "security_opt": ["no-new-privileges:true"],
            "networks": {"private": None},
            "entrypoint": [
                "/bin/sh",
                "/mimir/database/flyway.sh"
                if flyway
                else "/mimir/database/provision.sh",
            ],
            "command": [
                {
                    "flyway": "migrate",
                    "role-bootstrap": "bootstrap",
                    "role-credentials": "credentials",
                }[name]
            ],
            "secrets": [
                {"source": secret, "target": f"/run/secrets/{secret}"}
                for secret in role_secrets
            ],
            "environment": FLYWAY_ENV.copy() if flyway else {},
            "volumes": [
                {
                    "type": "bind",
                    "source": str(ROOT / source),
                    "target": target,
                    "read_only": True,
                }
                for source, target in (
                    [
                        ("deployment/database/flyway.sh", "/mimir/database/flyway.sh"),
                        ("backend/migrations", "/flyway/sql"),
                    ]
                    if flyway
                    else [("deployment/database", "/mimir/database")]
                )
            ],
        }
    return config


def test_compiled_migration_policy(migration_config: dict) -> None:
    policy = MigrationPolicy()
    policy.validate(migration_config)
    policy.validate_files(migration_config)


@pytest.mark.parametrize(
    "fault",
    [
        "admin_flyway",
        "public",
        "writable",
        "unpin",
        "clean",
        "baseline",
        "out_of_order",
        "credential_env",
        "ddl_backend",
        "network",
        "privileged",
        "command",
        "extra_mount",
        "entrypoint",
        "root_write",
        "additional_service",
    ],
)
def test_reject_unsafe_migration_model(migration_config: dict, fault: str) -> None:
    config = copy.deepcopy(migration_config)
    job = config["services"]["flyway"]
    if fault == "admin_flyway":
        job["secrets"].append({"source": "postgres_bootstrap_password"})
    elif fault == "public":
        job["ports"] = [{"target": 5432, "published": "5432"}]
    elif fault == "writable":
        job["volumes"][0]["read_only"] = False
    elif fault == "unpin":
        job["image"] = "flyway/flyway:latest"
    elif fault in {"clean", "baseline", "out_of_order"}:
        key = {
            "clean": "FLYWAY_CLEAN_DISABLED",
            "baseline": "FLYWAY_BASELINE_ON_MIGRATE",
            "out_of_order": "FLYWAY_OUT_OF_ORDER",
        }[fault]
        job["environment"][key] = "false" if fault == "clean" else "true"
    elif fault == "credential_env":
        job["environment"]["FLYWAY_PASSWORD"] = "synthetic-fixture"
    elif fault == "ddl_backend":
        config["services"]["backend"]["secrets"] = [
            {"source": "mimir_migrator_password"}
        ]
    elif fault == "network":
        job["networks"]["api"] = None
    elif fault == "privileged":
        job["privileged"] = True
    elif fault == "command":
        job["command"] = ["repair"]
    elif fault == "extra_mount":
        job["volumes"].append(
            {
                "type": "bind",
                "source": "/var/run/docker.sock",
                "target": "/var/run/docker.sock",
                "read_only": True,
            }
        )
    elif fault == "entrypoint":
        job["entrypoint"] = ["/bin/sh"]
    elif fault == "root_write":
        job["read_only"] = False
    else:
        config["services"]["other"] = job
    with pytest.raises(PolicyError):
        MigrationPolicy().validate(config)


@pytest.mark.parametrize(
    "fault", ["short", "newline", "unsafe_mode", "symlink", "shared"]
)
def test_database_secret_failure_paths(
    migration_config: dict, fault: str, tmp_path: Path
) -> None:
    path = Path(migration_config["secrets"]["mimir_api_password"]["file"])
    if fault == "short":
        path.write_text("short")
    elif fault == "newline":
        path.write_text("a" * 40 + "\nsecond-line")
    elif fault == "unsafe_mode":
        path.chmod(0o644)
    elif fault == "symlink":
        link = tmp_path / "link"
        link.symlink_to(path)
        migration_config["secrets"]["mimir_api_password"]["file"] = str(link)
    else:
        migration_config["secrets"]["mimir_auth_password"]["file"] = str(path)
    with pytest.raises(PolicyError):
        MigrationPolicy().validate_files(migration_config)


@pytest.mark.parametrize(
    "operation", ["clean", "repair", "baseline", "undo", "migrate -X"]
)
def test_operator_rejects_destructive_or_injected_operation(operation: str) -> None:
    with patch("migrate.subprocess.run") as run:
        with pytest.raises(PolicyError):
            MigrationRunner().run(operation)
        run.assert_not_called()


@pytest.mark.parametrize("operation", ["clean", "repair", "baseline"])
def test_shell_rejects_unsafe_operation_before_credentials(operation: str) -> None:
    result = subprocess.run(
        ["sh", str(ROOT / "deployment/database/flyway.sh"), operation],
        capture_output=True,
        text=True,
        timeout=5,
    )
    assert result.returncode != 0
    assert "forbidden" in result.stderr


def test_runner_stops_after_migration_failure(migration_config: dict) -> None:
    runner = MigrationRunner()
    commands = []

    def run(*args: str) -> None:
        commands.append(args)
        if "flyway" in args:
            raise subprocess.CalledProcessError(1, ["flyway"])

    with (
        patch(
            "migrate.subprocess.run",
            return_value=subprocess.CompletedProcess(
                [], 0, json.dumps(migration_config)
            ),
        ),
        patch.object(runner, "_compose", side_effect=run),
    ):
        with pytest.raises(subprocess.CalledProcessError):
            runner.run("migrate")
    assert len(commands) == 3
    assert not any("role-credentials" in command for command in commands)
