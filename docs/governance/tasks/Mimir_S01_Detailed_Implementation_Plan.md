# S01 — Shared Contract Foundation: Proposed Detailed Implementation Plan

**Status:** human reviewed and approved
**Task:** S01 · shared foundation
**Prerequisite:** S00 merged
**Suggested branch:** `feature/s01-shared-contract-foundation` (from latest `main`)
**Primary acceptance requirement:** `GOV-02`
**Downstream consumers:** T09 and later public backend HTTP tasks; CLI-03 and later Go client tasks

## 1. Objective

Turn the existing specification-stage artifacts in the repository-root `/contracts` directory into the **single versioned, checked, review-approved wire contract** consumed by the Python backend and Go client.

The task does not build API endpoints, the CLI, application service classes, database migrations, a production SSE publisher, or a deployed system. It must provide runnable *contract-level* tests now, while explicitly deferring **actual FastAPI producer conformance** to T20/T28 and **actual Go client conformance** to CLI-03 and its later integration gates.

A valid YAML document or matching SHA-256 manifest alone does not constitute S01 completion.

## 2. Authoritative references and permission boundary

Read, in order:

1. `AGENTS.md`; `docs/governance/TASK_GRAPH.json`; `docs/governance/IMPLEMENTATION_SEQUENCE.md`
2. `docs/decisions/FINAL_V1.md`; `CONTRACT_DECISIONS_V1.md`; `STATE_MACHINES.md`
3. `backend/API_CONTRACT.md`; `backend/ARCHITECTURE.md`; `cli/ARCHITECTURE.md`
4. `contracts/README.md`; `contracts/HTTP_SEMANTICS.md`; `contracts/SSE_PROTOCOL.md`
5. `contracts/openapi.yaml`; `contracts/sse.schema.json`; `contracts/errors.json`; `contracts/fixtures/*`; `contracts/manifest.json`
6. `docs/acceptance/REQUIREMENTS.md`; `TRACEABILITY.md`; `QUALITY_GATES.md`; `docs/verification/REPORT.md`; `PR_REVIEW_TEMPLATE.md`

S01 authorizes producing/repairing **validation code, fixtures, manifest, CI wiring, and task-scoped contract documentation**, subject to human approval. It does **not** authorize inventing unapproved wire behavior. Before changing any public request/response shape, authentication rules, status code, event semantics, versioning policy, or lifecycle rule, identify its exact approved source and escalate any unresolved gap.

The contract directory is protected. Obtain explicit human approval for proposed content changes to it and any other protected document or CI policy. Do not edit SQL migrations, application runtime handlers, CLI implementations, or unrelated tasks.

## 3. Deliverables

### A. Contract inventory and design-conformance matrix

Record each operation defined by OpenAPI (the baseline has **20 paths / 28 operations**) with:
- method, path, `operationId`, request body, parameters and headers;
- successful responses, error statuses, response media types and schema;
- authentication mode (bearer, cookie, anonymous where approved), browser CSRF/origin obligations;
- idempotency/optimistic-concurrency requirements;
- relevant acceptance and source-document references.

Compare the matrix to approved decisions. Resolve purely clerical inconsistencies within approved scope; **STOP** for substantive ambiguity requiring new owner decisions.

**Pay particular attention to:** cookie versus bearer authentication documentation and CSRF/origin requirements before T09; `/api/v1/auth/session` implicit renewal and prohibition of `/api/v1/auth/refresh`; `/api/v1/meta` discovery; 202 conversation deletion; 404 versus 410 operation recovery; 1 MiB *UTF-8 byte* limit; project/conversation pagination 50/200 and message pagination 100/500; branching modes; run cancellation request versus terminal state.

### B. Deterministic offline contract checker

Implement a single reproducible entrypoint, for example `scripts/check_contracts.py`, with tests under `tests/contracts/`. Use the existing pinned backend development environment or separately pinned contract-only development dependencies, not uncontrolled installs.

Checker must:
- parse OpenAPI 3.1 with an **OpenAPI-aware semantic validator**;
- parse and validate JSON Schema Draft 2020-12 for SSE;
- resolve references and validate documented example/fixture payloads;
- reject duplicate `operationId`s and unknown/invalid references;
- inspect error catalog shape/code/status consistency;
- verify `manifest.json` file membership and **SHA-256 of raw file bytes** (no paths escaping `/contracts`, no missing/extra governed artifacts, no implicit modification of content during validation);
- verify consistent `1.0.0` (or separately approved replacement) across relevant version markers and a documented semver/change-approval rule;
- compare the expected public surface/important semantic invariants to the approved design; tests for semantics not representable in JSON Schema must be implemented as explicit assertions.

A separate deliberate maintenance command may regenerate hashes; **checking must never regenerate hashes** or silently repair a failing manifest.

### C. Fixtures and stateful protocol tests

Retain and validate positive fixtures for all eight approved SSE event types and representative request/response/error schemas; add approved missing fixtures only where needed.

Test *protocol semantics*, not just individual event JSON objects:
- each frame's `event:` value matches the JSON discriminator;
- snapshot replaces provisional text, with `snapshot_sequence` high-water mark;
- only `message.delta` consumes contiguous text sequence numbers;
- gap, duplicate, reordered delta and invalid transition fail/trigger resubscription behavior as specified;
- heartbeat is a comment, not a data event;
- terminal events cannot be followed by an active transition;
- `GET /runs/{run_id}` remains authoritative for canonical results;
- 404 operation lookup cannot authorize automatic submission replay;
- 410 operation expiry is distinct from 404 unknown;
- cancellation requested does not imply terminal `cancelled`.

All tests should run **without an Ollama server, PostgreSQL, running FastAPI app, or completed CLI**. A lightweight contract-only producer/consumer fixture harness (including a generated/typed decoder check where feasible) can validate the agreement itself. Do **not** label such tests as real deployed-backend or real-CLI conformance.

### D. Makefile and CI wiring

Replace the current intentionally failing root `make contract-check` placeholder with a real deterministic test command.

Integrate contract validation into the existing repository CI and an appropriate aggregate check, while preserving all S00 checks. Ensure a modified or malformed contract fails a pull request; do not create a bypass or a standalone informational-only check.

Record exact commands and exit status; pin any newly introduced tooling under the repository's approved dependency management.

### E. Frozen contract version and governance

Once validation and human review approve the initial baseline:
- make the authoritative `/contracts` artifacts and all governed fixtures internally consistent;
- update the manifest and checksum using a deterministic, reviewable command;
- document how consumers find the canonical contract and the public `/api/v1/meta` version;
- specify public compatibility rules: breaking change → explicit approval and appropriate version bump; never quietly mutate frozen contracts in T09/CLI-03;
- retain a clear distinction between **candidate contract in a PR** and **approved contract at the merged commit**.

Do not copy `/contracts` into `cli/` or `backend/` as independent source-of-truth documents.

### F. Acceptance and evidence mapping

Map `GOV-02` to executable tests and record:
- checks on schema validity, references, manifest completeness/checksum, fixtures, errors and version;
- expected negative outcomes for each category;
- an explicit matrix of covered behaviors versus **deferred actual runtime producer/consumer tests**, with correct T20/T28/CLI-03 ownership;
- commands, test file paths, results, base SHA, contract SHA, unresolved risks.

Update task-scoped requirements/traceability only with permission; never mark unimplemented FastAPI or CLI tests as passed.

## 4. Required negative tests

At minimum:

1. Modified governed file without manifest refresh fails.
2. Missing/extra governed file or path-traversal manifest entry fails.
3. Corrupted YAML/JSON, unresolved `$ref`, duplicate operation ID, invalid schema or fixture fails.
4. Error code/status mismatch or version mismatch fails.
5. A fixture violating required/null/unknown-field constraints fails.
6. Invalid SSE discriminator/frame pairing, sequence gap/duplicate and impossible state transition fails.
7. Byte-limit tests distinguish UTF-8 bytes from character count; no truncation allowed.
8. Explicit assertions reject a refresh route and regressions to approved 202/404/410, pagination and session rules.
9. CI demonstrably fails when any contract checker is made to fail.
10. Breaking changes without a valid version/approval procedure are blocked, or are explicitly reported as a human-approval gate (not self-approved by automation).

## 5. Acceptance checklist

S01 is ready for review when:
- [ ] The operation/source/acceptance matrix is complete; gaps or conflicts are resolved with owner approval, or S01 is stopped with a precise blocker.
- [ ] Machine-readable schemas and governed fixtures pass an OpenAPI-aware validator and schema/fixture checks.
- [ ] The manifest covers all governed files and passes deterministic raw-byte checksum checks.
- [ ] Real positive and negative contract-level tests run successfully.
- [ ] `make contract-check` exists, works offline, and is enforced by CI alongside existing gates.
- [ ] Contract version, immutable baseline/proposed change process, and source-of-truth rules are documented.
- [ ] `GOV-02` has actual test evidence; future real backend/CLI conformance is clearly marked **not yet verified**.
- [ ] One task-scoped PR includes a reviewed diff, exact command outputs and provenance.
- [ ] A separate agent independently reviews the PR; explicit human approval occurs before merge.
- [ ] The agent **STOPS** after PR submission/review and does not start T09 or CLI work.

## 6. Suggested coding-agent assignment

> Implement **S01 — Shared Contract Foundation** on a fresh branch from current `main`.
>
> Read `AGENTS.md`, `docs/decisions/FINAL_V1.md`, the canonical task graph, `/contracts` artifacts, `QUALITY_GATES.md`, and the S01 plan provided with this assignment. S01 is a shared contract validation/freeze task, **not** a prerequisite to starting CLI development and **not** permission to implement CLI or HTTP runtime code.
>
> First audit the contract against the authoritative decisions. Identify conflicts, especially cookie/bearer/CSRF representation and any undefined request or response behavior. Stop and ask for owner approval before changing any public semantics or protected contract definition beyond the expressly approved S01 scope.
>
> Implement offline contract checks, positive and negative fixtures/tests, deterministic manifest verification, `make contract-check`, and CI enforcement. Map `GOV-02` to real test evidence. Do not claim backend producer or Go CLI conformance before those tasks exist.
>
> Submit one scoped PR with the precise checks run, results, files changed, remaining gaps, contract version and SHA. **STOP for independent review and human merge approval.** Do not start T09 or other tasks.

## 7. Important consistency notes in current design

- The task graph and task indices make **S01** a prerequisite of T09; T09's individual task brief lists only T08. Treat the graph as canonical and reconcile the task brief under separately authorized governance/document scope.
- The S01 brief includes boilerplate about DEPLOY-01 and CLI-12A/B, also seen in unrelated briefs. This is not a dependency.
- The baseline manifest is `draft_for_S01_human_approval` with version `1.0.0`; local archive file membership and hashes match at the time of this review. That is **not** a frozen or implemented contract.
- The current root Makefile still reports `NOT IMPLEMENTED` for `contract-check`.
- The current OpenAPI specifies HTTP bearer security, while normative backend design also requires web session cookies with CSRF/origin protections. Audit their complete, approved wire representation before T09; do not improvise a cookie mechanism.
