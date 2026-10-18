> **Normative v1 reconciliation:** See `../docs/decisions/FINAL_V1.md` for the approved deployment, lifecycle, contract and governance rules. Historical conflicting implementation suggestions do not authorize changes.

# Mimir CLI/TUI — Independent Implementation Plan

**Status:** Initial implementation baseline, derived from `ARCHITECTURE.md`  
**Date:** 2026-10-17  
**Repository:** planned `mimir-cli` (Go); implementation independently tracked from Mimir Server

## 1. Authority and execution rules

Read this plan with the companion `ARCHITECTURE.md` and the versioned Mimir Server OpenAPI 3.1 + normative SSE contract. In conflicts, raise a design/contract issue: do not silently infer client-owned backend data or alter approved semantics. Treat tasks below as **CLI-specific**, not as replacements for backend T20–T28. The backend plan must separately reconcile its existing client-specific tasks into backend-facing integration obligations.

Each implementation task should be a narrowly scoped branch/PR with an explicit interface, tests, commands/results, and a factual acceptance report. Pin dependencies. Use deterministic mocks to enable parallel server/client development. Introduce no unapproved end-user features or memory-provider APIs.

**Definition of done for each task:** required functions exercised by automated tests, errors and cancellation covered, no leaked credentials/content in logs, documented interfaces and manual/PTY evidence where terminal-specific. Functional changes must not depend on live Ollama to pass CI.

## 2. Dependency phases

```text
CLI-01 (Go/Cobra/toolchain)
   |-- CLI-02 (paths/config/state primitives)
   |      |-- CLI-04 (secure sessions)
   |      |-- CLI-06 (sticky context)
   |      `-- CLI-07 (operation journal)
   |
   |-- CLI-03 (typed REST/SSE client)
   |      |-- CLI-04 (sessions)
   |      |-- CLI-05 (resource commands)
   |      |-- CLI-07 (run recovery)
   |      `-- CLI-08 (one-shot ask/export)
   |
   `-- CLI-09 (TUI shell)
          |-- CLI-10 (composer/selectors)
          |-- CLI-11 (history store/paging)
          |        `-- CLI-12 (viewport/render/anchoring)
          |-- CLI-13 (themes)
          `-- CLI-14 (stream state integration)
                    `-- CLI-15 (cancellation/recovery UX)

CLI-16: Integrated system, performance, portability, release validation
```

Dependencies in the diagram are illustrative ordering constraints; exact task preconditions are specified below. Resource commands and TUI components may be tested using contract stubs independently.

The individual reviewable task briefs are indexed in [`TASK_INDEX.md`](TASK_INDEX.md), with one file per task under [`tasks/`](tasks/).

## 3. Task specifications

### CLI-01 — Go module, Cobra executable and quality baseline

**Scope:** Pin Go; create `mimir` executable, Cobra root/help/completions, exit-code conventions, Linux/macOS build targets, lint/format/test/benchmark targets and a package structure that does not entangle backend transport with TUI. Resolve dependency compatibility for Bubble Tea v2/Bubbles v2/Lip Gloss v2/Glamour v2. **Tests:** help/version/completion, invalid flags, noninteractive terminal behavior, compile/test on both supported OS. **Excluded:** network or application workflows.

### CLI-02 — Config, paths and safe local state primitives

**Depends:** CLI-01. **Scope:** TOML preference validation, native macOS and Linux/XDG locations, deployment-provisioned backend endpoint lookup, atomic file writes, scoped local account/backend state storage and short file locking; `mimir config show|path|set|unset`. No general environment override system, `--server` flag, or prompt cache. **Tests:** precedence, malformed schema, permissions, partial write, concurrent updates, correct namespace isolation.

### CLI-03 — Typed canonical REST and SSE transport

**Depends:** CLI-01 and published shared API schema/stubs. **Scope:** generate or implement verified typed REST requests/responses, auth injection hook, structured error mapping, timeouts, SSE event parser, snapshot-to-live boundary, bounded buffers and retry classification; no direct backend DB/provider access. **Tests:** mock API contract fixtures, malformed stream, status mappings, identity-mismatched resource response rejection where applicable, lost connections and snapshot ordering. **Excluded:** UI decisions and automatic resubmission with fresh keys.

### CLI-04 — Login and secure persistent session lifecycle

**Depends:** CLI-02/03 and backend auth contract. **Scope:** masked username/password prompt, macOS Keychain/Linux Secret Service adapters, opt-in protected-file fallback, server-managed transparent renewal, logout local clearance/server revocation, reauth without TUI state loss. **Tests:** secure-storage absence, expired/renewable/revoked session, simultaneous client access, auth error during reconnection, no secret leakage. **Excluded:** OAuth, Windows and automation tokens.

### CLI-05 — Resource commands and canonical project mutations

**Depends:** CLI-03/04, backend resource contracts. **Scope:** projects/conversations list, conversation creation and association, branch/resume target metadata, `assign`/`unassign`, version-check and ownership-error presentation. **Tests:** no client-side direct mutations, project mismatch rejection, stale-version conflict and branch-independent reassignment. **Excluded:** terminal selectors or model execution.

### CLI-06 — Sticky context and target resolution

**Depends:** CLI-02/05. **Scope:** account/backend-scoped `active_project` and `active_conversation`; exact precedence; clear commands and `mimir context`; explicit selection commits with generation-safe concurrency; TUI navigation separate. **Tests:** each precedence path, unassigned vs standalone distinction, clear project clears both, stale context, concurrent command update, no unknown ID adoption on failed creation.

### CLI-07 — Operation journal, run status and reattach core

**Depends:** CLI-02/03/04 and backend operation-lookup/run-status contracts. **Scope:** persist metadata before send, no prompt bodies, resolution after crash, pruning, `mimir run status|attach`; idempotency identity stability; bounded status polling/reattachment. **Tests:** crash at pre-send/post-commit/post-run-ID boundaries, server lookup unavailable, permission errors, concurrent writers, no payload retention. **Excluded:** full submission replay without payload.

### CLI-08 — One-shot ask and full history export

**Depends:** CLI-03/04/05/06/07 and backend standalone-run contract. **Scope:** standalone vs persisted ask resolution, project-context creation, conversation append, stdin prompt context separation, clean stdout/stderr/exit-status contract, `mimir history` complete cursor traversal and Markdown/JSON export. **Tests:** pipes, TTY/non-TTY formatting, large exports, page faults, memory read/no ingestion assertion via backend fake, accepted-but-unobserved run, conflict/prompt handling. **Mandatory specification:** decide stdout partial-output behavior on snapshot rollback before claiming fully reconciled streamed output.

### CLI-09 — Bubble Tea application shell

**Depends:** CLI-01/02. **Scope:** full-screen alternate screen, app message/state architecture, resize, status/header/composer placeholders, focus/keymap, mouse capability behavior, spinner component. **Tests:** headless model tests and PTY start/exit/resize; no sidebars/popups. **Excluded:** actual history or backend stream workflows.

### CLI-10 — Composer and context selectors

**Depends:** CLI-05/09. **Scope:** draft editor, keyboard actions, in-viewport `/conversation` and `/project` selectors, `/new`, `/model`, `/status`, `/help`, `/exit`, switching conversation without cancel, latest active backend discovery. **Tests:** drafts retained, rapid switching, invalid/deleted selector target, focus/Esc semantics, no unintended sticky one-shot modifications.

### CLI-11 — Canonical message page store and cursor fetching

**Depends:** CLI-03/09, backend cursor contract. **Scope:** initial approximately 30–40 messages, older-page cursor loading/prefetch, duplicate in-flight suppression, latest/frozen inherited ordering, retain fetched canonical messages in-session. **Tests:** prepend, nested-branch history, empty/large archive, stale/out-of-order network responses, no duplicated messages.

### CLI-12 — Windowed Markdown renderer and stable scroll anchors

**Depends:** CLI-09/11. **Scope:** Glamour renderer adapter, approximately 200-line adaptive visible window, long-message slicing, logical revision+line anchors, bounded rendered cache keyed by revision/width/theme/renderer version; scroll/resize/stream stability. **Tests:** code blocks/tables, 1 MiB message, 10k-message fixture, rendering-scale benchmarks, mouse selection smoke testing, anchor roundtrips. **Early spike:** validate Bubbles viewport window replacement and terminal selection semantics before committing algorithm.

### CLI-13 — Semantic themes and live preview

**Depends:** CLI-02/09/12 interface (can begin before complete renderer). **Scope:** terminal default/Tokyo Night/Dracula/Nord/Solarized Light registries, `mimir --theme list`, one-invocation `--theme NAME`, `/theme` preview/apply/revert, fallback for low-color and monochrome terminals. **Tests:** list works offline/unauthenticated, theme persistence, invalid theme handling, no history refetch/re-anchor loss, active stream unaffected.

### CLI-14 — Streaming state machine and TUI progressive rendering

**Depends:** CLI-03/09/10/11/12. **Scope:** typed app events for durable run attach, authoritative snapshot replacement, transient text, usage and single terminal result, activity state, display following and scrolled-up stability. **Tests:** deterministic chunk stream, duplicate connection event, interrupted/restarted server, snapshot behind already-viewed text, overflow/reconnect, conversation switch mid-stream.

### CLI-15 — Cancellation, failure and TUI recovery UX

**Depends:** CLI-04/07/10/14. **Scope:** Esc cancellation request with confirmation state; completion/cancel races; reattach after auth and network failure; preserve drafts and selected context; surface conflicts and terminal failure accurately. **Tests:** disconnect does not cancel, repeated cancellation safe, pending run on startup, auth renewal, stale conversation version, backend downtime, no accidental regeneration.

### CLI-16 — Integration, benchmarking, packaging and release evidence

**Depends:** CLI-01 through CLI-15 plus contract-conformant backend. **Scope:** Linux/macOS build/install/deployment endpoint provisioning, PTY compatibility, full CLI-to-backend tests with deterministic provider and fake `MemoryService`, selected real-provider smoke tests, benchmark baselines, operator/user docs. **Gate:** all architecture release-blocking scenarios pass or deviations explicitly approved; latency/memory targets measured on pinned reference hardware; no secrets or prompt text in standard logs.

## 4. Shared API prerequisites and cross-project handoff

The CLI plan cannot own server changes. Maintain a separate dependency register against the Mimir Server plan:

| Required backend capability | Client tasks depending on it | Backend action |
|---|---|---|
| Canonical `/api/v1` OpenAPI and normative SSE types | CLI-03 onward | Version contract and fixtures |
| Revocable opaque sessions + silent renewal | CLI-04, -15 | Define renewal limits and concurrent-process semantics |
| Latest accessible conversation ordering | CLI-10 | Define canonical query/ordering |
| Project association mutation with CAS | CLI-05, -06 | Domain + HTTP endpoint, authorization tests |
| Durable standalone run, no archive/ingestion | CLI-08 | Schema/domain/API, retention + memory eligibility |
| Idempotency-key accepted-operation lookup | CLI-07, -08 | Scope, authentication and response contract |
| Durable run status/cancel/SSE atomic snapshot | CLI-03, -07, -14, -15 | Publish definitive event/control semantics |
| Authorized cursor page history with frozen revisions | CLI-11, -12, -08 | Stable ordering, pagination tests |

Backend T20–T28 are now backend-owned contract and runtime integration work, as specified in `../backend/TASK_INDEX.md`. Keep CLI task IDs independent and validate shared contract compatibility without importing server internals.

## 5. Testing gates

- **Correctness:** contract, concurrency, memory eligibility, authorization and output fidelity validated.
- **Resilience:** ambiguous accepted submission, idempotency resolution, snapshot/live race, backend restart, canceled/completed race, and crashed CLI operation lookup pass.
- **Responsiveness:** target p95 latencies and cache/RSS limits from `ARCHITECTURE.md` measured on reference machines and realistic fixtures; before stabilizing baseline, treat numbers as provisional design goals.
- **Portability:** Linux/macOS with keyboard, resizing, selected terminal mouse operations, terminal-default and ANSI low-color modes; piped stdout is clean.
- **Scope:** no client-managed memory database, no Windows, no public SaaS features, no new account integration requirements.

## 6. Documents to complete before coding gates

1. Publish/freeze shared REST/SSE contract for the minimal vertical slice, including backend additions listed above.
2. Reconcile Mimir Server `ARCHITECTURE.md`, `IMPLEMENTATION_PLAN.md`, `TASK_INDEX.md`, T20–T28 and relevant backend domain task briefs against this independent build.
3. Create individual `tasks/CLI-01...CLI-16` briefs with exact allowed files/packages, PR dependencies, scenarios and commands when the repositories are initialized.
4. Run early terminal-history prototype and backend-independent transport test to validate riskier library assumptions.

This plan is intentionally independent from the server's completion timeline. The server and CLI can progress in parallel against the same published API contract, converging for full end-to-end validation.

## Specification-closure prerequisite
Apply top-level `AGENTS.md`, `SPECIFICATION_GAPS.md`, `QUALITY_GATES.md`, `TRACEABILITY.md`, `THREAT_MODEL.md`, and `EVALS.md` before commencing any externally observable API, state-machine or persistence behavior. Bootstrap tasks may establish tools and harnesses; do not treat unapproved product defaults or missing contract fixture files as complete. Explicitly freeze shared monorepo schemas and reconcile task ownership before affected work begins.

## Mandatory feature-branch STOP / human-and-agent PR review checkpoint

1. **Start** only the assigned task from the latest merged `main` with its declared predecessors merged. Create one `feature/<task-id>-<slug>` branch; record base SHA, task ID and approved contract revision. Do not stack dependent work on an unmerged branch.
2. **Implement/test** only the assigned task; include relevant unit, integration, contract, negative/security and race/restart cases. Capture actual commands, versions, results and remaining risks; a failed/unrun gate must be disclosed, never represented as passed.
3. **STOP implementation** at the feature boundary. Open a PR with `PR_REVIEW_TEMPLATE.md` completed, scope/diff summary, contract/schema/dependency changes, test evidence, migration reversal/recovery guidance and review checklist. The implementation agent must not approve its own PR.
4. **Independent agent reviewer** reviews the full diff against the task, invariants, repository guardrails and contract; records findings and severity and verifies checks. The author addresses findings and obtains a new review on material changes.
5. **Human reviewer/owner** inspects the PR and evidence; explicit approval to merge is required **after** independent review. Silence, green CI, or agent approval alone is never consent. No bot auto-merge, direct main push, or self-merging on behalf of an unavailable human.
6. **Merge checkpoint** only an authorized human (or specifically delegated operator after affirmative human approval) merges. Record merge SHA and update task state/traceability. **Halt:** do not start or branch for the next task until merge is confirmed and the human assigns/authorizes subsequent work. If rejected, revise within the same branch and repeat review.
7. Shared contract changes require an explicitly authorized PR against canonical /contracts, independent agent review, and human approval before any dependent backend or CLI work. Monorepo tasks remain bounded to a single assigned feature branch; never presume atomic multi-task delivery.
