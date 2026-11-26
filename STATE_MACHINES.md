# Canonical lifecycle transitions v1

Transitions are atomic, owner-scoped and guarded with expected state/version/lease token where applicable. Repeated same terminal outcome is idempotent; invalid transitions fail with stable typed conflict, never silently overwrite evidence.

| Entity | From | Allowed next | Rule |
|---|---|---|---|
| canonical message | absent | completed | Authoritative content and selected immutable revision must exist at commit; user creation commits with pending run, assistant creation only with successful run finalization and eligible ingestion job |
| canonical message | completed | none | Identity/owner/conversation/role/ordinal and selected revision immutable; governed conversation deletion/account purge remain permitted |
| run | pending | running, cancelled, interrupted, failed | Cancellation before worker claim wins; restart may interrupt pending only on unrecoverable state |
| run | running | completed, failed, cancelled, interrupted | Exactly one terminal commit; terminal state and canonical output revision committed in one transaction |
| run | completed/failed/cancelled/interrupted | none | Immutable terminal state; cancellation returns terminal unchanged |
| job | pending | running, cancelled | Claim requires owned lease and `FOR UPDATE SKIP LOCKED` |
| job | running | completed, retry_wait, failed, cancelled, reconciliation_required | Lost lease cannot commit outcome |
| job | retry_wait | running, cancelled, failed | Exponential backoff capped and max attempts enforced |
| job | reconciliation_required | pending, completed, failed, cancelled | Manual/verified reconciliation with audit |
| job | completed/failed/cancelled | none | Immutable terminal |
| session | active | active (sliding renewal), revoked, expired | Absolute expiration immutable, idle renewal cannot exceed absolute expiration |
| session | revoked/expired | none | Never reactivate token |
| account deletion | requested | fenced | Fence+token invalidation commits before purge starts |
| account deletion | fenced | purging | Worker may retry idempotently |
| account deletion | purging | complete, verification_required | Cross-store verification required before complete |
| account deletion | verification_required | purging, complete | Reconcile orphaned vector/backup artifacts |
| account deletion | complete | none | Durable fence survives all backups/restores |
| recovery operation | unknown_local | accepted, not_accepted, indeterminate | `not_accepted` only when authority proves never accepted; metadata cannot reconstruct prompt |
| recovery operation | accepted | terminal_confirmed, accepted | Do not resubmit body when missing |
| recovery operation | terminal_confirmed | none | eligible for client journal pruning after configured retention |

**Run cancel race:** cancellation is a request, not a terminal outcome; whichever transaction commits canonical terminal state first wins. On `completed`, final assistant revision must exist; on all other terminal states, do not fabricate a successful revision. Interrupted work may be resumed only through explicit new operation/approved backend recovery algorithm; no hidden duplicate inference.

**Owner-approved T05 clarification:** canonical messages have no persisted pending,
failed, cancelled or interrupted state. A missing selected revision is permitted
only within an uncommitted transaction. Failed/cancelled/interrupted runs create
no canonical assistant message; execution state and provisional output remain in
runtime checkpoints/streams. Consecutive user messages are valid. Editing and
regeneration create new conversations; revisions and selected pointers are never
rewritten. Only successfully committed user/assistant revision pairs are eligible
for memory ingestion. T05 verifies archive-local constraints only; T12/T16 own
cross-domain atomicity. Unsuccessful checkpoint retention and explicit retry
behavior are unresolved by this clarification. The draft shared `Message.status`
enumeration requires separately authorized reconciliation; it is not changed here.


**Owner-approved S01-CANCEL:** cancel responses use one authoritative snapshot:
202 iff nonterminal with durably accepted cancellation_requested true; 200 iff
terminal with cancellation_requested false, including immediate cancellation or
completion winning during processing. A terminal Run is returned unchanged, never
409 solely for being terminal. Repeated requests have no duplicate side effects
and require no separate idempotency key. Before worker claim cancellation wins
atomically; running races use the first valid terminal commit, and late provider
output cannot overwrite it. Do not wait for provider cancellation. Both run kinds
agree; GET resolves later state and SSE reports committed outcomes. Runtime
concurrency/fencing is not established by S01 contract fixtures.
