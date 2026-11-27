# Approved Mimir v1 decision ledger — authoritative reconciliation

These are approved owner decisions from Database Reviews 1–7, API Reviews 1–6, Governance Reviews 1–5, CLI Reviews 1–6, and System Audits 1–5. Component design/contract artifacts must implement these rules; conflicting historical text is superseded and should be corrected before feature implementation. No new default may be invented by an agent.

## Topology and packaging
- **Monorepo**, separate Python and Go builds; one canonical `/contracts` directory. Single-host **local-only** Linux/macOS deployment. API published to host `127.0.0.1` only; databases and container Ollama use private Compose network, no database published ports. CLI connects to local provisioned URL only, with no server profiles, LAN client, remote TLS proxy or `MIMIR_SERVER_URL` override.
- Complete **connected installation bundle**, not just Go binary: Compose-managed PostgreSQL 18, Qdrant, Flyway one-shot, Python API/worker and Ollama. All services and optional jobs use Compose networks and service DNS; host-network containers and host-gateway provider overrides are unsupported. The [owner-approved T02 networking correction](COMPOSE_NETWORKING_CORRECTION.md) retires the previous host-installed Ollama alternative. Services and images pinned; model downloads with informed administrator action. Persistent DB/vector volumes survive stop/upgrade/uninstall. Independent CLI builds for Linux/macOS amd64/arm64; native-platform smoke/PTY evidence required for claimed support.
- Manual, coordinated **offline/on-demand** PostgreSQL + Mem0/Qdrant backup/restore, with compatible versions, checksums and deletion-fence replay before opening service; no automatic scheduling/retention, WAL PITR or RPO/RTO target in v1. Installer/upgrade never deletes volumes or silently changes DB major versions.
- CPU/GPU probe records actual verified Ollama use; NPU detection informational only. **OpenVINO GenAI v2**; appendix defines future implementation. No GPU scheduler or auto provider switching.

## Data and security
- PostgreSQL **18** + Flyway **SQL-only**: T03 V001 roles/schema, T04 V002 identity/auth, T05 V003 archive, T06 V004 branches, T07 V005 runtime/jobs, strictly sequential; SQLAlchemy only ORM. Roles: `mimir_owner` NOLOGIN, `mimir_migrator`, `mimir_api`, `mimir_worker`, `mimir_auth`, `mimir_maintenance`. RLS tenant scope transaction-local; worker/maintenance procedures restricted; no normal BYPASSRLS.
- Message revisions immutable in DB; dense ordinals/revisions; nested branches freeze exact inherited revision IDs without copy and validate parent prefix. Run submission/idempotency/conversation version atomic; one active run per conversation, READ COMMITTED explicit locks, no transaction open during inference. One canonical finalization transaction commits assistant revision + terminal run + eligible memory job.
- Accepted operation ledger 30 days, tombstone next 30, unresolved evidence persists. Job FIFO per user with cross-user concurrency; lease 2m heartbeat 30s; retry at most 5, initial 5s capped 5m; uncertain Mem0 write reconciled, never blind replay. Stale run executors fenced. Already-started interrupted inference never regenerated automatically.
- Conversation subtree deletion HTTP 202, fenced, cancels outstanding source work, retains already-created managed memory; account deletion revokes sessions, fences, purges and verifies provider+archive, with independent journal effective even after backup restore.

### Owner-approved T05 clarification: canonical messages and immutability

Approved explicitly by the owner during the T05 implementation assignment. These
rules supersede inconsistent message-state wording in earlier design inputs.

- The archive owns authoritative conversational content; runtime exclusively owns
  inference execution state. Persisted canonical messages have only `completed`
  status and always select an immutable revision. A message may temporarily lack
  its revision within an uncommitted transaction, never after commit.
- A user message and its initial revision commit atomically with the associated
  pending run. An assistant message is created only on successful conversational
  inference, atomically with its immutable revision, terminal run state and eligible
  memory ingestion job. Only successfully committed user/assistant revision pairs
  are eligible for ingestion. Consecutive user messages are valid; strict role
  alternation is not an archive invariant.
- Provisional assistant output belongs in runtime checkpoints and streaming.
  Failed, cancelled and interrupted runs create no canonical assistant message;
  their state and available partial output remain runtime concerns. Clients may
  display these through existing run/checkpoint interfaces. Unsuccessful checkpoint
  retention and explicit retry behavior remain unresolved and outside T05.
- Revision records are immutable. After a message commits, its selected revision
  cannot be replaced or cleared and must belong to the same message and owner.
  Message identity, owner, conversation membership, role and ordinal are immutable.
  There are no application or internal operational exceptions. Authorized archive
  deletion and account purge remain permitted under their own lifecycle policies.
- Editing and regeneration create new conversations through the approved branch
  mechanism. Inherited history references exact immutable revisions without copying
  text. Retain separate message and revision tables despite one selected revision
  per committed message in v1.
- T05 enforces archive-local invariants in PostgreSQL. T06 owns branch snapshots;
  T07 owns runtime schema/checkpoints; T12 owns atomic user-message/run submission;
  T16 owns successful assistant/run/job finalization. No cross-domain transaction
  is implemented or claimed verified by T05.
- Contract reconciliation is required: `Message.status` in
  `contracts/openapi.yaml` must expose only `completed`. Its existing `pending`,
  `failed` and `interrupted` alternatives are inconsistent with this approval.
  T05 records the discrepancy without changing shared contract artifacts or their
  manifest. Contract modification/validation requires separate authorization through
  established S01 governance before public producers/consumers implement it.

## API and execution
- Backend-owned versioned OpenAPI 3.1, SSE schema, error catalog, fixtures, checksum, `/api/v1`, semver independently of app; `/api/v1/meta` compatible public discovery. Contract-first S01; no absent public details invented by implementation agents. snake_case JSON, UUIDv7 persistent new IDs, UTC RFC3339, typed errors, exact omission/null rules. Canonical `text/v1` message content **max 1 MiB UTF-8 for user AND assistant**; max generation tokens configured per model; no silent truncation. Projects/conversations page 50 max 200; messages page 100 max 500.
- Username/password login, Argon2id server verifier; opaque random 256-bit bearer session, server SHA-256 token hash; sliding idle 30d, absolute 90d, **no auth refresh endpoint**. Persistent Linux/macOS OS keyring by default; protected-file fallback opt-in 0700/0600, reject unsafe perms/symlinks; logout locally removes secret regardless of remote revocation outcome.
- Explicit conversation > explicit project > sticky conversation > sticky project > standalone; mismatched explicit project/conversation rejected. Standalone durable run, no canonical archive or memory ingestion, completed result retained 7d. Project-only creates a conversation. Client sticky state account/backend scoped, generation CAS and short locks; TUI navigation does not silently change one-shot selection. Model override affects invocation/TUI session only.
- SSE initial replace-all snapshot, eight event types (`run.snapshot`,`run.started`,`message.delta`,`run.usage`,`run.completed`,`run.failed`,`run.cancelled`,`run.interrupted`); contiguous delta sequence, gap-free snapshot/live bridge, 15s heartbeat, subscriber buffer 256; no Last-Event-ID persistent replay. Client reconnect 500ms starting/10s max/60s budget. Transport loss never cancels run. Cancellation request not immediate terminal outcome.
- CLI one-shot `ask` and `run attach` emit **canonical GET-confirmed** final result to stdout once; stderr diagnostics, no appended newline, no provisional stdout; partial OS stdout write cannot be guaranteed atomic. Prompt positional and optional stdin: positional prompt plus stdin is untrusted additional context; stdin-only prompt allowed, bounded read max 1 MiB, whitespace-only invalid. CLI exit codes 0 success, 1 general, 2 input, 3 auth, 4 connection, 5 conflict, 6 unresolved, 7 failed, 8 cancelled/interrupted, 9 config/credential.
- CLI state journal persisted before side-effect submission, operation IDs but **no prompt body**; 30-day completed cleanup eligibility, unresolved retained. Ambiguous lookup 200 known/410 expired/404 unknown, never automatically recreate submission even for unknown 404. Local-only connection deployment-provisioned; user config strict versioned TOML, theme semantic palette. TUI drafts scoped to target and memory-only, canonical/provisional distinct; exit doesn't cancel provider, scroll anchoring, PTY keybinding validation and untrusted escape sanitization.

## Inference and memory
- Separate provider-neutral inference interface (T13, Ollama T15) and `MemoryService` (T30, Mem0 T31). No shared generic provider superclass, no OpenVINO v1. Orchestrator constructs deterministic context, models of conversation, Mem0 generation and embeddings separately configured. Current prompt never silently truncated. Memory reference material cannot override system instruction.
- Qdrant isolated memory/search collections; Qwen3-Embedding-4B exact pinned configuration 2560 float32 cosine dense, immutable embedding config identity. Mismatched vectors fail closed and require validated rebuild/alias cutover. User filter plus canonical PostgreSQL validation.
- Committed exact user/assistant revision pair eligible for async Mem0 ingest; never standalone/provisional/failed/inherited branch prefix. Per-user FIFO; no dual writes or direct Qdrant bypass. Memory unavailable → explicit degraded response; ownership/isolation doubt → fail closed. Conversation completion does not wait for eventual ingestion. Background resource concurrency bounded.

## Operational and implementation governance
- Component-owned typed config, deployment-provisioned secrets, local loopback only. Structured redacted metadata-only logs; restricted health/dependency diagnostics; lightweight metrics but no required monitoring service. Graceful shutdown fences active execution and persists unresolved work.
- Task DAG owns implementation boundaries. CLI-12 and CLI-16 split; T20–T28 conformance rather than reimplementation; standalone execution shares T16 executor. One assigned task/branch/PR, independent agent review, human authorization to merge, stop before next task; protected files and tests cannot be weakened without approval. Requirements/test traceability; fake providers in deterministic CI, real dependencies/platform checks at milestones.

## Owner-approved S01 contract decision addenda

The owner explicitly approved S01-AUTH, S01-CANCEL and S01-PAGE during the S01
implementation assignment. These decisions supersede inconsistent draft wording.
Their complete wire requirements are recorded in the canonical
[HTTP semantics](../../contracts/HTTP_SEMANTICS.md).

- **S01-AUTH:** one T08 session service/identity model; CLI-issued bearer sessions
  and web-issued HTTPS-only `__Host-mimir_session` cookies, transport-bound by
  stored client type. Login discriminates CLI token JSON versus web session-only
  JSON plus secure cookie; fresh credential each login. Mutually exclusive
  credentials, 400 `ambiguous_credentials`, 401 `unauthenticated`; browser mutations
  require exact configured Origin and `X-Mimir-CSRF: 1`, with 403 `forbidden` on
  failure. Trusted host, no permissive CORS, JSON bodies only, no-store session
  responses, matching cookie expiry on logout. Existing 30d/90d lifecycle and
  redaction remain; no refresh/JWT/OAuth/additional endpoint. Browser TLS/frontends
  and HTTP middleware remain outside S01; TLS provisioning is outside T09 too.
- **S01-CANCEL:** both success codes return canonical Run from one snapshot:
  202 for durably requested cancellation while nonterminal with the flag true;
  200 for any unchanged terminal outcome with the flag false, including immediate
  cancellation/completion races. No separate idempotency key, terminal conflict,
  provider wait or late terminal overwrite. Pending cancellation before worker
  claim wins; running outcomes use the first valid terminal commit. GET resolves
  subsequent state; SSE terminal events follow commit. Both run kinds agree.
- **S01-PAGE:** exclusive two-column keysets: projects created_at/project_id DESC;
  conversations updated_at/conversation_id DESC. Latest active is first owned,
  visible, unfenced active conversation, including a nonterminal run; use existing
  pages, no new filters/endpoints/columns. updated_at tracks committed canonical
  creation/messages/actual metadata changes atomically and monotonically, never
  reads/provisional/runtime-only/session/memory activity or no-op metadata.
  Flattened V004 history initially selects newest, returns ascending visible
  ordinals, and pages older using the smallest ordinal as exclusive boundary.
  Appends/version advancement do not invalidate older boundaries. Opaque,
  integrity-protected, versioned cursors bind owner/resource/parent/filters/order/
  boundary, remain subject to authorization/RLS/fences, and need not retain a
  boundary row. Invalid scope/tampering/format is 422 `invalid_request` on accessible
  resources. Page-size changes are allowed; exhaustion/empty returns null cursor.
  Mutable listings have per-page committed reads, no cross-request frozen snapshot;
  clients may restart. Preserve 50/200 and 100/500 limits; no offset pagination,
  mandatory cursor persistence or long-lived snapshot infrastructure.

T08 inspection confirms stored client_type, common session metadata, fresh 256-bit
issuance and hash-only 30d/90d renewal/revocation support. V003 permits monotonic
canonical timestamp maintenance and existing API/worker update grants; V004 exposes
flattened inherited-plus-offset-local visible ordinals. No runtime or applied SQL
changes are authorized or made by S01. Runtime services/transactions must enforce
the added HTTP requirements in their assigned tasks. The initial contract remains
1.0.0 and a candidate pending independent review and explicit human approval.

## Owner-approved S01 independent-review remediation

**S01-DISCOVERY (R1):** The owner explicitly approved ConversationDetail on the
existing conversation GET, with required nullable active_run_id. Summary schemas
stay unchanged. The field derives an owner-visible, unfenced pending/running
conversational run (including requested cancellation), from the same authoritative
read snapshot as metadata. Null is only absence on an accessible conversation;
404 conceals inaccessible conversation/run existence and dependency errors remain
errors. No canonical version/timestamp mutation, persisted column, migration or
new endpoint. Client detail → run GET → snapshot-first SSE → canonical history
handles completion races without resubmission or inferred IDs. Full semantics and
T10/T11/T20/T28/CLI-03/10/11/14/15 ownership are in
[HTTP semantics](../../contracts/HTTP_SEMANTICS.md). Runtime remains deferred.

**R2/R3:** The owner approved exact chronological/UUID normalization in the
contract reference model and stronger authentication definition/header wiring and
semantic mutation tests. S01-PAGE and S01-AUTH public behavior is unchanged.
Contract 1.0.0 remains the initial candidate, pending renewed independent review
and explicit human contract/merge approval. This approval authorizes remediation,
not acceptance of findings by the independent reviewer or task completion.

## Owner-approved T09 independent-review remediation

The owner accepted T09-R1 and authorized its HTTP error mappings. LoginCredentialConflict
returns 400 `ambiguous_credentials`. UntrustedHost, InsecureBrowserTransport and
ForeignReadOrigin return 403 `forbidden`. ExpectedJSON, malformed JSON and invalid
UTF-8 request-body parsing failures return 422 `invalid_request`. The canonical
Error envelope and existing catalog apply. Parsing translation is confined to
request-body decoding, preserving unrelated HTTP exceptions and server failures.
JSON transport/parsing and browser security checks precede session renewal and
profile mutation. The normative details and agreement fixtures are in
[HTTP semantics](../../contracts/HTTP_SEMANTICS.md) and
[auth cases](../../contracts/fixtures/auth-cases.json).

The owner also accepted T09-R2 and authorized the scoped T09 coverage entry in
`TRACEABILITY.md`. This records executable paths, verification evidence and
limitations while preserving existing normative acceptance ownership and
later-task verification responsibilities. The corrections require renewed
independent review and human merge authorization.
