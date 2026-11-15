# T02 — Compose and service isolation

**Milestone:** M1
**Branch:** `feature/t02-compose-foundation` (from updated `main`)
**Dependencies:** T01
**Status:** Implementation and applicable validation complete; submitted in [PR #3](https://github.com/zimmerj271/mimir/pull/3) for independent agent review and human review. Explicit human merge authorization completed; Merged.

Acceptance IDs: DEPLOY-01 (docs/acceptance/REQUIREMENTS.md); OPS-01 supporting
infrastructure persistence/isolation coverage only, with application checks deferred.

## Objective / authorized scope
Define backend, PostgreSQL, Qdrant, Ollama integration endpoint, persistent volumes and isolation.

## Owner-approved networking correction assignment

After T02 merged, the owner explicitly approved a separate deployment correction
following diagnosis during T05. Work from updated `main` at
`105d4747299ab317c0d5dda5469d892bcdbc66e2` on
`feature/t02-compose-networking-correction`; leave staged T05 work in its original
checkout. T01 and the original T02 prerequisites are merged. The original PR #3
and its passing CI remain historical evidence, not a claim about this correction.

All container services and optional jobs must use Compose networks and service
DNS. Retire the native host-installed Ollama overlay, gateway aliases/flags and
the host-network container used by its smoke test. Reuse Compose-managed Ollama
and its retained volume. The three optional migration jobs already use the
private network and must continue to do so. Only backend joins the API bridge;
data/model ports remain unpublished and host API publishing remains loopback-only.

Reconcile the decision ledger, root README, backend architecture/operations,
deployment instructions and acceptance traceability. Record the root cause,
previous CI outcome and explicit owner-approved architecture change in
`docs/decisions/COMPOSE_NETWORKING_CORRECTION.md`. Preserve historical test evidence.
Cover supported Compose profiles, real service DNS and every service/job's rejection
of host-network/host-address overrides, retaining persistence/credential/isolation
tests. No host-firewall changes, application/schema/contract work, new dependencies
or future inference features are included. Run quality, deployment and database
regression gates; open one correction PR and stop for independent and human review.

## Out of scope
Do not implement successor tasks, change shared contracts or introduce major dependencies without approval. No public per-memory CRUD, application-owned canonical memory tables, hidden direct client database/provider access, or future-web implementation.

## Required test scenarios and acceptance
Compose config validates; persistence and network exposure are checked; no secrets in Git. Add pertinent failure, auth, boundary and concurrency tests. Prefer deterministic unit tests where possible and real-service integration tests when persistence/provider guarantees are involved.

## Required engineering review
- Read `AGENTS.md`, `CODING_STANDARDS.md`, `ARCHITECTURE.md` and relevant domain documents before coding.
- Favor cohesive OOP services/models/adapters, composition and dependency injection. Do not introduce speculative frameworks or standalone business-logic functions.
- Formatting, linting, type checks, targeted tests and relevant integration checks must have **actual logged results**.
- Reviewer independently checks changed files, contracts, security, quality, tests and scope.
- No direct push to `main`; submit one PR from `feature/t02-compose-foundation`, obtain user approval, then merge.
- Stop at this brief's boundary; do not start a dependent task automatically.

## PR evidence / completion template
- Branch and base commit; changed paths; concise design rationale.
- Acceptance cases → test files → exact commands → results or blockers.
- New or modified public interfaces, migrations, dependencies.
- Unresolved risks and architectural deviations (explicitly none if absent).
- Reviewer outcome and user approval (pending until granted).

## Agent hardening gate
Apply repository `AGENTS.md` and language `CODING_STANDARDS.md`. Before writing observable behavior, check `SPECIFICATION_GAPS.md` and the frozen `contracts/` artifacts. Stop for missing, contradictory, or unapproved interface/state semantics; do not infer them. Map relevant acceptance IDs and tests in `TRACEABILITY.md`, execute applicable `QUALITY_GATES.md` commands, and report literal results. This gate does not expand the authorized scope of this task.

### Blocking branch acceptance checkpoint
At completion, **STOP**. Open one task-scoped PR using the root `PR_REVIEW_TEMPLATE.md`. Require successful documented checks, independent coding-agent review, explicit human review and affirmative merge approval. Do not merge autonomously or begin the next task before merge confirmation and a fresh human assignment. See `AGENTS.md` and `QUALITY_GATES.md`.

**V1 normative references:** `CONTRACT_DECISIONS_V1.md`, `STATE_MACHINES.md`, `PROMPT_AND_MEMORY_CONTRACT.md`, `contracts/openapi.yaml`, `contracts/sse.schema.json`, and `contracts/errors.json` at unified-package root. These are consumed from the one shared `/contracts` directory in the monorepo; without separate copies.
