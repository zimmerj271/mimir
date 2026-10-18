# CLI-16 — End-to-end, performance, portability and packaging

**Status:** Ready for implementation planning; design-dependent API details remain contractual prerequisites  
**Repository:** `mimir-cli` (Go)  
**Dependencies:** CLI-01 through CLI-15  
**Backend prerequisites:** Contract-conformant FastAPI/PostgreSQL, fake inference and MemoryService, staging real provider where useful

## Objective

Deliver the bounded client capability described below, using the approved CLI architecture and the shared REST/SSE contract. Implement as an independent, reviewable feature branch/PR; do not extend the backend or duplicate its canonical responsibilities.

## In scope / deliverables

- CLI-to-server E2E suite.
- PTY smoke suite.
- deterministic fault injector.
- benchmarks and p95 measurements.
- distribution/install scripts for Linux/macOS.
- docs and review evidence.

## Explicit exclusions

- Requiring live Ollama for routine CI.
- Windows packaging.
- web UI delivery.

## Implementation and interface rules

- Read `../ARCHITECTURE.md`, `../IMPLEMENTATION_PLAN.md`, and the versioned API/SSE schemas before coding. Stop and raise an interface issue if a needed endpoint, enum, or behavior is undefined.
- Keep transport, domain client, local state, and UI layers separate. Use deterministic fakes for repeatable tests; never depend on the real model for routine CI.
- Preserve user isolation and private prompt handling; never write credentials or full prompt bodies to standard logs or the recovery journal.
- Use explicit cancellation and context propagation for background operations. CLI does not silently regenerate, mutate canonical archives, or cancel server inference on disconnect.
- Include README/interface notes for any exported Go package or user-visible command added by this task.

## Required tests and failure cases

- Lost POST ack, SSE disconnect, backend restart, auth renewal, isolation, sticky context, 10k-message scrolling, console restoration, piped output, both OS targets.

## Acceptance criteria

Correctness/recovery/compatibility gates pass; p95 goals benchmarked on pinned hardware with documented deviations; release artifact contains pinned dependencies and no credentials.

## PR evidence checklist

- [ ] Changes limited to this task's scope; any dependencies or contract changes documented.
- [ ] Unit/contract/PTY tests for applicable behaviors included and results reported accurately.
- [ ] Go formatting, lints, tests and race checks executed where applicable; commands and outcomes recorded.
- [ ] Sensitive-data and authorization boundaries reviewed; no test fixture secrets leak to output.
- [ ] CLI help and relevant architecture/contract references updated.
- [ ] Reviewer can reproduce the demonstration against deterministic fixtures or contract mock.

## Notes for implementer

Exact API paths, response types, error codes, session policy, and standalone run lifecycle must be taken from the normative shared contract when published. This brief is not permission to invent server semantics.

## Agent hardening gate
Apply repository `AGENTS.md` and language `CODING_STANDARDS.md`. Before writing observable behavior, check `SPECIFICATION_GAPS.md` and the frozen `contracts/` artifacts. Stop for missing, contradictory, or unapproved interface/state semantics; do not infer them. Map relevant acceptance IDs and tests in `TRACEABILITY.md`, execute applicable `QUALITY_GATES.md` commands, and report literal results. This gate does not expand the authorized scope of this task.

### Blocking branch acceptance checkpoint
At completion, **STOP**. Open one task-scoped PR using the root `PR_REVIEW_TEMPLATE.md`. Require successful documented checks, independent coding-agent review, explicit human review and affirmative merge approval. Do not merge autonomously or begin the next task before merge confirmation and a fresh human assignment. See `AGENTS.md` and `QUALITY_GATES.md`.

**V1 normative references:** `CONTRACT_DECISIONS_V1.md`, `STATE_MACHINES.md`, `PROMPT_AND_MEMORY_CONTRACT.md`, `contracts/openapi.yaml`, `contracts/sse.schema.json`, and `contracts/errors.json` at unified-package root. These are copied/pinned into independent repositories as reviewed contract artifacts.
