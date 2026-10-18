"""T02 real-engine checks. Only unique, labelled test resources may be deleted.

The backend command is an explicit HTTP port probe, never a Mimir API or health
implementation. No model is downloaded and no Flyway/application schema is run.
"""

import json
import os
import secrets
import socket
import subprocess
import sys
import time
import urllib.request
import uuid
from pathlib import Path

import pytest

import mimir_backend

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "deployment"))
from validate_compose import ComposePolicy  # noqa: E402


class Stack:
    def __init__(self, directory: Path) -> None:
        self.directory = directory
        directory.chmod(0o700)
        self.token = uuid.uuid4().hex
        self.owns_project = False
        self.project = f"mimir-t02-{self.token[:12]}"
        self.env = os.environ.copy()
        self.env["COMPOSE_PROJECT_NAME"] = self.project
        self.keys = {}
        for name, variable in (
            ("postgres_bootstrap_password", "MIMIR_POSTGRES_PASSWORD_FILE"),
            ("qdrant_api_key", "MIMIR_QDRANT_API_KEY_FILE"),
        ):
            path = directory / name
            path.write_text(secrets.token_urlsafe(48))
            path.chmod(0o600)
            self.env[variable] = str(path)
            self.keys[name] = path
        self.source = ["-f", str(ROOT / "deployment/compose.yaml")]
        self.model = json.loads(
            self.docker(
                "compose",
                *self.source,
                "--profile",
                "backend",
                "config",
                "--format",
                "json",
            ).stdout
        )
        ComposePolicy().validate(self.model)
        ComposePolicy().validate_secret_file(self.model)
        self.image = f"{self.project}-backend:t02"
        self.file = directory / "test-only-probe.json"
        self.write_probe_model(self.model, self.file)
        self.volume_names = [item["name"] for item in self.model["volumes"].values()]
        self.outside = f"{self.project}_outside"
        self.host_provider = f"{self.project}-host-ollama"

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
        if check:
            assert result.returncode == 0, result.stderr
        return result

    def compose(self, *args: str, **kwargs) -> subprocess.CompletedProcess:
        return self.docker("compose", "-f", str(self.file), *args, **kwargs)

    def write_probe_model(self, model: dict, file: Path) -> None:
        model = json.loads(json.dumps(model))
        for service in model["services"].values():
            service["labels"] = {"mimir.t02_test": self.token}
        backend = model["services"]["backend"]
        backend.pop("profiles")
        backend["image"] = self.image
        backend["ports"][0]["published"] = "0"
        backend["command"] = [
            "python",
            "-m",
            "http.server",
            "8000",
            "--bind",
            "0.0.0.0",
        ]
        backend["healthcheck"] = {
            "test": [
                "CMD",
                "python",
                "-c",
                "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/',timeout=1)",
            ],
            "interval": "1s",
            "timeout": "2s",
            "retries": 30,
        }
        backend["build"]["labels"] = {"mimir.t02_test": self.token}
        file.write_text(json.dumps(model))

    def sql(self, statement: str) -> str:
        result = self.compose(
            "exec",
            "-T",
            "postgres",
            "/bin/sh",
            "-c",
            'PGPASSWORD="$(cat /run/secrets/postgres_bootstrap_password)" '
            "exec psql -h 127.0.0.1 -U postgres -d postgres -Atq -v ON_ERROR_STOP=1",
            input=statement,
        )
        return result.stdout.strip()

    def api(self, method: str, path: str, data: dict | None = None) -> dict:
        # Credentials travel on stdin, not the host argv, Compose model or logs.
        script = (
            "import json,sys,urllib.request; p=json.load(sys.stdin); "
            "r=urllib.request.Request('http://qdrant:6333'+p['path'], method=p['method'], "
            "headers={'api-key':p['key'],'Content-Type':'application/json'}, "
            "data=json.dumps(p['data']).encode() if p['data'] is not None else None); "
            "print(urllib.request.urlopen(r,timeout=5).read().decode())"
        )
        result = self.compose(
            "exec",
            "-T",
            "backend",
            "python",
            "-c",
            script,
            input=json.dumps(
                {
                    "key": self.keys["qdrant_api_key"].read_text(),
                    "method": method,
                    "path": path,
                    "data": data,
                }
            ),
        )
        return json.loads(result.stdout)

    def info(self, service: str) -> dict:
        container = self.compose("ps", "-q", service).stdout.strip()
        return json.loads(self.docker("inspect", container).stdout)[0]

    def wait_ready(self) -> None:
        for attempt in range(30):
            try:
                self.api("GET", "/collections")
                return
            except AssertionError:
                if attempt == 29:
                    raise
                time.sleep(0.5)

    def port(self) -> int:
        return int(
            self.info("backend")["NetworkSettings"]["Ports"]["8000/tcp"][0]["HostPort"]
        )

    def gateway(self) -> str:
        network = self.model["networks"]["private"]["name"]
        return json.loads(self.docker("network", "inspect", network).stdout)[0]["IPAM"][
            "Config"
        ][0]["Gateway"]

    def cleanup(self) -> None:
        self.remove_owned("container", self.host_provider)
        if self.owns_project:
            self.compose("down", "--remove-orphans", check=False)
        self.remove_owned("network", self.outside)
        for name in self.volume_names:
            result = self.docker("volume", "inspect", name, check=False)
            if result.returncode == 0:
                details = json.loads(result.stdout)[0]
                # Never delete an operator volume, even following a naming collision.
                if details.get("Labels", {}).get("mimir.t02_test") == self.token:
                    self.docker("volume", "rm", name)
        self.remove_owned("image", self.image)

    def remove_owned(self, kind: str, name: str) -> None:
        result = self.docker(kind, "inspect", name, check=False)
        if result.returncode == 0:
            details = json.loads(result.stdout)[0]
            labels = (
                details.get("Labels", {})
                if kind == "network"
                else details.get("Config", {}).get("Labels", {})
            )
            if labels.get("mimir.t02_test") == self.token:
                args = [kind, "rm"] + (["-f"] if kind == "container" else []) + [name]
                self.docker(*args, check=False)


@pytest.fixture(scope="module")
def stack(tmp_path_factory):
    stack = Stack(tmp_path_factory.mktemp("mimir-t02"))
    try:
        stack.docker(
            "info", "--format", "{{.ServerVersion}}"
        )  # Fail, never skip, without an engine.
        existing = stack.docker(
            "ps", "-aq", "--filter", f"label=com.docker.compose.project={stack.project}"
        )
        assert not existing.stdout.strip(), (
            "refusing to reuse an existing Compose project"
        )
        stack.owns_project = True
        for name in stack.volume_names:
            stack.docker(
                "volume", "create", "--label", f"mimir.t02_test={stack.token}", name
            )
            details = json.loads(stack.docker("volume", "inspect", name).stdout)[0]
            assert details["Labels"]["mimir.t02_test"] == stack.token
        stack.compose("pull", "postgres", "qdrant", "ollama")
        stack.compose("build", "backend")
        stack.compose("up", "-d", "--wait", "--wait-timeout", "120")
        stack.wait_ready()
        yield stack
    finally:
        stack.cleanup()


def test_real_bindings_private_connectivity_and_rejected_auth(stack: Stack) -> None:
    assert (
        mimir_backend.__doc__
    )  # The installed T01 package also underlies the probe image.
    for name in ("postgres", "qdrant", "ollama"):
        info = stack.info(name)
        assert not any(info["NetworkSettings"]["Ports"].values())
    bindings = stack.info("backend")["NetworkSettings"]["Ports"]["8000/tcp"]
    assert len(bindings) == 1 and bindings[0]["HostIp"] == "127.0.0.1"
    with urllib.request.urlopen(
        f"http://127.0.0.1:{stack.port()}/", timeout=5
    ) as response:
        assert response.status == 200
    with pytest.raises(OSError):
        socket.create_connection((stack.gateway(), stack.port()), timeout=2)
    script = (
        "import socket,urllib.request,json; "
        "socket.create_connection(('postgres',5432),timeout=5).close(); "
        "assert json.load(urllib.request.urlopen('http://ollama:11434/api/tags',timeout=5))['models']==[]"
    )
    stack.compose("exec", "-T", "backend", "python", "-c", script)
    assert stack.api("GET", "/collections")["status"] == "ok"
    rejected = stack.compose(
        "exec",
        "-T",
        "postgres",
        "/bin/sh",
        "-c",
        "PGPASSWORD=deliberately-invalid exec psql -h 127.0.0.1 -U postgres -d postgres -c 'select 1'",
        check=False,
    )
    assert (
        rejected.returncode != 0 and "password authentication failed" in rejected.stderr
    )
    script = (
        "import urllib.request,urllib.error; "
        "\ntry: urllib.request.urlopen('http://qdrant:6333/collections',timeout=5)"
        "\nexcept urllib.error.HTTPError as e: assert e.code in (401,403)"
        "\nelse: raise SystemExit('unauthenticated Qdrant request unexpectedly succeeded')"
    )
    stack.compose("exec", "-T", "backend", "python", "-c", script)
    stack.compose(
        "exec",
        "-T",
        "backend",
        "python",
        "-c",
        "import importlib.util; assert importlib.util.find_spec('pytest') is None; "
        "assert importlib.util.find_spec('mypy') is None",
    )


def test_foundation_backend_cannot_start_as_placeholder(stack: Stack) -> None:
    services = stack.docker(
        "compose", *stack.source, "config", "--services"
    ).stdout.splitlines()
    assert set(services) == {"postgres", "qdrant", "ollama"}
    model = json.loads(json.dumps(stack.model))
    model["services"]["backend"]["image"] = stack.image
    file = stack.directory / "fail-closed-backend.json"
    file.write_text(json.dumps(model))
    result = stack.docker(
        "compose",
        "-f",
        str(file),
        "--profile",
        "backend",
        "run",
        "--rm",
        "--no-deps",
        "backend",
        check=False,
    )
    assert result.returncode != 0
    assert "no command specified" in result.stderr.lower(), result.stderr


def test_cross_network_client_cannot_reach_services(stack: Stack) -> None:
    stack.docker(
        "network",
        "create",
        "--internal",
        "--label",
        f"mimir.t02_test={stack.token}",
        stack.outside,
    )
    for name, port in (("postgres", 5432), ("qdrant", 6333), ("ollama", 11434)):
        network = stack.model["networks"]["private"]["name"]
        address = stack.info(name)["NetworkSettings"]["Networks"][network]["IPAddress"]
        script = (
            "import socket; "
            f"\ntry: socket.create_connection(({address!r},{port}),timeout=2)"
            "\nexcept OSError: pass"
            "\nelse: raise SystemExit('private service reachable from outside network')"
        )
        stack.docker(
            "run",
            "--rm",
            "--network",
            stack.outside,
            "--cap-drop",
            "ALL",
            stack.image,
            "python",
            "-c",
            script,
        )


def test_data_survives_container_removal_and_down_volumes(stack: Stack) -> None:
    assert stack.sql("show server_version;").startswith("18.")
    stack.sql(
        "create table t02_volume_probe (id integer primary key); insert into t02_volume_probe values (42);"
    )
    stack.api(
        "PUT",
        "/collections/t02_volume_probe",
        {"vectors": {"size": 1, "distance": "Cosine"}},
    )
    stack.compose(
        "exec",
        "-T",
        "ollama",
        "/bin/sh",
        "-c",
        'printf %s "$1" > /root/.ollama/t02-volume-probe',
        "t02-test",
        stack.token,
    )
    stack.compose("down", "--volumes", "--remove-orphans")
    for name in stack.volume_names:
        stack.docker("volume", "inspect", name)
    stack.compose("up", "-d", "--wait", "--wait-timeout", "120")
    stack.wait_ready()
    assert stack.sql("select id from t02_volume_probe;") == "42"
    assert stack.api("GET", "/collections/t02_volume_probe")["status"] == "ok"
    assert (
        stack.compose(
            "exec", "-T", "ollama", "cat", "/root/.ollama/t02-volume-probe"
        ).stdout
        == stack.token
    )


@pytest.mark.parametrize(
    "service,key",
    [("postgres", "postgres_bootstrap_password"), ("qdrant", "qdrant_api_key")],
)
def test_empty_credentials_fail_before_startup(
    stack: Stack, service: str, key: str
) -> None:
    path = stack.keys[key]
    original = path.read_bytes()
    try:
        path.write_bytes(b"")
        result = stack.compose("run", "--rm", "--no-deps", service, check=False)
        assert result.returncode != 0
        assert "required and must be nonempty" in result.stderr
    finally:
        path.write_bytes(original)


def test_explicit_linux_host_ollama_endpoint(stack: Stack) -> None:
    # Real Ollama in the host network models a host-local process, without
    # installing software on the runner or binding a LAN/wildcard interface.
    stack.compose("stop", "ollama")
    gateway = stack.gateway()
    stack.env["MIMIR_HOST_OLLAMA_IP"] = gateway
    host_model = json.loads(
        stack.docker(
            "compose",
            *stack.source,
            "-f",
            str(ROOT / "deployment/compose.host-ollama.yaml"),
            "--profile",
            "backend",
            "config",
            "--format",
            "json",
        ).stdout
    )
    ComposePolicy().validate(host_model, gateway)
    host_file = stack.directory / "host-only-probe.json"
    stack.write_probe_model(host_model, host_file)
    stack.docker(
        "run",
        "-d",
        "--name",
        stack.host_provider,
        "--network",
        "host",
        "--label",
        f"mimir.t02_test={stack.token}",
        "--cap-drop",
        "ALL",
        "--security-opt",
        "no-new-privileges:true",
        "-e",
        f"OLLAMA_HOST={gateway}:11434",
        "-v",
        f"{stack.project}_ollama:/root/.ollama",
        stack.model["services"]["ollama"]["image"],
        "serve",
    )
    stack.docker(
        "compose", "-f", str(host_file), "up", "-d", "--wait", "--wait-timeout", "120"
    )
    # The host Ollama can start after the topology; bounded retries stay in the
    # test client and do not implement application retry/provider behavior.
    script = (
        "import json,time,urllib.request; "
        "\nfor attempt in range(30):"
        "\n try:"
        "\n  result=json.load(urllib.request.urlopen('http://ollama:11434/api/tags',timeout=2)); "
        "assert result['models']==[]; break"
        "\n except OSError: time.sleep(0.5)"
        "\nelse: raise SystemExit('host-local Ollama endpoint unavailable')"
    )
    stack.compose("exec", "-T", "backend", "python", "-c", script)
