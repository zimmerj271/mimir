# HTTP semantic constraints (normative supplement to OpenAPI)

This supplement is contract-owned and must be checked with `openapi.yaml`, `errors.json`, fixtures and the authoritative approved decision ledger. No implementation may silently supply an unspecified public behavior.

- All JSON uses snake_case, UUIDv7 IDs for new persistent entities and UTC RFC3339 datetimes. Unknown request fields are rejected and nullable fields follow their schema. Message text has a **UTF-8 encoded** maximum of 1,048,576 bytes; JSON Schema `maxLength` alone cannot enforce this byte limit. The limit applies equally to user and final assistant content; overflow fails rather than silently truncates.
- Project and conversation cursor pagination defaults to 50, maximum 200; message pagination defaults to 100, maximum 500. Cursor values are opaque, user-scoped and stable under their documented ordering policy. No full-history export may stop after the first page.
- Deleting a conversation (`DELETE /api/v1/conversations/{id}`) returns HTTP 202 with a deletion identity, places the full subtree behind a durable deletion fence and performs asynchronous cleanup. It is not HTTP 204 synchronous deletion.
- Branch creation takes explicit `mode: after_revision | replace_message` with `source_revision_id` and `expected_conversation_version`; exact inherited revision IDs are frozen transactionally. Branch creation does not silently launch inference.
- `GET /api/v1/operations/{operation_type}/{idempotency_key}` returns 200 for retained accepted identity, 410 after expiration and 404 for unknown lookup. 404 is **not evidence** that the operation was never accepted. An expired standalone result may be absent while its operation identity is retained. No automatic replay after ambiguous acknowledgment.
- `POST /api/v1/runs/{run_id}/cancel` returns the authoritative Run with `cancellation_requested` until the terminal transition wins; a request does not imply a cancelled outcome.
- `GET /api/v1/meta` provides unauthenticated supported contract-version discovery but no sensitive diagnostics. One canonical opaque session has exclusive CLI bearer and HTTPS browser-cookie transports; see S01-AUTH below. There is no refresh endpoint.
- Standalone runs retain final result seven days, create no conversation revision and trigger no memory ingestion. Completed conversational runs atomically commit their canonical assistant revision, terminal run and eligible ingestion job.
- Provider errors and memory-degraded diagnostics must never leak prompt, secret or memory contents. Security/isolation ambiguity must fail closed; mere memory unavailability may permit explicitly labeled degraded inference.

## S01-AUTH — Owner-approved authentication resolution

One canonical T08 opaque session model supports two exclusive credential
transports. Protected requests accept CLI-issued sessions only through
`Authorization: Bearer`, or web-issued sessions only through the
`__Host-mimir_session` cookie. Validate the stored session client type. Never
choose precedence or fall back after validation fails. Both mechanisms resolve
the same authenticated-user context and preserve owner scope, RLS and fences.
Both mechanisms present, or duplicated/conflicting authentication credentials:
400 `ambiguous_credentials`. Missing, malformed, expired, revoked or
transport-incompatible credentials: 401 `unauthenticated`. Cross-user resource
lookups remain 404 without existence disclosure.

Login keeps `POST /api/v1/auth/login`, HTTP 201 and required `client_type`.
CLI login returns `CliLoginResponse` (`access_token`, CLI `session`). Web login
returns `WebLoginResponse` (`session` only) and `Set-Cookie`; `access_token` is
absent, never null. Every successful login issues a fresh credential; incoming
credentials are never reused. No login may carry Authorization. Web login may
replace an existing cookie, even expired, without authenticating with it. CLI
login must not carry the Mimir session cookie. The request-body discriminator
selects the login response; subsequent authorization uses the stored client type.

Issue `__Host-mimir_session` with HttpOnly, Secure, SameSite=Strict, Path=/,
no Domain, and Max-Age no greater than the remaining absolute session lifetime.
Browser login and cookie authentication require HTTPS and reject insecure
transport. CLI retains the deployment-provisioned loopback HTTP transport.
Browser TLS provisioning and frontend implementation are outside S01/T09.

Browser architecture is first-party and same-origin. All browser state-changing
requests, including web login/logout, require `X-Mimir-CSRF: 1` and an Origin
exactly equal to the explicitly configured trusted browser origin. Missing, null,
malformed or unauthorized origins and missing/incorrect CSRF headers fail with
403 `forbidden`. Validate host against trusted deployment configuration; arbitrary
Host or forwarded headers cannot establish trust. No cross-origin browser access
or permissive credentialed CORS. Require application/json for operations with
JSON request bodies; form-encoded alternatives are forbidden. CLI bearer requests
do not require CSRF. The custom header is not a credential or secret token.
Authenticated GET, including SSE, needs no CSRF header; reject unauthorized Origin
when present. GET cannot change business state; implicit session renewal is allowed.

Credentials have 256 random bits; persist only SHA-256 hashes. Idle expiry slides
30 days, capped by immutable 90-day absolute expiry. Renewal is implicit, race-safe
and does not require token rotation. No refresh token, refresh endpoint, JWT, OAuth,
additional auth endpoint, identity store or separate authentication service.
`GET /auth/session` returns metadata only. Logout revokes the current session and
returns 204; web logout also expires the cookie with matching name, Path=/,
HttpOnly, Secure, SameSite=Strict and no Domain. Session credentials/private session
metadata responses use `Cache-Control: no-store`. Never log or include credentials,
passwords, Authorization or cookie values in diagnostics, committed fixtures or
errors. Schema examples omit credential values; ephemeral harness values are not
usable sessions. Runtime enforcement belongs to T09/T24/T20/T28.

## S01-CANCEL — Owner-approved cancellation resolution

`POST /api/v1/runs/{run_id}/cancel` returns the canonical Run schema in both
successful responses. 202 means cancellation is durably accepted, the response
snapshot is nonterminal and `cancellation_requested: true`. Repeated requests
against that state return 202 without duplicate side effects. 200 means the
response snapshot is terminal (completed, failed, cancelled or interrupted),
with `cancellation_requested: false`; return the committed outcome unchanged,
including immediate cancellation or completion winning before the response.
Already-terminal is not a 409 conflict. Nonexistent/foreign-user runs return 404.

Status and body come from one authoritative snapshot; there is no promise that
state remains unchanged afterward. Do not wait for provider cancellation to finish.
No separate idempotency key is required. Pending cancellation before worker claim
wins atomically; running cancellation races use the first valid terminal commit.
Late provider results cannot replace terminal state. Both run kinds use these rules.
GET Run resolves later state; terminal SSE reflects committed outcomes. Contract
fixtures cannot prove transactional concurrency, worker fencing or provider behavior.

## S01-PAGE — Owner-approved pagination and selection resolution

Projects order by `(created_at DESC, project_id DESC)`; conversations by
`(updated_at DESC, conversation_id DESC)`. Both columns form an exclusive keyset
boundary. Return owner-accessible projects and owner-accessible, unfenced
conversations, preserving existing archival/project visibility. There is no new
public filter, sort parameter or endpoint. Latest active is the first owned,
authorized, unfenced `status: active` conversation in this ordering. Consumers may
traverse further pages; an active conversation with a nonterminal run is eligible
and retains run discovery/reattachment. If none exists, preserve the approved
creation workflow; opening the UI does not implicitly create a conversation.
Clients must not approximate this ordering using local timestamps or cached activity.

`updated_at` records the most recent committed canonical conversation modification:
creation/branch creation, user message commitment, final assistant commitment or
actual metadata changes (title/project/archival status). Maintain it atomically
with that archive mutation, monotonically and with existing conversation-version
rules. No separate activity column or version semantics. Reads/resume, subscriptions,
provisional output, runtime-only transitions, cancellation without archive changes,
memory activity, session renewal and no-op metadata updates do not advance it.

History is the canonical flattened V004 visible path, with inherited ordinals and
offset local ordinals. Initially select the newest `limit` messages; return them
ascending by visible ordinal. Older requests use the preceding page's smallest
visible ordinal as an exclusive upper boundary, selecting the next older page and
returning it ascending. Clients prepend pages without reversing or reconstructing
ancestry. New appends do not shift older boundaries; conversation_version advancement
alone does not invalidate a cursor. Runtime checkpoints/provisional output are excluded.

Cursors are opaque and integrity-protected, bound to owner, resource type, applicable
parent identity, effective supported filters, ordering policy, exclusive boundary
and cursor format version. Encoding/integrity implementation is runtime discretion;
no client-generated cursor, offset paging or mandatory server-side cursor storage.
A cursor never bypasses current authentication, authorization, RLS or deletion fences.
On otherwise accessible resources, malformed/tampered/unsupported/scope-mismatched
cursors return 422 `invalid_request` without cross-owner existence disclosure.
Page size may change within the existing 50/200 project/conversation and 100/500
message defaults/maxima without changing traversal scope. Do not require the boundary
record still to exist. `next_cursor: null` means no further eligible rows at the
page-read snapshot, including empty results.

Each project/conversation page is deterministically constructed from its database
read over current committed data. No frozen snapshot spans requests. Concurrent
insertions, updates, archival, reassignment or deletion may alter eligibility;
an updated conversation may move ahead of a previous boundary. Restart without a
cursor to refresh. No long-lived transactions, materialized listing snapshots or
new pagination persistence. History retains stable older boundaries under append,
subject to current authorization/deletion. Runtime SQL/timestamp/cursor integrity
and CLI scrolling conformance remain later-task checks.

Canonical Run lifecycle timestamps retain the existing V005 invariant: a non-null
completed_at must be at or after created_at as a chronological instant. Contract
validation uses the same exact timestamp normalization as chronological keysets;
formats, precision, status and nullability requirements remain unchanged.

## S01-DISCOVERY — Owner-approved conversation active-run discovery (R1)

`GET /api/v1/conversations/{id}` returns `ConversationDetail`: all Conversation
summary properties plus required, nullable, read-only `active_run_id` (UUID or
null, never omitted). List/create/patch/branch and other summary responses retain
Conversation unchanged. No new endpoint, request field, query or sort option.
The two closed schemas share property definitions rather than extending a closed
Conversation with allOf. Unknown properties remain rejected.

Metadata and active_run_id come from one consistent authoritative database read
snapshot. Only an owner-authorized, visible, unfenced conversational run for that
same conversation with status pending or running qualifies; cancellation_requested
true remains eligible. Completed/failed/cancelled/interrupted and standalone runs
never qualify. V005 `runtime.model_runs` and `model_runs_one_active_conversation`
already enforce at most one nonterminal run per owner/conversation. Derive the
reference; no persisted column, index or migration is introduced. Discovery and
changes to this runtime reference alone never increment conversation_version or
advance updated_at.

Enforce conversation authorization, RLS and deletion fences before disclosure.
Nonexistent, inaccessible, fenced and foreign-owner conversations return the same
existing typed 404, without disclosing run existence. Null means an accessible
conversation has no eligible run at that read snapshot; it never substitutes for
an authorization failure or runtime dependency failure (retain typed failure,
including 503 dependency_unavailable where applicable).

A client with no submission metadata selects an eligible conversation from the
existing listing (traversing archived pages) or explicitly selects its ID, GETs
ConversationDetail, then uses only non-null active_run_id to construct existing
GET `/api/v1/runs/{run_id}` and, when nonterminal, GET
`/api/v1/runs/{run_id}/events`. Null proceeds to ordinary history loading. Do not
infer run IDs from message IDs, cached operations, timestamps or undocumented
fields. This does not replace operation/idempotency-key recovery for ambiguous
submissions. The discovery response is a snapshot, not a continued liveness promise.
A run may finish before status inspection or SSE attachment. Preserve snapshot-first
SSE and terminal handling; when completion wins, recover canonical committed
conversation history. Never fabricate cancellation, resubmit a prompt or create a
run as recovery. Existing GET/SSE visibility and typed failure rules still apply.

T10 owns the owner-scoped domain/repository snapshot boundary and derived lookup;
T11 integrates the runtime lookup and ConversationDetail HTTP response, preserving
summary responses. T07 is already merged into canonical main, so its V005 runtime schema is
available to T10/T11. T07 is not an ancestor of T11 in the current canonical task
dependency graph; merged availability is distinct from a declared dependency. T20/T28 verify actual generated backend
schema and client protocol conformance. CLI-03 owns typed detail/null consumption;
CLI-10 latest-active/selector discovery, CLI-11 canonical history reconciliation,
CLI-14 snapshot/terminal reattachment and CLI-15 startup/resume/recovery races own
actual client behavior. These are obligations, not implemented S01 runtime claims.

### R2/R3 agreement clarifications (unchanged S01-PAGE/S01-AUTH behavior)

Chronological keysets compare timestamp instants, with equivalent RFC3339
fractional representations equal and all supplied fractional precision retained;
UUID tie-breakers compare identities. Sorting and exclusive cursor comparisons
use the same normalization. No fixed timestamp spelling or precision is required.
Removed boundary records and mutable-listing limitations above remain unchanged.

OpenAPI parameter `x-mimir-browser-requirement` and header `x-mimir-cookie`
metadata make the existing conditional requirements machine-checkable. They agree
with the normative descriptions and fixture policies: browser mutations require
CSRF value 1 and exact trusted Origin, authenticated GET needs no CSRF and rejects
unauthorized Origin if supplied; CLI bearer does not acquire browser-only duties.
Login 201 Set-Cookie references SessionCookie, logout 204 references
ExpiredSessionCookie; each is required for web and absent for CLI. Logout expires
with Max-Age=0; issuance has positive Max-Age bounded by remaining absolute lifetime.
Both retain HTTPS/HttpOnly/Secure/SameSite=Strict/Path=/ and no Domain. Applicable
authentication success responses reference required NoStore via Cache-Control.
The executable checks inspect wiring, resolved definitions, structured agreement
policies and complete normative descriptions, independently of manifest integrity.
