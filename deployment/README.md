# T02 Compose and service isolation

Base main/T01 merge: `d12e3362a5c0c2e6a348534db2558bf8534467ea`. The approved
[FINAL_V1](../docs/decisions/FINAL_V1.md), [operating contract](../backend/OPERATIONS.md)
and [task graph](../docs/governance/TASK_GRAPH.json) govern this foundation.
Acceptance: DEPLOY-01. This is not the later connected installer/release gate.

## Delivered boundary

`compose.yaml` defines PostgreSQL 18.4, Qdrant 1.19.2, Ollama 0.40.6, external
persistent volumes and an externally isolated private network. Service images,
Python 3.14.7 and uv 0.13.9 build stages are pinned by verified registry digests.
The backend image installs only the locked runtime package; Compose runs it as
65532:65532, drops capabilities and makes its filesystem read-only. Build context
is allowlisted, excluding host environments, secrets and caches.

Qdrant telemetry and Ollama cloud features are explicitly disabled using the
configuration supported by the pinned vendor releases.

The backend is wiring in a disabled `backend` profile. Its sole host binding is
`127.0.0.1:8000`; data/model services have no published ports. There is no server
entrypoint, idle placeholder or fake health response. Even explicit activation
fails without a command. Later application tasks must supply the real server
and compatible-schema readiness gate. T03 adds the explicitly selected
[Flyway/role provisioning overlay](database/README.md); it does not run as part of
this default T02 topology. No administrator or migration secret is given to the backend.

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
>=0.13.4,<0.14.0 and Python 3.14.7. Commands below run from the monorepo root. Setup
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

## Compose networking in every profile

Backend uses `http://ollama:11434` through Compose service DNS. Ollama remains on
the private network, uses the existing external `ollama_data` volume and publishes
no host port. All optional services follow the same networking boundary:

| Configuration | Services and network attachments |
|---|---|
| Default `compose.yaml` | PostgreSQL, Qdrant and Ollama on `private` |
| Explicit `backend` profile | Backend on `private` and the loopback-publishing `api` bridge; providers stay on `private` |
| Explicit migration overlay/profile | `role-bootstrap`, `flyway` and `role-credentials` on `private`; backend/provider attachments are unchanged |

The backend profile is still disabled until application tasks supply its actual
command/readiness checks. The migration overlay is an operator-only action through
the [database provisioning guide](database/README.md), not part of default startup.
No service uses host networking, a host-gateway provider alias or a per-deployment
host-firewall exception for internal service communication.

The [owner-approved networking correction](../docs/decisions/COMPOSE_NETWORKING_CORRECTION.md)
retires the native host-installed Ollama alternative. Remove
`-f deployment/compose.host-ollama.yaml`, `MIMIR_HOST_OLLAMA_IP` and `--host-ollama`
from deployment commands and use the
foundation startup above. The removed flag fails explicitly; it does not silently
select a provider. Existing container model volumes are retained. Native host model
files are not imported or deleted, and native processes/firewall rules are not
changed automatically. Model provisioning remains a separately informed action.

Linux/macOS v1 targets are unchanged. Native macOS container execution and complete
installation acceptance remain DEPLOY-01 work; these instructions are not a
platform support or release-readiness claim.

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
actual loopback bindings, Compose-only provider DNS and the default/backend/migration
profile combinations. The database suite additionally inspects all three optional
migration jobs' live private-network attachments. Backend commands
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
`python:3.14.7-slim-bookworm`, `ghcr.io/astral-sh/uv:0.13.9` manifests.
Relevant upstream references:
[Postgres 18 image data layout](https://hub.docker.com/_/postgres),
[private networks](https://docs.docker.com/reference/compose-file/networks/),
[external volumes](https://docs.docker.com/reference/compose-file/volumes),
[host bindings](https://docs.docker.com/reference/compose-file/services/).

Rollback is a separately authorized reviewed revert; it removes neither stored
data nor credentials. No applied migrations exist in this task. Stop after opening
the PR: agent review, human review and explicit merge authorization remain pending.


S01's owner-authorized uv CLI upgrade pins the 0.13.9 multi-platform image to
`sha256:d3b601f0c7b118bc1753febf7c41cc80981df678445e0254284eecf84db79a7d`.
The digest/version and linux/amd64 + linux/arm64 entries were verified against
[Astral's published GHCR package metadata](https://github.com/astral-sh/uv/pkgs/container/uv/versions?filters%5Bversion_type%5D=tagged).
Direct registry retrieval/build verification is recorded separately in
[S01 evidence](../docs/verification/S01.md); publisher metadata verification does
not claim an executed container build. Python image/version/digest are unchanged.
