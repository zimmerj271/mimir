> **Normative v1 reconciliation:** See `../docs/decisions/FINAL_V1.md` for the approved deployment, lifecycle, contract and governance rules. Historical conflicting implementation suggestions do not authorize changes.

# Mimir Server — Approved backend v1 architecture

**Authoritative v1 design · revised October 17, 2026 from uploaded v1.2 baseline.** This document supersedes earlier discussion drafts. V1 requirements appear in the main body. **All future ideas are confined to the appendix at the end.**

## 1. Mission and invariants
- Local-first, private, self-hosted multi-user assistant with durable conversation archive and replaceable memory/inference providers. User explicitly selects any hosted inference; never automatically send prompts externally.
- Two loosely coupled builds: Python/FastAPI/uv server and independently planned Go/Bubble Tea CLI developed first; TypeScript/React/assistant-ui web client later. Clients communicate only with the same canonical REST/SSE backend; no client-to-client API.
- Three concepts remain distinct: **conversation archive** (authoritative history), **managed memory** (provider-owned information), **working context** (per-request transient model input). Never use bronze/silver/gold terminology.
- Application services own conversations, authorization, working context and execution. `MemoryService` owns **complete managed-memory lifecycle** behind a replaceable adapter. The orchestrator neither extracts nor consolidates memories and never touches Mem0/Qdrant internals.
- Favor narrow typed interfaces, real integration tests, measured optimization, and minimal infrastructure.

## 2. Domain ownership
| Boundary | Owner | V1 implementation |
|---|---|---|
| Users/projects/conversations/branches/messages/revisions/attachments | Conversation archive | PostgreSQL 18 (18 preferred), SQLAlchemy 2.x persistence |
| Authentication/sessions | Backend auth | Server-managed opaque sessions |
| Managed memory lifecycle, storage, extraction, consolidation, vector indexing, search | `MemoryService` | OSS Mem0 via in-process adapter preferred, backed by Qdrant, local embeddings |
| Context gathering | Orchestrator | Authorized PostgreSQL reads + user-scoped `MemoryService.search()` |
| Context selection/budget | `WorkingContextAssembler` | Pure, deterministic Python component |
| Inference, streaming, cancellation | `InferenceService` + executor | Replaceable runtime adapters, Ollama initially |
| Job delivery and execution | Runtime coordinator | PostgreSQL `SKIP LOCKED` jobs + leases |
| Client events | Event distribution + SSE adapter | In-process bounded pub/sub initially |

## 3. Conversations and branching
- Every resource has one private owner. One project owns zero or more conversations; every conversation belongs to at most one project. No cross-user sharing.
- Every assistant answer is a canonical assistant message with separately stored model execution metadata. User message is committed before inference. Streamed token deltas are NOT individual message revisions; partial output uses execution checkpoints, with at most one final assistant revision per run.
- Immutable message revisions. Editing upstream messages, regeneration, or branching creates a **new conversation** with a new ID. Its inherited visible prefix is an immutable ordered snapshot of **references to exact parent message revisions** (flatten nested branches). Never copy inherited text. Parent edits cannot change descendants. New branch inherits the parent's project at creation and is independent thereafter.
- Branch creation does not re-ingest inherited messages into memory. Deletion of a conversation also purges dependent descendant-branch subtree and associated archive artifacts; memory remains untouched.
- Optimistic `conversation_version` guards writes; one nonterminal generation run per conversation; parallel work across separate conversations is allowed subject to resource limits.

## 4. MemoryService v1
- **Public application capability:** `ingest()` committed exact-revision turn asynchronously and `search()` by authenticated user and optional soft project hint. **Restricted internal lifecycle:** full-user `delete_user_data()` for account purge and complete current-memory export/list for migration/backup when provider can support it. Provider-specific diagnostics allowed only behind adapter.
- **No public memory browsing, individual `get/update/delete` endpoints, or natural-language selective-forget contract in v1.** Mem0 may perform its own normal internal update/deletion/consolidation. The user cannot precisely erase one memory in v1; prominently disclose this limitation.
- Self-hosted Mem0 OSS owns memory record identity, persistence, index and consolidation; the application never creates canonical `memory.*` tables or rewrites Mem0's Qdrant payload. Pin tested Mem0 OSS release and use a local Ollama configuration; Qwen3-Embedding-4B, 2,560-dim FP32 cosine vectors are the target **subject to compatibility spike**. Dense vectors initially; no custom hybrid, quantization, or revision-point enforcement in v1.
- User isolation is release blocking for search and full purge. Adapter must be able to demonstrate scope enforcement; direct access to Mem0/Qdrant forbidden to clients. Project affinity is a soft retrieval hint; do not exclude global memories by default. No guarantee of exact provenance across Mem0 consolidations.
- Durable, asynchronously scheduled ingestion of committed turns; per-user ingestion serialized, different users may process concurrently. Exact source revision IDs in job payload/ledger. Provider decides what to retain. At-least-once delivery; ambiguous external outcomes are reconciled or held for operator review, never blindly replayed as exactly-once effects.
- Conversation deletion leaves Mem0-managed memory intact. Full account/data purge deletes *all* user's managed memories. No automatic per-conversation memory rebuild.

## 5. Working-context assembly
- Orchestrator independently gathers authorized project instructions, visible branch-aware recent history, current message, and memory-search results. Ordinary conversational requests search memory, except explicit nonconversational bypasses. Memory outages degrade with diagnostics; archive availability is required.
- Pure deterministic `WorkingContextAssembler` receives already-gathered inputs, model profile, policy and token counter; it does no I/O and changes no memory.
- Required content: application system instructions, applicable project instructions, current message, format/protocol overhead and reserved model output. If these do not fit, return typed `ContextBudgetExceeded` (do not truncate current user request).
- Optional allocation: preserve configured soft minimum of newest coherent turns that fit; select provider-ranked memory up to caps; fill remaining capacity with older recent history. Final provider-compatible count/estimation plus margin; iteratively drop optional units if over budget. No fixed percentages, no partial torn-apart tool/turn units, no summarization in v1.
- Memory text and conversation history are **untrusted reference information**, never privileged instructions. Current user message exactly once; preserve exact revision ordering in branches. Exact ID/content duplicates suppressed; do not build a semantic reranker or consolidation engine in assembler.
- Store selected revision/memory IDs, budget estimates/mode, truncation reasons, memory degradation and model policy versions with model-run diagnostics. Full assembled prompt retention is optional and **off by default**.

## 6. Inference architecture and execution
- `InferenceService` owns runtime adapter registry and normalizes generation requests/events, errors, cancellation and capabilities. Model registry separates provider endpoint from configured model/context/output capacity/tokenizer/capabilities. Configurable concurrency/queue capacity; initial local inference cap conservative and measured.
- Streaming-first; collecting the stream provides a nonstreaming result. Typed events include `started`, `text_delta`, `usage`, and exactly one terminal outcome of `completed`, `failed`, `cancelled` or `interrupted`. Preserve monotonic event sequences and `run_id`.
- Durable model runs independently of connections. Conversational submissions atomically persist user message and pending run; standalone submissions atomically persist a conversation-independent run and operation identity without an archive message. Both execute outside database transactions. For conversational runs, final output is committed atomically with the terminal run state and an eligible ingestion job. Standalone runs finalize result and terminal state without canonical messages or memory ingestion. Partial output checkpointed separately. Cancellation explicit and idempotent; client disconnect does not automatically cancel.
- No automatic restart after user-visible output; limited transient retries before output only; one controlled overflow retry if safe. No automatic hosted fallback. Regeneration creates a separate conversation branch.

## 7. API and transport
- Shared FastAPI resource API at `/api/v1`, OpenAPI 3.1 with separate normative SSE event schema. No Mem0/Ollama types in public responses. Thin HTTP handlers.
- **Approved REST + SSE**: two-step idempotent message submission → durable `run_id` → `GET /runs/{id}/events` SSE. Run cancellation/status via REST. A stream attaches via authoritative snapshot followed by live deltas with **atomic handoff**. No durable token replay and no unsupported `Last-Event-ID` replay promise. Slow subscriber buffers bounded; overflow disconnects and requires new snapshot.
- Server-managed, silently renewable opaque sessions: secure HttpOnly same-site cookie (web) and opaque bearer credentials (CLI); `auth` resolves common trusted user context. Browser mutating requests protected against CSRF.
- Optimistic conversation version checks, explicit `Idempotency-Key` on create/submit/branch/regenerate operations, tenant-scoped error responses, opaque cursor pagination, ownership checks for all GET/SSE/POST operations.
- In-process event bus is replaceable and supports multiple subscribers, but does not own durable state. REST remains authoritative for run state, even after event loss.

## 8. Runtime
- Durable `runtime.jobs` queue with PostgreSQL `FOR UPDATE SKIP LOCKED`, leases, lease tokens and bounded retry scheduling; run executor separate from generic background jobs. Only short DB transactions; no DB transactions around inference or Mem0 calls.
- Known transient failure retries with capped exponential backoff/jitter; permanent failure terminal; ambiguous Mem0 ingestion outcome enters explicit reconciliation state or manual intervention. Lost worker lease cannot authorize stale completion; acknowledge that provider side effects may already have happened.
- Per-user memory ingestion serial; independent users parallel. Separate logical worker pools for interactive inference and background work. Resource contention at Ollama/GPU monitored.
- In-process typed event publisher/subscriber with bounded buffers. Future broker can replace it without changing services. Avoid additional microservices or Redis/Celery in v1.

## 9. Deployment and operations
- Single-host Docker Compose reference topology: backend (initially one API process with internal worker loops), PostgreSQL, Qdrant, Ollama (containerized or host-managed), one-shot Flyway migrator. Web UI deployed later. Non-root containers where feasible, pinned versions, persistent volumes.
- Flyway explicit SQL authoritative migrations. SQLAlchemy 2.x does not generate production DDL. Separate migration and runtime roles; validate expected schema compatibility at startup. PostgreSQL required for readiness; Ollama/Qdrant failure degrades affected features rather than globally rejecting archive operations.
- Typed startup-validated configuration; changes require restart. Secrets mounted/injected, never committed. Local services not exposed to untrusted networks. HTTPS for remote access.
- Structured JSON logs and OpenTelemetry-compatible metrics/tracing, optional dashboards; prompt/content text excluded from default logs. Request/run/job IDs correlated, bounded labels and retention.

## 10. Security and deletion
- Administrator-provisioned local accounts; public registration disabled. Argon2id passwords, revocable opaque server sessions, token hashes only, expiry/rotation/CSRF controls. PostgreSQL RLS forced for user-owned tables under non-owner runtime role, user-scoped composite FKs, transaction-local trusted user context; pooled sessions must not leak scope. Mem0 cross-user isolation must pass integration tests.
- HTTPS network access, encrypted host volumes and encrypted backups, without application field-level encryption initially. Explicit hosted model choice required.
- User-facing operations: **archive conversation**, **delete conversation + dependent conversation subtree**, **delete entire user/account/data**. Conversation deletion does not delete/alter memories; no selective memory delete API. Full account deletion uses durable deletion fence, revokes sessions, stops jobs, purges all user archive/attachments/operational artifacts and Mem0/Qdrant user data, verifies completion. Deletions must be re-applied on restore of older backups before exposure. Backup erasure depends on retention; do not claim instantaneous physical elimination of old backups.


## 11. Acceptance and build governance
- Unit tests: pure context assembler, capability validation, error mapping and resource contracts. Real PostgreSQL/Flyway integration: RLS, branching/revisions, transactions, concurrency, claim leases and purge. Real Mem0/Ollama/Qdrant smoke/compatibility gate; deterministic fake providers for ordinary CI; end-to-end CLI smoke. All release gates in `ACCEPTANCE_TESTS.md`.
- Milestones: M1 foundation/schema; M2 conversations/API; M3 inference/context/SSE; M4 backend client-integration capabilities (not Go implementation); M5 Mem0/memory ingestion; M6 reliability/security/backups/telemetry; M7 release acceptance. Mem0 compatibility spike early. Agents receive bounded tasks, exclusion rules, tests and completion evidence; no architecture reinterpretation.


## 12. Additional v1 client-facing backend capabilities
- **Standalone one-shot inference** is a first-class authenticated durable run without a conversation ID or canonical message. By default it retrieves available user-scoped managed memory (with optional soft project hint), but it **never** enqueues managed-memory ingestion; Mem0 alone judges importance for eligible persisted conversational turns. Project-only one-shot requests create a normal persisted conversation, as documented in the CLI contract; standalone mode is selected only when neither an explicit nor sticky conversation/project applies. Do not add a second inference engine.
- **Standalone retention:** persist enough protected run/checkpoint/result/operation metadata for reliable short-term attachment/recovery; adopt a documented bounded retention policy in T21 and T23. Standalone results do not appear in conversation history or archive backups indefinitely. Deleting/expiry of standalone state must invalidate recovery cleanly, and respect user purge.
- **Project reassignment:** an authenticated, owner-checked, version-guarded PATCH moves an existing conversation between projects or to no project without rewriting messages, immutable inherited revisions, or past Mem0 records. Subsequent requests use current association. Branches are independently assigned.
- **Operation lookup:** authenticated lookup of accepted operations by user-scoped idempotency identity resolves run and created-conversation IDs. Never return another user's identity or prompt. Replay of the same key and same request returns original result; different payload is a conflict. Idempotency resolution precedes expected-version checks on replays. Lookup of missing keys is not evidence a request can safely be reconstructed after a client crash.
- **Session renewal:** CLI sessions persist across restarts/standby and are extended silently server-side within configured idle and absolute expiry constraints; no user-managed token or environment variable. Reuse the existing opaque bearer where safe; explicit logout, disablement and expiration revoke access. One session shared by concurrent CLI processes must remain valid without race-prone mandatory client-side token rotation. Browser-specific CSRF guarantees remain.
- **Run recovery:** all run types support REST status, idempotent cancellation and SSE snapshot/live atomic handoff; no durable token replay is implied. Backend may survive client disconnect; post-restart statuses and interruption must be authoritative. CLI stores only operation metadata locally, not prompt bodies.
- **Deployment:** server endpoint for CLI is provisioned by installer/infrastructure settings, not a user-required environment variable. Local loopback first; HTTPS for remote family usage. Server secrets still follow secure infrastructure injection, not client environment configuration.
- **API ownership:** Mimir Server alone owns OpenAPI 3.1 plus normative SSE contract. Go and later web clients use generated/typed clients and may use contract mocks in independent repositories. The CLI-01–CLI-16 work plan is independently managed under `../cli/`.

---

# Appendix — Future enhancements and non-normative references

These ideas **are not v1 requirements**. All additional future work must be recorded in this appendix, not inserted into normative v1 chapters.

- **Advanced custom memory engine (v2 reference):** application-owned `memory.memories`, immutable memory revisions/evidence/audit lifecycle, contradiction handling, consolidation candidates, stable semantic identity, memory/project associations; supersession and retraction.
- **Custom Qdrant projection engine (v2 reference):** deterministic revision-specific points, projection generations, reconcilers, canonical PostgreSQL checks, versioned physical collections, alias/model migration.
- Selective individual memory browsing/editing/deletion, semantic forgetting, source-aware memory deletion, source-level provenance and rebuilding memory from surviving conversations; none are supported public v1 capabilities.
- Conversation RAG and separate historical-conversation Qdrant collection; hybrid/sparse search, rerankers, quantization and conversation summaries.
- Multi-instance backend, broker-backed event distribution, distributed worker coordination, advanced scheduling, optional WebSocket event transport, durable event replay and collaborative live sessions.
- Advanced authentication federations (OIDC/OAuth), field-level encryption, hot configuration reload, multimodal memory ingestion and complex tool/voice workflows.
- `appendix/v2-reference/` stores the prior advanced memory drafts only as historical design references. Their SQL cannot be migrated as part of v1.

## V1 resolution and review authority
The selected v1 behavior baseline is `CONTRACT_DECISIONS_V1.md`, with lifecycle rules in `STATE_MACHINES.md`, context format in `PROMPT_AND_MEMORY_CONTRACT.md`, and wire schemas in `contracts/`. Older wording describing G01–G15 as unanswered or suggesting T20 may choose public payload shapes is superseded. Every feature PR must pass the **mandatory stop / independent-agent + human review** in `PR_REVIEW_TEMPLATE.md`; no subsequent branch work before authorized merge and reassignment.
