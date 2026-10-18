"""Shared deterministic Compose policy fixtures; no daemon required."""

from pathlib import Path

import pytest


@pytest.fixture
def config(tmp_path: Path) -> dict:
    tmp_path.chmod(0o700)
    services = {}
    for role, target in (
        ("postgres", "/var/lib/postgresql"),
        ("qdrant", "/qdrant/storage"),
        ("ollama", "/root/.ollama"),
    ):
        services[role] = {
            "image": ("postgres:18.4" if role == "postgres" else f"{role}/{role}:1")
            + "@sha256:"
            + "a" * 64,
            "networks": {"private": None},
            "security_opt": ["no-new-privileges:true"],
            "volumes": [{"type": "volume", "source": f"{role}_data", "target": target}],
        }
    services["postgres"].update(
        {
            "environment": {"POSTGRES_INITDB_ARGS": "--auth-host=scram-sha-256"},
            "secrets": [{"source": "postgres_bootstrap_password"}],
        }
    )
    services["qdrant"].update(
        {
            "entrypoint": ["/bin/sh", "/mimir/qdrant-entrypoint.sh"],
            "cap_add": ["DAC_OVERRIDE"],
            "environment": {"QDRANT__TELEMETRY_DISABLED": "true"},
            "secrets": [{"source": "qdrant_api_key"}],
        }
    )
    services["ollama"]["environment"] = {"OLLAMA_NO_CLOUD": "true"}
    services["backend"] = {
        "profiles": ["backend"],
        "entrypoint": [],
        "command": [],
        "user": "65532:65532",
        "read_only": True,
        "cap_drop": ["ALL"],
        "security_opt": ["no-new-privileges:true"],
        "networks": {"private": None, "api": None},
        "sysctls": {"net.ipv4.ip_forward": "0"},
        "ports": [{"host_ip": "127.0.0.1", "target": 8000, "protocol": "tcp"}],
    }
    secrets = {}
    for name in ("postgres_bootstrap_password", "qdrant_api_key"):
        path = tmp_path / name
        path.write_text("synthetic-test-fixture")
        path.chmod(0o600)
        secrets[name] = {"file": str(path)}
    return {
        "name": "fixture",
        "services": services,
        "secrets": secrets,
        "networks": {
            "private": {"internal": True},
            "api": {
                "internal": False,
                "driver": "bridge",
                "driver_opts": {
                    "com.docker.network.bridge.host_binding_ipv4": "127.0.0.1"
                },
            },
        },
        "volumes": {
            f"{role}_data": {"name": f"fixture_{role}", "external": True}
            for role in ("postgres", "qdrant", "ollama")
        },
    }
