"""Validate compiled Compose topology without printing configuration or secrets."""

import argparse
import ipaddress
import json
import os
import re
import stat
import subprocess
import sys
from pathlib import Path
from typing import cast

ROOT = Path(__file__).resolve().parents[1]


class PolicyError(ValueError):
    """The deployment violates a local-only foundation invariant."""


def require(condition: object, message: str) -> None:
    if not condition:
        raise PolicyError(message)


def mapping(value: object) -> dict[str, object]:
    require(isinstance(value, dict), "expected configuration object")
    result = cast(dict[str, object], value)
    require(all(isinstance(key, str) for key in result), "invalid object key")
    return result


def sequence(value: object) -> list[object]:
    require(isinstance(value, list), "expected configuration list")
    return cast(list[object], value)


class ComposePolicy:
    """Local topology, credential separation and data-volume boundaries."""

    def validate(self, document: object, host_gateway: str | None = None) -> None:
        config = mapping(document)
        services = mapping(config.get("services"))
        expected = {"backend", "postgres", "qdrant"}
        if host_gateway is None:
            expected.add("ollama")
        require(set(services) == expected, "unexpected/missing services or Ollama mode")
        networks = mapping(config.get("networks"))
        require(set(networks) == {"private", "api"}, "unexpected deployment networks")
        require(
            mapping(networks["private"]).get("internal") is True,
            "private network must be externally isolated",
        )
        api = mapping(networks["api"])
        require(
            api.get("internal", False) is False and api.get("driver") == "bridge",
            "API publishing requires the dedicated bridge",
        )
        require(
            mapping(api.get("driver_opts")).get(
                "com.docker.network.bridge.host_binding_ipv4"
            )
            == "127.0.0.1",
            "API bridge default host binding must be loopback",
        )
        volumes = mapping(config.get("volumes"))
        roles = (
            {"postgres", "qdrant", "ollama"}
            if host_gateway is None
            else {"postgres", "qdrant"}
        )
        require(
            set(volumes) == {f"{role}_data" for role in roles},
            "persistent volumes differ",
        )
        project = str(config.get("name", ""))
        require(re.fullmatch(r"[a-z0-9][a-z0-9_-]*", project), "invalid project name")
        for role in roles:
            volume = mapping(volumes[f"{role}_data"])
            require(volume.get("external") is True, "data volume must be external")
            require(
                volume.get("name") == f"{project}_{role}", "unstable volume identity"
            )

        for name, raw in services.items():
            service = mapping(raw)
            require(
                service.get("security_opt") == ["no-new-privileges:true"],
                f"{name}: privilege escalation must be disabled",
            )
            require(
                service.get("cap_add", [])
                == (["DAC_OVERRIDE"] if name == "qdrant" else []),
                f"{name}: unexpected additional capabilities",
            )
            require(
                set(mapping(service.get("networks")))
                == ({"private", "api"} if name == "backend" else {"private"}),
                f"{name}: unexpected network attachment",
            )
            require(
                not service.get("network_mode") and not service.get("privileged"),
                f"{name}: host/privileged networking is forbidden",
            )
            require(
                not service.get("pid") and not service.get("devices"),
                f"{name}: host process/device access is forbidden",
            )
            if name != "backend":
                require(
                    not service.get("ports"), f"{name}: published port is forbidden"
                )
                require(
                    re.fullmatch(
                        r"[^\s]+@sha256:[0-9a-f]{64}", str(service.get("image"))
                    ),
                    f"{name}: service image must be digest-pinned",
                )
            else:
                self._backend(service, host_gateway)

        postgres = mapping(services["postgres"])
        require(
            str(postgres["image"]).startswith("postgres:18."), "PostgreSQL 18 required"
        )
        env = mapping(postgres.get("environment"))
        require(
            not any(
                key in env
                for key in (
                    "POSTGRES_PASSWORD",
                    "POSTGRES_PASSWORD_FILE",
                    "POSTGRES_HOST_AUTH_METHOD",
                )
            ),
            "bootstrap credentials/trust overrides must not be Compose environment values",
        )
        require(
            env.get("POSTGRES_INITDB_ARGS") == "--auth-host=scram-sha-256",
            "PostgreSQL TCP authentication must use SCRAM",
        )
        secrets = sequence(postgres.get("secrets"))
        require(
            len(secrets) == 1
            and mapping(secrets[0]).get("source") == "postgres_bootstrap_password"
            and mapping(secrets[0]).get("target")
            in {
                None,
                "postgres_bootstrap_password",
                "/run/secrets/postgres_bootstrap_password",
            },
            "PostgreSQL must use only its bootstrap file secret",
        )
        require(
            set(mapping(config.get("secrets")))
            == {"postgres_bootstrap_password", "qdrant_api_key"},
            "unexpected deployment secrets",
        )
        for raw_secret in mapping(config["secrets"]).values():
            secret = mapping(raw_secret)
            require(
                "file" in secret and set(secret) <= {"file", "name"},
                "secret must be file-backed",
            )
        qdrant = mapping(services["qdrant"])
        require(
            mapping(qdrant.get("environment", {})).get("QDRANT__TELEMETRY_DISABLED")
            == "true",
            "Qdrant telemetry must be disabled",
        )
        if "ollama" in services:
            require(
                mapping(mapping(services["ollama"]).get("environment", {})).get(
                    "OLLAMA_NO_CLOUD"
                )
                == "true",
                "Ollama cloud features must be disabled",
            )
        require(
            qdrant.get("entrypoint") == ["/bin/sh", "/mimir/qdrant-entrypoint.sh"],
            "Qdrant must load file-backed credentials",
        )
        require(
            "QDRANT__SERVICE__API_KEY" not in mapping(qdrant.get("environment", {})),
            "Qdrant key must not be a Compose environment value",
        )
        qdrant_secrets = sequence(qdrant.get("secrets"))
        require(
            len(qdrant_secrets) == 1
            and mapping(qdrant_secrets[0]).get("source") == "qdrant_api_key"
            and mapping(qdrant_secrets[0]).get("target")
            in {None, "qdrant_api_key", "/run/secrets/qdrant_api_key"},
            "Qdrant must use its API key file secret",
        )
        mounts = {
            "postgres": ("postgres_data", "/var/lib/postgresql"),
            "qdrant": ("qdrant_data", "/qdrant/storage"),
            "ollama": ("ollama_data", "/root/.ollama"),
        }
        for name, (source, target) in mounts.items():
            if name not in services:
                continue
            entries = [
                mapping(item)
                for item in sequence(mapping(services[name]).get("volumes"))
            ]
            require(
                any(
                    item.get("type") == "volume"
                    and item.get("source") == source
                    and item.get("target") == target
                    for item in entries
                ),
                f"{name}: missing persistent data mount",
            )
            for item in entries:
                if item.get("type") == "bind":
                    require(
                        name in {"postgres", "qdrant"}
                        and item.get("read_only") is True
                        and item.get("source")
                        == str(ROOT / f"deployment/{name}-entrypoint.sh")
                        and item.get("target") == f"/mimir/{name}-entrypoint.sh",
                        f"{name}: unexpected host bind mount",
                    )
                else:
                    require(
                        item.get("type") == "volume"
                        and item.get("source") == source
                        and item.get("target") == target,
                        f"{name}: unexpected data mount",
                    )

    def _backend(self, service: dict[str, object], host_gateway: str | None) -> None:
        require(
            mapping(service.get("sysctls")) == {"net.ipv4.ip_forward": "0"},
            "backend must not route between the API and private networks",
        )
        require(
            service.get("profiles") == ["backend"],
            "backend must remain explicitly disabled",
        )
        require(
            service.get("entrypoint") == [] and service.get("command") == [],
            "no placeholder application command is allowed",
        )
        require(
            service.get("user") == "65532:65532" and service.get("read_only") is True,
            "backend must be non-root with a read-only filesystem",
        )
        require(
            service.get("cap_drop") == ["ALL"], "backend capabilities must be dropped"
        )
        require(
            not service.get("secrets") and not service.get("volumes"),
            "backend must not receive bootstrap credentials or host mounts",
        )
        ports = sequence(service.get("ports"))
        require(len(ports) == 1, "only one backend port may be published")
        port = mapping(ports[0])
        require(
            port.get("host_ip") == "127.0.0.1"
            and port.get("target") == 8000
            and port.get("protocol") == "tcp",
            "backend must publish IPv4 loopback only",
        )
        if host_gateway is None:
            require(
                not service.get("extra_hosts"), "default mode must use container Ollama"
            )
        else:
            address = ipaddress.ip_address(host_gateway)
            require(
                address.version == 4
                and address.is_private
                and not address.is_loopback
                and not address.is_link_local
                and not address.is_unspecified,
                "host Ollama must use the private bridge gateway",
            )
            hosts = service.get("extra_hosts")
            require(
                hosts == {"ollama": host_gateway}
                or hosts == [f"ollama={host_gateway}"],
                "host Ollama address differs from the actual Docker bridge gateway",
            )

    def validate_secret_file(self, document: object) -> None:
        for raw in mapping(mapping(document)["secrets"]).values():
            path = Path(str(mapping(raw)["file"]))
            require(
                not path.is_symlink() and path.is_file(),
                "secret file is missing/unsafe",
            )
            details = path.stat()
            require(
                details.st_uid == os.getuid()
                and stat.S_IMODE(details.st_mode) == 0o600,
                "secret must be caller-owned mode 0600",
            )
            require(
                stat.S_IMODE(path.parent.stat().st_mode) == 0o700,
                "secret directory must have mode 0700",
            )
            require(bool(path.read_bytes().strip()), "secret must be nonempty")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host-ollama", action="store_true")
    args = parser.parse_args()
    command = ["docker", "compose", "-f", str(ROOT / "deployment/compose.yaml")]
    if args.host_ollama:
        command.extend(["-f", str(ROOT / "deployment/compose.host-ollama.yaml")])
    command.extend(["--profile", "backend", "config", "--format", "json"])
    try:
        result = subprocess.run(
            command, capture_output=True, text=True, check=True, timeout=30
        )
        config: object = json.loads(result.stdout)
        gateway = None
        if args.host_ollama:
            network = mapping(mapping(mapping(config)["networks"])["private"])["name"]
            result = subprocess.run(
                ["docker", "network", "inspect", str(network)],
                capture_output=True,
                text=True,
                check=True,
                timeout=30,
            )
            inspected = mapping(sequence(json.loads(result.stdout))[0])
            gateway = str(
                mapping(sequence(mapping(inspected["IPAM"])["Config"])[0])["Gateway"]
            )
        policy = ComposePolicy()
        policy.validate(config, gateway)
        policy.validate_secret_file(config)
    except (
        PolicyError,
        OSError,
        subprocess.SubprocessError,
        ValueError,
        KeyError,
    ) as exc:
        # Subprocess error strings contain only command/return code, not config contents.
        print(f"FAIL: deployment validation: {exc}", file=sys.stderr)
        return 1
    print("PASS: Compose isolation, persistence topology and protected secret file")
    return 0


if __name__ == "__main__":
    sys.exit(main())
