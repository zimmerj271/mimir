"""DEPLOY-01 policy failure paths; the actual Compose model is checked in Docker CI."""

import copy
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "deployment"))
from validate_compose import ComposePolicy, PolicyError  # noqa: E402


def test_valid_policy_and_protected_files(config: dict) -> None:
    policy = ComposePolicy()
    policy.validate(config)
    policy.validate_secret_file(config)
    # Compose omits false/default network fields in normalized JSON.
    config["networks"]["api"].pop("internal")
    policy.validate(config)


@pytest.mark.parametrize(
    "fault",
    [
        "public_backend",
        "database_port",
        "vector_port",
        "model_port",
        "egress",
        "host_network",
        "privileged",
        "unpinned",
        "pg_major",
        "disposable_volume",
        "lost_mount",
        "credential_in_env",
        "trust_auth",
        "backend_bootstrap_secret",
        "backend_placeholder",
        "backend_root",
        "qdrant_no_key",
        "host_mount",
        "peer_api_network",
        "backend_routing",
        "qdrant_telemetry",
        "ollama_cloud",
    ],
)
def test_reject_unsafe_compose_models(config: dict, fault: str) -> None:
    config = copy.deepcopy(config)
    services = config["services"]
    if fault == "public_backend":
        services["backend"]["ports"][0]["host_ip"] = "0.0.0.0"
    elif fault in {"database_port", "vector_port", "model_port"}:
        role = {
            "database_port": "postgres",
            "vector_port": "qdrant",
            "model_port": "ollama",
        }[fault]
        services[role]["ports"] = [{"target": 5432, "published": "5432"}]
    elif fault == "egress":
        config["networks"]["private"]["internal"] = False
    elif fault == "host_network":
        services["backend"]["network_mode"] = "host"
    elif fault == "privileged":
        services["backend"]["privileged"] = True
    elif fault == "unpinned":
        services["qdrant"]["image"] = "qdrant/qdrant:latest"
    elif fault == "pg_major":
        services["postgres"]["image"] = "postgres:17@sha256:" + "a" * 64
    elif fault == "disposable_volume":
        config["volumes"]["postgres_data"]["external"] = False
    elif fault == "lost_mount":
        services["postgres"]["volumes"][0]["target"] = "/var/lib/postgresql/data"
    elif fault == "credential_in_env":
        services["postgres"]["environment"]["POSTGRES_PASSWORD"] = "synthetic-fixture"
    elif fault == "trust_auth":
        services["postgres"]["environment"]["POSTGRES_INITDB_ARGS"] = (
            "--auth-host=trust"
        )
    elif fault == "backend_bootstrap_secret":
        services["backend"]["secrets"] = [{"source": "postgres_bootstrap_password"}]
    elif fault == "backend_placeholder":
        services["backend"]["command"] = ["sleep", "infinity"]
    elif fault == "backend_root":
        services["backend"]["user"] = "0:0"
    elif fault == "qdrant_no_key":
        services["qdrant"]["secrets"] = []
    elif fault == "peer_api_network":
        services["postgres"]["networks"]["api"] = None
    elif fault == "backend_routing":
        services["backend"]["sysctls"]["net.ipv4.ip_forward"] = "1"
    elif fault == "qdrant_telemetry":
        services["qdrant"]["environment"]["QDRANT__TELEMETRY_DISABLED"] = "false"
    elif fault == "ollama_cloud":
        services["ollama"]["environment"]["OLLAMA_NO_CLOUD"] = "false"
    else:
        services["qdrant"]["volumes"].append(
            {"type": "bind", "source": "/", "target": "/host"}
        )
    with pytest.raises(PolicyError):
        ComposePolicy().validate(config)


@pytest.mark.parametrize(
    "fault", ["empty", "readable", "directory", "symlink", "missing"]
)
def test_reject_unsafe_secret_files(config: dict, fault: str, tmp_path: Path) -> None:
    path = Path(config["secrets"]["qdrant_api_key"]["file"])
    if fault == "empty":
        path.write_bytes(b"")
    elif fault == "readable":
        path.chmod(0o644)
    elif fault == "directory":
        tmp_path.chmod(0o755)
    elif fault == "missing":
        path.unlink()
    else:
        target = tmp_path / "real-key"
        path.rename(target)
        path.symlink_to(target)
    with pytest.raises(PolicyError):
        ComposePolicy().validate_secret_file(config)


def test_host_mode_requires_actual_bridge_gateway(config: dict) -> None:
    del config["services"]["ollama"]
    del config["volumes"]["ollama_data"]
    backend = config["services"]["backend"]
    backend["extra_hosts"] = ["ollama=172.23.0.1"]
    ComposePolicy().validate(config, "172.23.0.1")
    with pytest.raises(PolicyError):
        ComposePolicy().validate(config, "172.24.0.1")
    backend["extra_hosts"] = ["ollama=127.0.0.1"]
    with pytest.raises(PolicyError):
        ComposePolicy().validate(config, "127.0.0.1")
