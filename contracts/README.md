# Canonical shared API contract (monorepo)

`/contracts` is the single backend-owned wire contract used by independent Python
and Go builds. This S01 baseline is a **candidate pending independent review and
explicit human approval**. It is not FastAPI-generated or runtime-conformant merely
because validation passes. The approved semantics are in
[FINAL_V1](../docs/decisions/FINAL_V1.md), [state machines](../STATE_MACHINES.md),
[HTTP semantics](HTTP_SEMANTICS.md), and [SSE protocol](SSE_PROTOCOL.md).

## Reproduce validation

Use Python 3.14.7 and local uv >=0.13.4,<0.14.0. CI and the digest-pinned
container use exactly uv 0.13.9. From the repository root:

```sh
cd backend
uv sync --locked --all-groups
uv lock --check
cd ..
make contract-check
make check
```

Initial locked installation needs PyPI access or a compatible populated uv cache.
After installation, `make contract-check` uses `uv run --offline --locked` and
requires no network or running application/service. Validation is read-only: it
never repairs artifacts, refreshes hashes, or approves a baseline. All files under
`contracts/` except the manifest itself are governed; symlinks and escaped paths
are forbidden. Hashes are SHA-256 of raw bytes, including whitespace/newlines.
The manifest cannot hash itself. Fixture membership and schema mappings are
explicit in [fixtures/index.json](fixtures/index.json).

Deliberate maintenance, followed by diff review and validation:

```sh
cd backend
uv run --offline --locked python ../scripts/update_contract_manifest.py
```

## Version and approval

`VERSION`, OpenAPI `info.version`, error-catalog version and manifest
`contract_version` must agree: initial candidate `1.0.0`. SSE `event_version: 1`
is the major event protocol discriminator, not a separate semver release.
`GET /api/v1/meta` discovers `contract_version` and `compatible_range` without
authentication or sensitive diagnostics. Application/package versions are independent.

There is no approved frozen predecessor for this first baseline. The manifest
status stays `candidate_pending_human_approval` throughout this PR; checks do not
turn it into approval. An approved baseline is identified by the reviewed merged
Git commit and its manifest, with the human approval recorded in the PR.
Once frozen, consumers use those canonical files at the approved commit; never
maintain independent contract copies in backend or CLI.

Every later public change requires a scoped contract PR, an explicit compatibility
diff against the last approved commit, independent review and human approval.
Breaking changes require an explicitly approved major-version/API migration;
compatible additions use a minor version; compatible corrections use a patch.
No automated pass or regenerated manifest authorizes a breaking change. Version
selection and approval are human review gates; S01 does not claim an automated
semantic breaking-change classifier. T09 and CLI-03 cannot silently mutate this baseline.

The operation/source/acceptance audit and actual execution evidence are recorded
in [the S01 review packet](../docs/verification/S01.md). Contract harness results
verify the agreement only. Actual FastAPI producer conformance belongs to T20/T28;
Go transport/consumer conformance belongs to CLI-03 and later integration gates.
