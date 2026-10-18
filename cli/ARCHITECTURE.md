> **Normative v1 reconciliation:** See `../docs/decisions/FINAL_V1.md` for the approved deployment, lifecycle, contract and governance rules. Historical conflicting implementation suggestions do not authorize changes.

# Mimir CLI/TUI — Architecture and Detailed Design

**Status:** Design baseline for implementation planning  
**Date:** 2026-10-17  
**Scope:** Go command-line application and full-screen terminal UI (Linux and macOS)  
**Companion system:** Mimir Server, authoritative Python/FastAPI backend

> This document captures decisions approved during the CLI/TUI design discussions. It specifies the client architecture, behavior, and backend dependencies. It does **not** redefine the backend's canonical archive, memory-provider internals, or shared API contract. Explicitly labeled proposals and open specification details require resolution in the implementation plan or shared contract; they are not silently approved features.

## 1. Product intent and architectural invariants

Mimir is a private, local-first, conversational assistant intended initially for personal/family use. A private remotely hosted backend remains possible; public SaaS, multi-organization tenancy, marketplace distribution, Windows support, and public sign-up are not v1 goals. The name Mimir identifies the overall product; `mimir` is the terminal executable. Naming overlap with unrelated projects is not presently a release blocker, though public distribution would warrant an appropriate naming review.

The Go CLI/TUI and Python backend are **loosely coupled, separately planned engineering builds**. The CLI interacts only through the authenticated, versioned canonical `/api/v1` REST and SSE contract. It never reads PostgreSQL or Qdrant, calls Mem0/Ollama directly, or duplicates authoritative archive, authorization, memory, or run-management logic. The future TypeScript/React web client consumes the same contract, not the Go client.

**Nonnegotiable invariants:**

1. One canonical conversation/archive authority: the backend. CLI state is selection and recovery metadata, not a parallel transcript.
2. Managed memory lifecycle belongs to `MemoryService`; clients do not select individual facts to ingest, update, or forget.
3. Authentication and resource ownership are enforced on the backend on every resource/stream request.
4. No implicit generation cancellation on UI exit, network disconnection, or conversation switch.
5. No duplicate persisted submission following ambiguous network failures; recover with the same operation identity.
6. The user's current editor draft, reading position, and confirmed context selections survive recoverable interface failures.
7. User-visible stdout for one-shot inference is clean generated content, independent of status and diagnostics.
8. Prefer measured simplicity. No new frontend state framework, database, general message bus, or token replay layer in v1.

## 2. Technologies and platforms

| Area | Selected stack / policy |
|---|---|
| Language | Go, pinned toolchain and dependency versions |
| Commands | Cobra |
| TUI core | Bubble Tea v2 |
| Terminal widgets | Bubbles v2, adapted where needed |
| Styling | Lip Gloss v2, semantic palette |
| Markdown | Glamour v2; long-message/window adapter around renderer |
| HTTP | Typed Go client generated/validated from normative OpenAPI 3.1 |
| Streaming | SSE parser and typed event model conforming to the normative SSE schema |
| Platforms | Linux, macOS; Windows deferred |
| Tests | Go unit/benchmark tests, mock HTTP/SSE server, PTY interaction tests, cross-system E2E |

Use stable, pinned releases compatible with one another; verify actual library APIs in the implementation spike. Do not invent compatibility shims absent a demonstrated problem.

## 3. High-level component boundaries

```text
mimir [Cobra command tree]
   |-- configuration / platform paths / profiles
   |-- secure credential adapter
   |-- local context state + minimal operation journal
   |-- typed Mimir API client
   |     |-- REST resources / auth / idempotency / errors
   |     `-- SSE snapshot + event transport
   |-- one-shot command controller
   `-- Bubble Tea TUI application
         |-- composer and keymap
         |-- conversation/project selectors
         |-- message page store + cursor prefetch
         |-- viewport/window manager + scroll anchors
         |-- Markdown renderer + bounded formatted cache
         |-- run/stream state machine
         `-- theme manager / status and notifications
                  |
                  v
         Authenticated Mimir Server /api/v1
                  |
        Canonical archive, projects, memory, inference and runs
```

Transport, domain client, command handlers, local persistence, view model, renderer, and terminal components should be independently testable. Bubble Tea's update loop owns visual state; asynchronous work returns typed messages/events rather than mutating components from goroutines. A shared client core supports both one-shot commands and the TUI.

## 4. Canonical resources and backend contract

The backend owns users, projects, conversations, immutable message revisions, branches, durable inference runs, managed memory, and authorization. Every conversation has its own ID and belongs to at most one project. Branching/editing/regeneration creates a **new conversation** with frozen references to exact inherited message revisions. The backend materializes the branch's visible ordered history; the client does not reconstruct ancestry from arbitrary mutable parents.

A backend-owned monotonic `conversation_version` supports optimistic concurrency for canonical writes; it is distinct from message revision IDs, message ordinals, inference-run IDs, and local sticky-selection generations. Exactly one nonterminal inference run may occupy a conversation; distinct conversations may execute concurrently as resources allow.

API contract obligations include:

- Authenticated project/conversation list, detail, create, branch, association and history resources with user isolation.
- Opaque cursor history pagination; authoritative order including frozen inherited revisions.
- Atomic idempotent persistent submission that commits a user message and a pending run, returning the `run_id`.
- Durable run status, cancellation and `GET /runs/{id}/events` SSE.
- SSE connection begins with an authoritative checkpoint snapshot followed by a race-free live handoff. No persistent token-delta replay or guaranteed `Last-Event-ID` replay.
- Structured, stable authorization, version-conflict, invalid-request, and unavailable-service errors.
- A conversation-independent **standalone run**, reusing inference/context machinery, with managed-memory reading but no canonical conversation or memory ingestion.
- Server-side lookup of an accepted operation using its idempotency identity and authenticated scope, for minimal crash recovery.
- Server-managed persistent opaque CLI sessions with silent renewal; project reassignment/unassignment with concurrency checks.

The last three bullets extend the earlier backend baseline and must be added explicitly to the backend architecture, schemas, contract, tests, and implementation plan. Exact endpoint names, status fields, standalone retention, and session expiry/renewal details belong to the shared API/backend specifications, not ad hoc client invention.

## 5. Command-line interface

The executable is `mimir`. Cobra owns help, completion and shell-compatible exit codes. An invocation with no subcommand opens the TUI, resuming the most recently active accessible conversation (or a sensible empty/new-state screen if none exists). The TUI must not create an empty canonical conversation simply by launching.

**Approved functional commands** (exact spelling and flags to freeze in command-contract tests):

```text
mimir                                 # Open TUI; resume latest active conversation
mimir resume [conversation-id]        # Resume named or latest conversation in TUI
mimir ask "prompt"                    # Resolve sticky context; otherwise standalone
mimir ask --conversation ID "prompt"  # Append and select that conversation
mimir ask --project ID "prompt"       # Create conversation in project, make it sticky
mimir conversation create [--project ID]
mimir conversations list
mimir projects list
mimir history ID [--format markdown|json]  # Full visible history to stdout
mimir assign conversation CID project PID
mimir unassign conversation CID
mimir clear conversation
mimir clear project
mimir clear all
mimir context                          # Show sticky selection and resolved association
mimir run status ID
mimir run attach ID
mimir login
mimir logout
mimir status
mimir config show|path|set|unset ...
mimir --theme list
mimir --theme nord                     # Invocation-specific appearance override
mimir completion ...
```

Use Cobra help/usage conventions for singular/plural command naming; avoid maintaining competing aliases unless they demonstrably improve usability. `mimir history` exports the **complete visible canonical branch path**, not only the loaded TUI pages. Markdown default, JSON option. Pipe to a file using shell redirection. No direct file-writing backend API required.

**One-shot input/output:** accept quoted prompt arguments, and optional stdin context. When stdin is piped, preserve a clear boundary between the user prompt and untrusted piped content; do not promote the piped text to privileged instructions. Print generated answer only to stdout; status, IDs, progress and errors go to stderr. Disable spinners/ANSI formatting for non-TTY output. Return nonzero on failure; distinguish an accepted run that the CLI could not finish observing from a confirmed failed run. The CLI must not report an unverified operation as successful.

## 6. Sticky command context and conversation creation

Two client-owned, account/backend-scoped selections persist across *separate CLI processes*: `active_project_id` and `active_conversation_id`. They are resource pointers, not a canonical source of truth. Every use validates authorization and current server associations.

**Resolution precedence for `mimir ask`:**

1. Explicit `--conversation ID`: append there; on successful validation/adoption, set sticky conversation. If project flag is also present, require a matching association or reject; never silently move conversations.
2. Explicit `--project ID`: create a **new persisted conversation** in the project for this prompt; make its ID and project sticky after confirmed creation/acceptance.
3. Sticky conversation: append to it.
4. Sticky project without sticky conversation: create a new conversation there and adopt it.
5. Neither: submit a **standalone run** without canonical conversation creation.

`mimir conversation create` deliberately creates a persisted conversation and adopts it. With no explicit project, it uses the sticky project if present; otherwise the conversation is unassigned. Selecting a project using an operation that merely changes selection need not create an empty conversation until a prompt requires one.

`mimir clear conversation` retains project selection; next ask creates a new conversation if project remains selected. `mimir clear project` clears both project and conversation; `mimir clear all` has the same result. Clearing selections does **not** delete or unassign canonical objects.

`mimir assign conversation CID project PID` and `mimir unassign conversation CID` are **backend mutations**, not selection changes. They preserve message revision identity, inherited branch references, and history; increment the backend's canonical concurrency version as specified by the backend contract. Branch descendants do not automatically follow reassignment. Future requests use new project context, but previously ingested managed memories are not retroactively rewritten.

A selected conversation's canonical project association takes precedence over any stale locally cached project description. Failed creation must not install an unknown conversation ID. Use atomic local state updates with a short lock and selection generation to prevent older concurrent command completions from overwriting newer selections. TUI browsing/selection state is distinct from one-shot sticky selections; browsing the TUI must not silently redirect shell one-shot commands.

## 7. Standalone inference and memory semantics

Standalone one-shot runs exist to answer quick queries without cluttering the canonical conversation archive. This is the default only when neither explicit nor sticky conversation/project context resolves. Standalone runs have authenticated user scope, the same model/provider and working-context pipeline as normal inference, and existing managed-memory retrieval. They do **not** produce canonical conversation messages or queue managed-memory ingestion.

Persistent conversation exchanges are eligible for asynchronous `MemoryService.ingest()` from committed exact-revision source turns. Mem0 determines whether any information is actually important enough to retain. The CLI exposes **no** `--ephemeral`, `--no-memory`, per-memory editing, or ingestion switches in v1. Absence of a conversation does not imply a zero-data-retention guarantee: operational metadata and bounded run checkpoints follow backend retention rules. The orchestrator must not synthesize a separate Mem0 ingestion path from uncommitted standalone text.

## 8. Full-screen TUI and interaction

Use Bubble Tea v2 in alternate-screen mode with a minimal, conversational layout: history viewport, lightweight header/status, message composer and concise footer/status notices. Avoid permanent sidebars, dashboard panels, popup windows or unnecessary modal UI. Keyboard-first operation; mouse scrolling supported where terminal capabilities permit. Small animated activity spinner for active operations. Keep the terminal's native text-selection affordances where feasible and test alternate-screen mouse tradeoffs explicitly.

Approved interaction intentions:

- `/conversation` opens an in-viewport, keyboard-navigable selector; `/conversation ID` switches directly.
- `/new` deliberately creates and enters a canonical conversation; `/project`, `/model`, `/theme`, `/status`, `/help`, `/exit` provide context and controls.
- Conversation switching and UI exit do **not** cancel active inference. `Esc` when a run is active requests explicit cancellation, with a clear pending state; other key behavior depends on UI focus.
- Preserve unsent drafts across recoverable failures. Never silently send a draft to a different conversation after a stale-version conflict.
- `/theme` previews highlighted palettes live, Enter persists, Esc restores the original palette. Full-screen selectors temporarily occupy the principal viewport rather than creating OS-style modal windows.
- Launch defaults to the backend's latest active accessible conversation, defined consistently by backend ordering. If a run is in progress, discover and reattach to its durable state.

The backend must define the authoritative 'latest active' sort semantics; the client must not approximate this with local file timestamps.

## 9. History pagination, rendering, and scroll anchoring

On open, load approximately 30–40 most recent **canonical messages**. Older pages are fetched on upward scroll using opaque backend cursors; prefetch approximately one viewport above the loaded boundary. Suppress duplicate in-flight requests. Retain fetched canonical message pages during the session (v1 no message-page eviction). A branch's inherited messages are presented as the backend-materialized ordered visible path.

**Windowed rendering:** format only an adaptive sliding window targeting roughly 200 terminal lines, rather than rendering the full fetched conversation on every frame. Implement a history store, a window manager/viewport adapter, and a Markdown renderer as separable components. One exceptionally long message may be rendered once and sliced into the visible line range, with specialized measurement/profiling if this proves expensive. A 200-line window is a **performance target, not a fixed correctness cap**.

**Anchoring:** maintain a message revision identifier plus relative line offset (and relevant wrap/width context) as the logical reading anchor. Preserve its visible position when prepending older pages, shifting the render window, receiving streamed text, changing themes, and resizing the terminal. If the user is at the bottom, follow new content only according to explicit bottom-follow behavior; don't yank an upward-scrolled reader back down.

Cache rendered per-message output keyed by immutable revision ID, width, theme/palette identity and renderer version. **Bound and evict the formatted-output cache**; retaining fetched canonical messages does not require retaining formatted copies indefinitely. Streaming output is transient, separately rendered and reconciled with canonical revisions upon completion. Do not disk-cache conversation bodies or rendered Markdown in v1.

Prototype early using 100, 1,000 and 10,000-message fixtures, a 1 MiB Markdown message, long code blocks, complex tables, resize, mouse selection, theme switching, and active streaming; validate actual viewport behavior before locking down window algorithms.

## 10. Streaming, run state and failure recovery

**Persistent submit:** generate a stable idempotency key, read current `conversation_version`, and submit atomically via REST. Once accepted, record `run_id` and subscribe to SSE. Idempotency resolution of an already-accepted identical operation must precede rejecting its now-stale expected version. Never retry an ambiguous submission with a *new* idempotency key. Reject key reuse with a different payload.

**Stream attach:** `GET /runs/{id}/events` provides a coherent authoritative snapshot followed atomically by live events. Snapshot content **replaces** local provisional text; never append the snapshot onto previously received deltas. No durable per-token replay assumption and no `Last-Event-ID` dependency. When the stream dies, leave current display readable, show unobtrusive reconnect state, and reattach to the same run with bounded backoff and jitter.

**Terminal states:** model started, text delta, usage, and exactly one terminal result from `completed`, `failed`, `cancelled`, `interrupted` as the normative SSE schema prescribes. A missing stream is not proof of failure. Completed runs load the canonical assistant revision; failed/interrupted runs display their authoritative status and checkpoint where available; **do not auto-regenerate** after a terminal failure. Regeneration/editing follows canonical new-conversation branching semantics.

**Cancellation:** explicit idempotent REST request, backend-confirmed result. Completion may race cancellation and win; never locally assert `cancelled` before confirmation. Client exit, switching or disconnect never automatically cancels backend work.

**Version conflicts:** preserve unsent content, display the stale-context condition, fetch authoritative state, and require deliberate resubmission/branching rather than silent retry against a newer conversation version.

**One-shot resilience:** bounded network retries while the process lives; reattach to accepted run. If recovery deadline expires, report useful run/operation information on stderr and use nonzero exit status. `mimir run status` and `mimir run attach` support later inspection/reattachment. Avoid ambiguity over whether any output was partial; stdout content that was already emitted cannot be rolled back after a snapshot rewind—one-shot recovery must define a safe streaming policy (e.g., buffered checkpoint commit or documented partial-output semantics) in its detailed transport specification before promising exact complete output on reconnect.

## 11. Minimal crash-recovery journal

A small persistent **operation-metadata journal**, distinct from credentials and cached transcripts, is an approved v1 feature. Journal an operation ID/idempotency key **before submission**, and record backend profile/account identity, target IDs, run ID once known, timestamps and recovery state. **Do not store prompt bodies or full responses.** Protect with private permissions, atomic writes, and short synchronization locks. Prune resolved entries according to explicit retention policy.

After restart, resolve accepted operations by authenticated server-side idempotency lookup and/or known `run_id`. If the backend never accepted an operation and the prompt body is no longer available, report the unresolved request rather than fabricate a retry. A crash before the operation identity reaches durable local storage cannot be recovered by that mechanism; writing before dispatch is therefore required. Concurrent CLI processes must not corrupt journal data.

## 12. Authentication and secure credentials

Administrator-provisioned local accounts; password entered interactively through masked prompts, never as an argument. Backend issues a revocable opaque bearer credential; token hashes are held server-side. Store the client secret through macOS Keychain or a suitable Linux Secret Service implementation when available; an explicitly selected, access-restricted file is an opt-in headless Linux fallback. Never silently fall back to an insecure store. No user token copying, environment credential variables, or OAuth infrastructure for v1.

Backend-managed session renewal is silent during normal use and after standby/restart. Favor renewing expiry of a server-side opaque session within an established maximum lifetime to reduce multi-process credential-rotation races; exact expiry and rotation decisions are backend-owned. When renewal is impossible, TUI prompts to sign in again without discarding draft/history state; noninteractive commands fail clearly unless an appropriate interactive login is explicitly invoked. `mimir logout` removes local credentials and requests backend revocation; if the backend is offline, accurately report that server revocation was unconfirmed.

HTTPS is required for remote services; plain HTTP is permitted only for explicitly supported loopback development configurations. Scope credentials and local context to verified server/account identity. The CLI never assumes that authentication to one backend grants rights on another.

## 13. Configuration, deployment handoff, and local paths

Use user-editable TOML **preferences**, separate deployment-provisioned server connection settings, separate durable client state, and secure credential storage. No general CLI environment-variable override mechanism in v1. End users should not need to configure server URLs, authentication tokens, or service addresses. Docker Compose/Makefile/installer and future infrastructure-as-code are responsible for provisioning a discoverable backend connection; CI alone is not the runtime mechanism. Provide a known local loopback default as applicable. No general `--server` user override initially.

| Data | Linux default | macOS default |
|---|---|---|
| Preferences | `$XDG_CONFIG_HOME/mimir/config.toml` or `~/.config/mimir/config.toml` | `~/Library/Application Support/mimir/config.toml` |
| Durable state | `$XDG_STATE_HOME/mimir/` or `~/.local/state/mimir/` | `~/Library/Application Support/mimir/state/` |
| Disposable cache | `$XDG_CACHE_HOME/mimir/` or `~/.cache/mimir/` | `~/Library/Caches/mimir/` |
| Credentials | Secret Service or explicitly approved protected file | Keychain or explicitly approved protected file |

XDG directory variables here are **operating-system directory conventions**, not exposed application-configuration overrides. Local files may include `context.json` and recovery metadata, with schema version, account/backend scope, atomic writes and short locks. Prompt bodies and canonical history must not be added to disk caches.

Configuration precedence for supported appearance/behavior preferences: **explicit invocation flag → persisted user preference → built-in default**. Backend connection settings are separately provisioned and not part of arbitrary user preference precedence. Supported commands: `mimir config show`, `path`, `set`, `unset`; exclude credential values. Reserve internal tuning values (page size, rendered-line target) from the public config schema pending profiling. Version and validate schemas with clear errors.

## 14. Themes and terminal compatibility

Theme design uses semantic roles (background, surface, primary/muted text, accent, border, selection, success, warning, error, user, assistant, code/syntax) rather than hard-coded colors in components. Built-in v1 registry:

- `terminal-default` (initial default; respects terminal appearance)
- `tokyo-night`
- `dracula`
- `nord`
- `solarized-light`

`mimir --theme list` prints available themes and requires neither auth nor backend connectivity. `mimir --theme NAME` affects just that invocation; `mimir config set appearance.theme NAME` persists a choice. `/theme` in the TUI gives live preview with Enter to commit and Esc to revert. A theme change invalidates affected rendering-cache entries, without refetching canonical messages or interrupting generation, and preserves scroll anchor.

Support true color when available with graceful ANSI 256/16 and readable monochrome fallbacks; information must not be conveyed through color alone. Custom theme files defer beyond v1. Verify behavior with SSH-style terminals, alternate screen, narrow/wide dimensions, light/dark terminals, ANSI fallback and keyboard-only controls.

## 15. Observability, error reporting and security expectations

Map typed server errors to stable actionable messages. Never print raw session tokens, passwords, sensitive request headers, or prompt bodies in normal logs. Progress/log information goes to stderr for one-shot commands. Sanitize terminal control sequences from untrusted server/user text before display. Enforce bounded network and event buffers to avoid unbounded memory growth; support server overload/backpressure signals rather than masking them as successful output. Measure frame/update timings and cache sizes in development builds without sending private conversation text to telemetry.

The client is a security boundary for secure local credential handling but **not** a substitute for backend authorization. Server validates every user/project/conversation/run operation, including SSE attach and operation lookup.

## 16. Testing strategy and provisional performance gates

Use deterministic fake model streams and fake `MemoryService` for most end-to-end tests. Real Mem0/Qdrant and Ollama are covered by a smaller set of integration/compatibility suites. PTY tests exercise the actual binary, Bubble Tea event handling and terminal capabilities.

| Measure | Initial objective | Exclusions/notes |
|---|---:|---|
| CLI cold command readiness | p95 < 150 ms | No network/auth latency |
| Initial TUI terminal frame | p95 < 300 ms | Before backend history fetch |
| Local input-to-frame latency | p95 < 50 ms | Loaded conversation fixture |
| Cached history scroll | p95 < 50 ms | No network page fetch |
| Theme preview | p95 < 100 ms | Visible window rerender |
| Resize to stable frame | p95 < 150 ms | Realistic history fixture |
| History processing after fetch | p95 < 100 ms | Network/backend time excluded |
| Stream event to visible update | p95 < 50 ms | Under bounded event load |
| Incremental history/render cache RSS | < 250 MiB at 1,000 fetched messages | Provisional generous goal; benchmark definition required |

These values are **planning targets**, not measurements or mandatory guarantees absent a pinned CI reference platform. Set measured baselines before enforcing noisy p95 regressions as hard CI gates. The central requirement is that rendering cost scales with the visible window rather than the entire archive.

**Release-blocking functional scenarios:** owner isolation; auth renewal/revocation; shell stdout cleanliness; sticky precedence and clearing; deliberate conversation creation; standalone read-memory/no-ingestion; explicit project reassignment; branch frozen history; cursor prepend anchor; 1 MiB message and 10k-message stress; concurrent client version conflict; idempotent ambiguous submit; snapshot/live race; SSE disconnect/reconnect; backend crash/restart; cancellation/completion race; terminal error states; operation-journal crash recovery with no prompt body; multiple local processes; themes, resize and keyboard/mouse compatibility. Prefer risk-based coverage to an arbitrary global coverage percentage.

## 17. Ownership, implementation dependencies, and document hierarchy

This document is the **CLI-specific architectural authority**, under the system-level architecture and normative shared API contract. A separate `mimir-cli/IMPLEMENTATION_PLAN.md` will split client implementation into narrow, composable tasks, with unique CLI-specific IDs, one bounded implementation/review unit per task and explicit dependencies.

The server retains its own `ARCHITECTURE.md`, `IMPLEMENTATION_PLAN.md`, `TASK_INDEX.md`, and task briefs. Backend T20–T28 now cover server-owned client integration obligations and link to this independent CLI plan. Changes to server schema/routes/auth/run behavior are owned by backend tasks, not quietly implemented in Go. Shared API contract updates precede dependent client implementation.

Backend work added or strengthened by CLI design includes: conversation-independent durable standalone runs, standalone retention and memory eligibility, idempotency operation lookup, project reassignment/unassignment, server-managed session renewal, canonical recent-conversation selection semantics, and clarification of SSE/cancellation/run-status guarantees. Update existing backend tasks where overlap already exists, and add new backend task briefs only for truly uncovered work, preserving identifiers and PR discipline.

**Independent build strategy:** client unit tests and typed transport contract tests run against a mock backend before all server features are complete. Server tests run without a complete Go client. Joint end-to-end tests validate compatibility once both sides meet the same contract. Do not impose a linear dependency in which finishing the entire TUI precedes the backend's memory framework work.

## 18. Explicit deferrals and specification follow-ups

**Deferred from v1:** Windows packaging and credential support; public registration/hosted SaaS; OAuth/social auth; dedicated unattended automation tokens; manual env-based app configuration; multiple-server-profile UI; external custom theme files; local on-disk message cache; per-memory edit/browse/delete controls; implicit memory toggles; token-delta replay; elaborate multicolumn TUI layout; parallel-generation runs within one conversation.

**Must be specified in implementation/contract work, without reopening approved product behavior:**

1. Exact HTTP route/payload/error schema for standalone runs, operation lookup, project assignment, auth/session renewal and latest-conversation ordering.
2. Standalone-run and operation-journal retention; full lifecycle and cancellation of standalone work.
3. User-visible semantics for one-shot stdout already emitted before reconnect and authoritative snapshot rollback; distinguish streamed partial output from complete result.
4. Atomic sticky selection behavior in project/conversation creation under concurrent invocations and ambiguous acknowledgments.
5. Terminal framework compatibility and practical scroll/selection/performance prototype results.
6. Install/deployment handoff supplying the backend connection to native Go CLI without manual user configuration.

These follow-ups are bounded engineering specifications; none justifies silently creating a second backend/archive or compromising the established provider-neutral memory boundary.

## V1 resolution and review authority
The selected v1 behavior baseline is `CONTRACT_DECISIONS_V1.md`, with lifecycle rules in `STATE_MACHINES.md`, context format in `PROMPT_AND_MEMORY_CONTRACT.md`, and wire schemas in `contracts/`. Older wording describing G01–G15 as unanswered or suggesting T20 may choose public payload shapes is superseded. Every feature PR must pass the **mandatory stop / independent-agent + human review** in `PR_REVIEW_TEMPLATE.md`; no subsequent branch work before authorized merge and reassignment.
