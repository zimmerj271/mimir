# Compose networking correction — owner-approved T02 follow-up

## Approval and scope

The owner approved this correction after diagnosing the existing host-Ollama
deployment test during T05, and explicitly authorized implementation in a separate
checkout/PR. Base `main`: `105d4747299ab317c0d5dda5469d892bcdbc66e2`.
Branch: `feature/t02-compose-networking-correction`. Staged T05 schema work remains
in its original checkout and is not part of this correction.

This decision supersedes the earlier host-installed Ollama alternative in
[FINAL_V1](FINAL_V1.md), the [backend architecture](../../backend/ARCHITECTURE.md)
and [operations](../../backend/OPERATIONS.md). The supported v1 deployment uses
Compose-managed Ollama in every configuration. This is an explicit architectural
correction, not a waiver of a failing test or a change to host firewall policy.

## Error and evidence

The earlier optional overlay removed the normal Ollama container from the active
configuration and mapped backend's `ollama` hostname to the host's private bridge
gateway. Its integration test used a separate `docker run --network host` Ollama
container to model a native host process. That container was outside the Compose
service-policy validation, even though Compose service host networking was already
forbidden. This made the smoke test depend on host input-firewall rules.

The original T02 test did run and pass in Ubuntu CI: **7 passed in 98.91s** in
[the original PR check](https://github.com/zimmerj271/mimir/actions/runs/39172720111).
That historical result is retained. It did not establish compatibility with an
active deny-incoming UFW policy. The old setup instructions omitted that dependency.

On the development host, the unchanged `main` test failed with
`host-local Ollama endpoint unavailable`. A controlled reproduction established:

- Default Compose Ollama was running/healthy and backend received `{"models":[]}`.
- Host-mode Ollama listened on the intended bridge gateway and responded locally.
- Backend's hostname resolved to that same gateway.
- Kernel logs recorded `[UFW BLOCK]` for TCP SYN packets from the diagnostic
  backend (`172.19.0.2`) to the host gateway (`172.19.0.1`) on port `11434`.
- The host input policy was DROP, with no allow rule for that connection. The
  request failed before HTTP or model inference. No model download was involved.

The diagnostic addresses identify one disposable test network; they are not
deployment defaults. Inspection and a UFW syntax dry-run changed no firewall rules.
The owner rejected per-deployment firewall exceptions and selected Compose-only
container networking instead. Native host processes and firewall configuration
must not be altered implicitly by this correction.

## Required topology

| Service/configuration | Required networking |
|---|---|
| PostgreSQL, Qdrant, Ollama | Compose `private` network; no published ports |
| Explicit backend profile | Compose `private` plus `api`; only `127.0.0.1:8000` published |
| Optional role bootstrap, Flyway, role credentials | Compose `private` network; no published ports |

All internal endpoints use Compose service DNS. Host networking and `extra_hosts`
provider overrides are rejected for every service/job. The three optional
migration jobs already followed this topology and remain unchanged. No duplicate
Ollama service, new network, proxy, firewall rule or runtime dependency is needed.

Remove the host overlay, retired host flag/address variable, host-gateway validator
exception and host-network smoke container. Reuse the existing Ollama container,
pinned image, retained external volume and cloud-disable setting. Test every
supported profile combination and preserve credential, isolation and persistence
coverage. The native host-installed alternative is no longer supported in v1.

## Verification and recovery boundaries

Acceptance: DEPLOY-01 and T02's supporting infrastructure subset of OPS-01. Coverage
is in `backend/tests/test_compose_policy.py`, `backend/tests/test_migrations.py`,
`tests/integration/test_compose.py` and `tests/integration/test_database.py`.
Exact commands, counts, versions and the submitted SHA belong to the correction PR;
this record does not claim an unexecuted check passed. Independent review and
explicit human merge authorization remain required.

Existing operator volumes/credentials are retained. Remove the retired overlay,
host-address variable and flag from invocation and use the normal Compose startup
in the [deployment guide](../../deployment/README.md). Native host model files are
not automatically imported/deleted and host processes are not automatically stopped.
Model provisioning still requires informed administrator action.

There are no SQL, public contract, dependency or CI enforcement changes. Reverting
source does not alter data or host firewall rules. Any rollback that restores the
retired architecture requires separately reviewed owner authorization. Complete
installation/native macOS runtime, inference, model provisioning and T05's archive
schema remain outside this correction.
