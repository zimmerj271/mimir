# Canonical lifecycle transitions v1

Transitions are atomic, owner-scoped and guarded with expected state/version/lease token where applicable. Repeated same terminal outcome is idempotent; invalid transitions fail with stable typed conflict, never silently overwrite evidence.

| Entity | From | Allowed next | Rule |
|---|---|---|---|
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
