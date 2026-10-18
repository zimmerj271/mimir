> **Normative v1 reconciliation:** See `../docs/decisions/FINAL_V1.md` for the approved deployment, lifecycle, contract and governance rules. Historical conflicting implementation suggestions do not authorize changes.

# Canonical HTTP API and SSE protocol — v1 design

## Rules
- FastAPI/Pydantic models, generated OpenAPI 3.1 reviewed at `../contracts/openapi.yaml` before implementation; during implementation generated FastAPI output must conform; no OpenAPI file is claimed generated here. Prefix `/api/v1`. Both clients share this contract. Thin HTTP handlers; no provider-native types.
- Secure session cookies web, opaque bearer session CLI; both resolve authenticated user. CSRF checks for browser mutations; restrictive CORS. `404` for unauthorized cross-user resource ID lookup; avoid existence leaks.
- Mutating create/submit/branch/regenerate operations require `Idempotency-Key` scoped to owner + operation + fingerprint and atomically recorded with domain changes. Conflicting reuse is `409`.
- `conversation_version` expected on visible-conversation mutations; mismatch `409`, never silent merge. At most one nonterminal generation per conversation.

## Endpoints (draft contract to formalize in M2/M3)
| Method | Resource | Notes |
|---|---|---|
| POST/GET | `/auth/login`, `/auth/session` | Establish/inspect session |

| GET/PATCH | `/users/me` | User preferences |
| GET/POST | `/projects` | Owner-only listing/creation |
| GET/PATCH/DELETE | `/projects/{id}` | Owner-only |
| GET/POST | `/conversations` | Owner-only listing/creation |
| GET/PATCH/DELETE | `/conversations/{id}` | Archive, metadata, or deletion as explicitly defined |
| GET | `/conversations/{id}/messages` | Visible branch-aware revisions, cursor pagination |
| POST | `/conversations/{id}/messages` | Commit user message + pending run atomically; `expected_conversation_version`, `Idempotency-Key` |
| POST | `/conversations/{id}/branches` | Create new conversation and frozen inherited prefix |
| GET | `/runs/{id}` | Durable status and current/final output |
| GET | `/runs/{id}/events` | Owner-only SSE subscription |
| POST | `/runs/{id}/cancel` | Idempotent cancel request |
| GET | `/models` | Enabled model configs and capabilities |
| GET | `/health/live`, `/health/ready` | Minimal public checks |
| GET | `/health/dependencies` | Restricted diagnostics |

**Not in v1 public API:** `/memories` and `/memories/{id}` list/edit/delete; individual forget commands. Admin-provisioning, full-account deletion and export are restricted operations; specifics must be finalized in security implementation without exposing provider internals.

## Two-step streaming
1. Client sends message via `POST /conversations/{id}/messages`, supplying `Idempotency-Key` and expected version; response `201` includes committed user message ID, new conversation version and `run_id`.
2. Client `GET /runs/{run_id}/events` with session. Server authenticates before SSE, emits authoritative `run.snapshot` of accumulated text with high-water sequence, then `run.started`, `message.delta`, optional `run.usage`, exactly one terminal `run.completed`, `run.failed`, `run.cancelled`, `run.interrupted` as applicable. Snapshot/subscribe must have atomic handoff. Event sequences monotonic per run.
3. Disconnect does **not** cancel a run. Reconnect gets new snapshot replacing displayed text, then live deltas higher than snapshot high-water. Never promise durable event replay or use SSE `Last-Event-ID` without actually implementing it. Bounded per-subscriber buffers; overflow terminates subscriber to force snapshot recovery.
4. Terminal completion SSE emitted only **after** final assistant revision and terminal run state commit. Failure before SSE handshake uses HTTP JSON error; failure after open uses terminal SSE event; transport break requires `GET /runs/{id}` for authoritative state.
5. Cancellation `POST /runs/{id}/cancel` returns `202` for requested cancellation and durable terminal status when achieved; cancellation races resolve to exactly one terminal status.

## Common errors
`{ "error": { "code": "stable_code", "message": "Human-readable message", "details": {} }, "request_id": "..." }`. HTTP 400/401/403/404/409/413/422/429/503/504 as appropriate. Typed codes for `conversation_version_conflict`, `idempotency_key_conflict`, `context_budget_exceeded`, dependency degradation and run conflicts. Cursors opaque, user/filter scoped with deterministic unique-tiebreak ordering.

## Transport independence
Domain events carry run/job/correlation IDs and typed payloads. Publisher/subscriber interface independent of SSE. Future WebSockets **optional**, not required v2; internal event channel initially in-process, connection-independent durable PostgreSQL run state.


## Client-required v1 endpoints and semantic constraints
All paths below are relative to `/api/v1`; endpoint shapes are **normative proposed contract inputs** to T20, then frozen through versioned OpenAPI/SSE schemas before Go client generation.

| Method | Path | Semantics |
|---|---|---|
| POST | `/runs/standalone` | `Idempotency-Key`; optional project ID, prompt/model; commits durable standalone run and returns `run_id`, no conversation or memory ingestion. |
| GET | `/operations/{operation_type}/{idempotency_key}` | Owner-scoped accepted-operation lookup. Returns accepted status, run ID and created conversation ID where applicable; unknown/expired is explicit. Do not return the request prompt. |
| PATCH | `/conversations/{id}` | Include nullable `project_id` and `expected_conversation_version`; owner/project checks, increments version, no history rewrite. |
| POST | `/conversations` | Idempotent creation returns conversation ID and version, including project association; `project_id` optional. |
| GET | `/runs/{id}` | Works for standalone and conversation runs; includes status and authoritative checkpoint/final result and run kind. |
| POST | `/runs/{id}/cancel` | Idempotent for both kinds; completion race resolved by canonical terminal status. |
| GET | `/runs/{id}/events` | Both run kinds use snapshot + live SSE with atomic handoff, no durable delta replay. |
| GET | `/auth/session` | Validates opaque credential and reports expiry/renewal policy without surfacing secret material; eligible activity silently extends sliding lifetime within absolute ceiling. |

**Submission and replay:** a new operation requires an authenticated owner, unique key and stable request fingerprint. A repeated key with matching fingerprint returns the original accepted outcome before any new expected-version rejection; mismatched fingerprint is 409. When the first response is lost, a still-running client may repeat the identical request; after a process crash, a metadata-only journal resolves accepted work through `/operations/...` and must never fabricate a new prompt or resubmit an unavailable body.

**Standalone execution and memory:** project-only one-shot CLI requests create a persisted conversation within that project, so `/runs/standalone` is intended for requests with no selected project or conversation. Standalone `MemoryService.search()` is user-scoped and project-hinted only if a caller supplies project context. No `MemoryService.ingest()` or committed message revisions result from standalone runs. Temporary standalone input/output/checkpoints are protected operational artifacts with retention/erasure policy, not archived conversations.

**Session model:** use revocable bearer session secret stored by CLI via platform keyring (or explicit protected fallback). Server stores its hash. No separate refresh token, public OAuth flow, or CLI `LLM_API_TOKEN` required. Silent server-side expiration extension must be race safe; invalid/expired sessions require re-login. Cookies remain HttpOnly/SameSite and CSRF-protected for web clients.

**Client compatibility:** Go CLI and future web use same OpenAPI 3.1 and normative SSE event type/version definitions. Server emits typed errors with stable code, HTTP status and request/run identity; never expose secrets, other owners' resources or provider-native objects.

## Contract-first closure gate (supersedes draft timing)
The endpoints above are **design inputs only**, not an approved schema. The delivered `openapi.yaml`, SSE JSON schema, error code catalog and golden fixtures must be checked/approved and then verified by tests **before** implementation of public HTTP/event shapes or CLI-03. The older language saying T20 alone freezes the contract during implementation describes a superseded sequencing intention. T20 validates/publishes conformant generated FastAPI schema against the reviewed contract; it does not decide payload semantics. Do not invent JSON schemas, idempotency encoding, SSE synthetic event sequences or retention defaults. See `../contracts/README.md` and `../SPECIFICATION_GAPS.md` G02/G06/G09.

## V1 resolution and review authority
The selected v1 behavior baseline is `CONTRACT_DECISIONS_V1.md`, with lifecycle rules in `STATE_MACHINES.md`, context format in `PROMPT_AND_MEMORY_CONTRACT.md`, and wire schemas in `contracts/`. Older wording describing G01–G15 as unanswered or suggesting T20 may choose public payload shapes is superseded. Every feature PR must pass the **mandatory stop / independent-agent + human review** in `PR_REVIEW_TEMPLATE.md`; no subsequent branch work before authorized merge and reassignment.
