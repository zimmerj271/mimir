> **Approved final task boundary:** Verification only; T18/T19 own SSE/status/cancellation execution. See `../../docs/decisions/FINAL_V1.md`. Any incompatible legacy task wording is not authorized work.

# T27 — Unified run status, SSE and cancellation contracts

**Milestone:** M4 (backend client integration)  
**Branch:** `feature/t27-run-recovery-contract`  
**Dependencies:** T18, T19, T20, T21, T22  
**Status:** Design brief only; no code or tests claimed complete.

## Objective and in-scope work
Ensure run snapshot/live SSE handoff, authoritative REST status/checkpoint, explicit idempotent cancellation, terminal outcome, backend-restart interruption work identically for standalone and conversation run types.

## Boundaries and exclusions
This is a **Python Mimir Server** task. No Go/Cobra/Bubble Tea code, CLI credentials, local CLI sticky state, UI rendering or frontend packaging. Backend owns canonical schema, authorization, runtime lifecycle and REST/SSE. Do not add app-owned managed-memory tables, per-memory public APIs, new inference engines, hosted-model fallback or a distributed event bus. Changes outside this one task require review.

## Prerequisites and contractual outputs
Read `../ARCHITECTURE.md`, `../API_CONTRACT.md`, `../DATABASE_DESIGN.md`, `../ACCEPTANCE_TESTS.md`, `../AGENTS.md` and cross-client contracts. Use approved canonical `/contracts`; protected schema/SQL changes require separate explicit human authorization. Preserve owner-scoped invariants. Work against approved PostgreSQL 18 Flyway migrations V001–V005; never edit an applied migration. New migration requests require separate human authorization.

## Tests and acceptance
Deterministic reconnect and cancel/complete races; no delta replay guarantee; no duplicate final revision; protected standalone result. Include positive and negative tests, concurrency and failure injection where pertinent. Run formatting, typing, targeted unit suites and applicable real PostgreSQL/HTTP tests; record literal results.

## Delivery gate
One reviewable branch and one PR. Independent review validates diff, contracts, tests, security and scope; user approval required to merge. Stop after this task; do not begin another automatically.

## Agent hardening gate
Apply repository `AGENTS.md` and language `CODING_STANDARDS.md`. Before writing observable behavior, check `SPECIFICATION_GAPS.md` and the frozen `contracts/` artifacts. Stop for missing, contradictory, or unapproved interface/state semantics; do not infer them. Map relevant acceptance IDs and tests in `TRACEABILITY.md`, execute applicable `QUALITY_GATES.md` commands, and report literal results. This gate does not expand the authorized scope of this task.

### Blocking branch acceptance checkpoint
At completion, **STOP**. Open one task-scoped PR using the root `PR_REVIEW_TEMPLATE.md`. Require successful documented checks, independent coding-agent review, explicit human review and affirmative merge approval. Do not merge autonomously or begin the next task before merge confirmation and a fresh human assignment. See `AGENTS.md` and `QUALITY_GATES.md`.

**V1 normative references:** `CONTRACT_DECISIONS_V1.md`, `STATE_MACHINES.md`, `PROMPT_AND_MEMORY_CONTRACT.md`, `contracts/openapi.yaml`, `contracts/sse.schema.json`, and `contracts/errors.json` at unified-package root. These are consumed from the one shared `/contracts` directory in the monorepo; without separate copies.
