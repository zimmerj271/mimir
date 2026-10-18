# T02 Compose and service isolation

Base main/T01 merge: `d12e3362a5c0c2e6a348534db2558bf8534467ea`. The approved
[FINAL_V1](../docs/decisions/FINAL_V1.md), [operating contract](../backend/OPERATIONS.md)
and [task graph](../docs/governance/TASK_GRAPH.json) govern this foundation.
Acceptance: DEPLOY-01. This is not the later connected installer/release gate.

## Delivered boundary

`compose.yaml` defines PostgreSQL 18.4, Qdrant 1.19.2, Ollama 0.40.6, external
persistent volumes and an externally isolated private network. Service images,
Python 3.14.7 and uv 0.12.10 build stages are pinned by verified registry digests.
The backend image installs only the locked runtime package; Compose runs it as
65532:65532, drops capabilities and makes its filesystem read-only. Build context
is allowlisted, excluding host environments, secrets and caches.

Qdrant telemetry and Ollama cloud features are explicitly disabled using the
configuration supported by the pinned vendor releases.

The backend is wiring in a disabled `backend` profile. Its sole host binding is
`127.0.0.1:8000`; data/model services have no published ports. There is no server
entrypoint, idle placeholder or fake health response. Even explicit activation
fails without a command. Later application tasks must supply the real server
and compatible-schema readiness gate. T03 owns Flyway/roles: no runner or SQL is
implemented here, and no bootstrap administrator secret is given to the backend.

Only the backend also joins an API bridge, whose default host binding is
127.0.0.1. Docker suppresses host publishing for an internal-only network; this
separate bridge supplies the publishing path. Data/model services attach only
to the internal network, and backend IPv4 forwarding is explicitly disabled.

Qdrant requires a file-backed API key; PostgreSQL TCP authentication uses SCRAM.
Wrappers read protected 0600 files before invoking upstream entrypoints, keeping
values out of Compose interpolation, argv and logs. PostgreSQL drops to its
postgres user. Qdrant uses its upstream root user with only DAC_OVERRIDE added
after dropping capabilities, to read a protected host-owned file. Internal service
credentials do not implement Mimir user/session authentication.

## First foundation startup

Requires Docker Engine access, Compose **5.5.1** (tested and pinned in CI), uv
0.12.10 and Python 3.14.7. Commands below run from the monorepo root. Setup
explicitly downloads pinned service/build images and locked tooling, not models.
The data/model private network has no external egress. The backend's separate API
bridge is a standard Docker bridge; it does not provide an egress firewall for
future application code or authorize hosted inference.

Create secrets once. This refuses an existing directory and never overwrites
credentials during restart or upgrade:

```sh
uv run --project backend --locked python - <<'PY'
import os
from pathlib import Path
import secrets
os.umask(0o077)
directory = Path('deployment/secrets')
directory.mkdir(mode=0o700)
for name in ('postgres_bootstrap_password', 'qdrant_api_key'):
    with (directory / name).open('x') as stream:
        stream.write(secrets.token_urlsafe(48))
PY
docker volume create mimir_postgres
docker volume create mimir_qdrant
docker volume create mimir_ollama
make compose-config
docker compose -f deployment/compose.yaml up -d --wait
```

`make compose-config` requires nonempty caller-owned 0600 secret files in a 0700
directory, rejecting symlinks and unsafe permissions. To provision other protected
paths, use `MIMIR_POSTGRES_PASSWORD_FILE` and `MIMIR_QDRANT_API_KEY_FILE`; never put
plaintext keys in the Compose environment. These are deployment inputs, not CLI
connection profiles. Keep the original files with existing volumes: changing a
password file does not rotate an existing PostgreSQL database's password.

The default project is `mimir`. An explicit `COMPOSE_PROJECT_NAME` selects matching
`<project>_postgres`, `<project>_qdrant`, `<project>_ollama` external volumes; provision
those names when using another project. Missing external volumes fail startup
instead of silently replacing data. Stop using
`docker compose -f deployment/compose.yaml down`. External volumes survive even
`down --volumes`, container removal and image rebuilds. No lifecycle helper deletes
them. Backup/restore, encryption/retention, major upgrades and the connected
installer remain separately owned tasks.

## Explicit host-installed Ollama endpoint

Container Ollama is the default. Explicitly select `compose.host-ollama.yaml` for
the standalone-Linux bridge case, preserving internal `ollama:11434` naming.
Start the foundation network first; inspect its actual IPv4 gateway:

```sh
docker network inspect mimir_private --format '{{(index .IPAM.Config 0).Gateway}}'
```

Set `MIMIR_HOST_OLLAMA_IP` to that address. In another terminal, explicitly run
the selected host Ollama binary (0.40.6) with
`OLLAMA_NO_CLOUD=true OLLAMA_HOST="$MIMIR_HOST_OLLAMA_IP:11434" ollama serve`. Bind only that dedicated
bridge address, never a LAN/wildcard interface. Then validate and select the mode:

```sh
uv run --project backend --locked python deployment/validate_compose.py --host-ollama
docker compose -f deployment/compose.yaml -f deployment/compose.host-ollama.yaml up -d --wait --remove-orphans
```

Validation compares the alias with the actual existing private bridge gateway;
loopback, arbitrary endpoints and mismatched gateways fail. This removes container
Ollama from the active set without deleting its model volume. No host configuration
or provider choice is silently changed. A Linux bridge alias does not reach a
host's 127.0.0.1-only listener. Native Docker Desktop/macOS host connectivity and
installation lifecycle remain unverified for DEPLOY-01; the v1 Linux/macOS target
is unchanged. This is not a platform support or release-readiness claim.

No model provisioning, GPU scheduling, Mem0 adapter, automatic downloads or
unapproved network relay is included. Informed model provisioning/egress remains
later task evidence.

## Verification and provenance

`make backend-check` includes policy/script/integration-test formatting, lint and
strict script typing plus deterministic policy failure tests. `make check` keeps
root governance validation. `make deployment-check` requires Docker and creates
a unique labelled test stack; it fails rather than skipping without an engine.
Only owned test resources are eligible for cleanup; operator volumes are retained.

Real-engine tests cover compiled topology, external volume survival after
`down --volumes`, PostgreSQL data/Qdrant collection/model-volume persistence,
rejected service authentication, empty-secret rejection, cross-network isolation,
actual loopback bindings and a Linux host-local Ollama endpoint. Backend commands
and health checks in the suite are explicit test-only HTTP port probes. A separate
test proves the foundation image cannot start a placeholder API and excludes
development tools. These do not prove application auth, readiness or API behavior.

Both required CI jobs run deterministic package/policy gates. Linux additionally
runs the real-engine suite with pinned Compose 5.5.1; macOS does not claim container
runtime evidence. Local Docker socket denial must be reported separately from CI
execution. Exact command outcomes and review SHA belong to the T02 PR; prose and
static tests do not establish live-service acceptance.

Digests were resolved using `docker buildx imagetools inspect` against official
`postgres:18.4`, `qdrant/qdrant:v1.19.2`, `ollama/ollama:0.40.6`,
`python:3.14.7-slim-bookworm`, `ghcr.io/astral-sh/uv:0.12.10` manifests.
Relevant upstream references:
[Postgres 18 image data layout](https://hub.docker.com/_/postgres),
[private networks](https://docs.docker.com/reference/compose-file/networks/),
[external volumes](https://docs.docker.com/reference/compose-file/volumes),
[host bindings](https://docs.docker.com/reference/compose-file/services/).

Rollback is a separately authorized reviewed revert; it removes neither stored
data nor credentials. No applied migrations exist in this task. Stop after opening
the PR: agent review, human review and explicit merge authorization remain pending.
