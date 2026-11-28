# T10 conversation visibility foundation — approved scope and physical proposal

The owner explicitly authorized this boundary in the T10 implementation assignment.
This proposal is recorded before implementing its SQL. It does not claim execution
or approval of the candidate implementation by the independent reviewer.

## Physical schema and ownership

Use forward-only **V008**: merged T09 already owns V007 (session profile lookup).
Preserve V001–V007 byte-for-byte. Do not reuse V005 account deletion controls.

`runtime.conversation_deletion_fences` contains `user_id uuid NOT NULL`,
`root_conversation_id uuid NOT NULL`, and finite `fenced_at timestamptz NOT NULL`.
Its primary key is `(user_id, root_conversation_id)`; a composite foreign key
references `archive.conversations(user_id, conversation_id)` with restrictive
deletion. Each row means an **established** fence; there is no requested state,
expiration, public status, or production writer in T10. Root identity and timestamp
are immutable. The NOLOGIN `mimir_owner` owns the table. ENABLE/FORCE RLS uses the
existing transaction-local authenticated actor. API and worker roles receive only
SELECT; auth/maintenance/public receive no new privileges or definer routines.
Disposable tests establish rows using privileged fixtures, never runtime grants.

## Lookup and authentication

An invoker-rights `archive.visible_conversations` view is the shared conversation
eligibility boundary. It requires the current owner, active account, absence of an
established account fence, and absence of a conversation fence on the conversation
or any ancestor. Follow immutable V004 parent links under the same owner, including
archived ancestors; a fence does not affect ancestors or sibling branches outside
its subtree. Existing PK/composite owner indexes support ancestor traversal and
fence probes; the V004 owner/parent index supports descendant operations later.
No text, message revisions, run IDs or copied subtree membership enter the table.

An account control with `status = requested` and no `fenced_at` is not a fence.
All established V005 controls (including complete) have non-null `fenced_at` and
remain fences. The transaction boundary rechecks current account state/control;
missing/disabled accounts or established account fences raise the existing uniform
`AuthenticationRejected`, rather than returning empty lists or a resource 404.
Conversation invisibility raises the same domain not-found as a missing/foreign
resource. Database/visibility evaluation errors remain redacted storage failures;
they never become successful empty/null results. T09 remains the session verifier.

## Transactions and concurrency

Read operations use short read-only REPEATABLE READ transactions. Account checks,
conversation eligibility, metadata, history/list eligibility and active-run lookup
therefore share one authoritative snapshot. Detail derives pending/running
conversational run identity in its metadata query; cancellation requests still
qualify, terminal/standalone/foreign runs do not. There is no persisted reference,
read-side timestamp mutation, or canonical version increment.

Writes use READ COMMITTED, account-before-conversation locks, and fresh eligibility
checks after obtaining the conversation lock. Metadata/project changes use expected
version comparison and advance version/timestamp only for actual changes. Branch
creation locks the parent before reading the exact flattened prefix, inherits the
current project, and inserts reference rows atomically without inference/ingestion.
Branch creation does not change the parent. Current runtime writers cannot establish
fences. **T10 does not establish linearization between a future fence writer and
concurrent mutations**: T36 must coordinate acceptance/fence establishment with
branch creation and all other writers, including message submission/finalization.
The same applies to T35 account fence orchestration; T10 supplies established-fence
enforcement, not session revocation or a fence writer. A read may reflect the
snapshot before a concurrently committed fence; a subsequent read conceals it.

## Retention, cleanup and later owners

Fences have no TTL and never expire implicitly. The restrictive root FK prevents
silently discarding a fence during root removal. T36 must coordinate removal of
fence/root/subtree in its privileged cleanup transaction and retain the required
deletion evidence outside physically removed rows. T37 owns restoration/journal
replay before service exposure; this table alone does not provide restore safety.
No DELETE grants, deletion endpoints, run/ingestion cancellation, physical cleanup,
deletion acceptance, journal writer or restoration guarantee is delivered by T10.

T11 consumes this boundary for HTTP resources and handles opaque cursor encoding
and idempotent submission. T10 supplies typed exclusive keyset boundaries and
canonical ascending flattened history; it does not add public filters or contracts.
T35 retains account deletion orchestration, fencing and revocation. T36 retains
production conversation fencing, acceptance, writer coordination and cleanup.
T37 retains independent deletion evidence and restore replay.
