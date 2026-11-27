# Requirement-to-task-and-test traceability

S00 introduces the [machine-checked registry](docs/acceptance/registry.json) and
[interpretation notes](docs/acceptance/README.md). The seed below remains the
approved historical ownership input; registry source-qualified IDs preserve
distinct assertions and resolve the approved CLI-16 split without changing tests.

**Target artifact**, to complete before executable API/domain implementation. The source of acceptance IDs is `backend/ACCEPTANCE_TESTS.md`; no test exists simply because a scenario is described.

Every approved task brief must include `Acceptance IDs: ...` pointing to existing acceptance IDs or a newly approved ID; every ID must map to at least one task and a real test file; every contract operation and high-risk invariant must map to an acceptance ID. Unimplemented tests are tracked as **not implemented**, never implicitly passed.

Mandatory matrix columns: `requirement ID | normative source | implementation owner task | automated test path | fake/integration/e2e | status | last verified commit | evidence`. Initially all test path/status columns are **unpopulated**. T01/CLI-01 should introduce a CI checker that rejects dangling IDs and, after test paths are populated, missing files. T40 audits cross-component coverage, T41 release blocks on unresolved critical IDs.

Examples to seed the matrix (not test-pass claims): auth/session renewal → T24/CLI-04; conversation reassignment → T25/CLI-05; idempotent creation → T26/CLI-05; accepted-op recovery → T23/CLI-07; SSE/status/cancel reconciliation → T27/CLI-14/CLI-15; account deletion fences → T35; restore safety → T37; cross-tenant memory isolation → T31/T34.

## Complete acceptance ownership seed (not test execution evidence)

T08 authentication/provisioning coverage → `backend/tests/test_auth.py` and
`tests/integration/test_database.py` (`test_t08_*`). API-SESSION-01 receives
service-level 30-day idle/90-day absolute lifetime, hash lookup and silent
renewal coverage. AUTH-RENEW-01 receives real PostgreSQL concurrent bearer reuse,
absolute ceiling, logout, account disablement and revoked/expired rejection
coverage. SEC-01 receives local administrator provisioning, Argon2id verification,
256-bit bearer issuance/hash-only persistence, restricted routine grants and
credential-table mutation denial coverage. Supporting DB-01/DB-02,
DB-ROLE-01/DB-MIG-01 evidence includes FORCE RLS, pooled transaction context
restoration, fresh/repeat/restart V001–V006, V005 upgrade preserving accounts,
sessions/runtime data, transactional failure/recovery and checksum drift.
Disable/revoke/expiry-during-lock races and competing provision/renewal use real
PostgreSQL transactions; deterministic tests cover crypto/resource bounds,
password-work cancellation and redacted error/secret handling. The owner
explicitly authorized T08's forward-only V006 restricted mutation interfaces;
V001–V005 and public contracts are unchanged. HTTP routes, cookie/CSRF/origin
guards (T09), public producer/consumer conformance (S01/T20/T24), full purge,
restore and cross-user run/job access remain successor work. Normative ownership
and requirement status are unchanged. Literal results, limitations and exact
submitted review SHA belong to the T08 PR.

T07 runtime storage coverage → `tests/integration/test_database.py`
(`test_t07_*`), real PostgreSQL 18/Flyway. Tests cover fresh/repeat/restart
V001–V005; V004 upgrade with retained branch history; deliberate failed V005
rollback/recovery and checksum drift; forced RLS, missing context, row-security-off
failure and actual role grants on all runtime tables; run shapes/owned messages,
one active conversation run, terminal/checkpoint constraints, attempts, exact
successful ingestion pairs, duplicate delivery rejection, competing SQL claims,
per-user ingestion serialization, stale token predicates, lease/reconciliation
shape, attempt bounds, typed operation identities and retention/resolution
boundaries, standalone UTF-8 input/result limits, temporary-input owner isolation
and runtime write denial, and expired-artifact storage shape with input erasure.
This supports DB-01/DB-02/DB-06 and DB-MIG-01; DB-ROLE-01, JOB-01/JOB-02,
RUN-STAND-01/RUN-STAND-02, OP-LOOKUP-01/OP-LOOKUP-02 and SEC-01 receive only
storage/protocol support. Executor/queue/HTTP services, actual lifecycle cleanup,
independent deletion journal/replay, full purge and provider effects remain later
tasks. No normative ownership/status is changed. SQL protocol probes do not prove
the behavior of future production services. The T07 PR records literal command
results, limitations and exact submitted SHA; no public contract has changed.
PR #9 R1 restores architecture section 10's host-volume/backup encryption baseline
without application field encryption or key management. Encryption of host
volumes/backups is not verified by these PostgreSQL storage tests.

T06 supporting branch-storage coverage → `tests/integration/test_database.py`
(`test_t06_*`), real PostgreSQL 18/Flyway. DB-04 covers flattened nested prefixes,
exact inherited revision references, both approved branch boundaries, empty
prefixes and parent append/revision-mutation isolation. DB-01/DB-MIG-01 receive
V004 fresh/repeat/restart, V003 upgrade, transactional rollback/recovery and
checksum-drift support. DB-02/DB-ROLE-01 receive runtime RLS/view isolation,
missing/malformed actor context, least privilege and composite owner FK tests.
DB-05 receives competing branch/append/delete transaction support; DB-07 receives
dependency-FK retention support only. Negative tests reject incomplete, gapped,
reordered, extra or substituted snapshots, immutable-link/reference changes and
invalid branch points; deferred validation survives actor changes and savepoint
rollback. Branch services, HTTP/authentication, version/idempotency transactions,
runtime atomicity, governed subtree purge, memory behavior and complete V001–V005
acceptance remain successor work. Normative ownership/status are unchanged;
actual commands/results and submitted SHA belong to the T06 PR.

T06 review R1 supporting reliability coverage additionally exercises 1,000,
2,000 and 5,000 inherited references from root/nested parents. Transaction-local
PostgreSQL function counters require exactly one full-prefix validator call per
created conversation, including split inserts and multiple branches. Immediate
bounds and deferred exact-prefix failure timing, explicit constraint checks and
attempted extension of empty/complete snapshots preserve DB-04 storage integrity.
Actual timings and revised candidate V004 checksum belong to PR #8; no release
latency target or broader service/runtime acceptance is implied.

T05 supporting archive-local coverage → `tests/integration/test_database.py`
(`test_t05_*`), real PostgreSQL 18/Flyway. The suite covers V003 fresh/repeat/restart,
V002 upgrade rollback/recovery and drift, atomic message/revision pairs, mandatory
selected revisions at commit (including actor-context changes), completed-only
status, same-owner/same-message pointers, immutable message fields/revisions,
consecutive user messages, UTF-8 boundaries, dense numbering, real runtime RLS and
grants, composite owner relationships and competing appends/metadata CAS.
This supplies archive-local DB-01/DB-02/DB-03 and DB-05 storage support, plus
supporting DB-ROLE-01/DB-MIG-01 evidence. Full submission/run/job atomicity, branch
snapshots, inference, deletion and V001–V005 acceptance remain successor work.
The owner-approved clarification is recorded in
`backend/milestones/T05_archive-core-schema.md` and `docs/decisions/FINAL_V1.md`.
Normative acceptance ownership/status remain unchanged; actual results and exact
submitted SHA belong to the T05 PR. The shared contract status mismatch is recorded
for separately authorized reconciliation, not claimed resolved by T05.

T04 supporting identity/session database coverage →
`tests/integration/test_database.py` (the `test_t04_*` cases), using real
PostgreSQL 18/Flyway. This covers V002 fresh/repeat/restart, V001-to-V002 upgrade
and transactional rollback/recovery, checksum drift, owner integrity, exact
case-sensitive logins, restricted pre-authentication lookups, actual runtime
privileges/RLS and transaction-context clearing, session storage/lifetime guards
and competing uniqueness/renewal updates. It supplies the T04 subset of DB-01,
DB-02 and supporting DB-ROLE-01/DB-MIG-01 database evidence. SEC-01,
API-SESSION-01 and AUTH-RENEW-01 receive storage-level support only; password
verification, provisioning/session services, HTTP authentication/renewal and
full V001–V005 acceptance remain unimplemented by this task. Normative acceptance
ownership/status are unchanged. The owner's approved context is recorded in
`backend/milestones/T04_auth-schema.md`; exact results and reviewed SHA belong
to the T04 PR, not this mapping.

T03 supporting role/schema coverage → `backend/tests/test_migrations.py` and
`tests/integration/test_database.py`; deterministic policy failures and real
PostgreSQL 18/Flyway integration. The suite covers V001, runtime privilege denial,
forced-RLS test fixtures and synthetic incremental failure/recovery. DB-ROLE-01,
DB-01/DB-02 and DB-MIG-01 retain their broader not-implemented status until actual
tenant tables and V001–V005 are supplied by later tasks. The registry's T03
`task_coverage` records this partial evidence without changing ownership or
claiming production RLS, full migrations, application readiness or release
acceptance. Actual command evidence and submitted SHA belong to the T03 PR.

T02 DEPLOY-01 configuration/isolation/persistence coverage is implemented by
`backend/tests/test_compose_policy.py` and `tests/integration/test_compose.py`.
The owner-approved T02 networking correction additionally maps to
`backend/tests/test_migrations.py` and `tests/integration/test_database.py` for all
optional migration jobs' policy rejection and live private-network attachments.
Default/backend/migration profile combinations reuse Compose-managed Ollama and
service DNS. Native host-installed Ollama support is retired by
`docs/decisions/COMPOSE_NETWORKING_CORRECTION.md`; the prior host-mode CI result
remains historical evidence. No normative acceptance owner is reassigned.
The real-engine suite uses a test-only backend port probe; application serving,
schema readiness, native macOS runtime and full installation acceptance remain
unverified. OPS-01 retains its not-implemented status for application/recovery
behavior owned by later tasks. Exact commits, commands and real results are in
the T02 PR; neither static policy tests nor a probe establish API correctness.

T01 supporting coverage: GOV-01 (normative source `docs/acceptance/REQUIREMENTS.md`)
→ `backend/tests/test_toolchain.py`, `backend/tests/test_environment.py` and
`tests/governance/test_checks.py`; unit/toolchain integration; executable tests
introduced by T01, with command outcomes and exact reviewed SHA recorded in its
PR. The `task_coverage` mapping in the registry preserves S00/T41 ownership and
does not claim application or final governance acceptance. T01's normative scope
is `backend/milestones/T01_python-toolchain.md` at its recorded main base.

| Acceptance ID | Responsible implementation tasks | Test path / status |
|---|---|---|
| DB-01 | T03–T07 | To create in task PR; NOT IMPLEMENTED |
| DB-02 | T03–T07 | To create in task PR; NOT IMPLEMENTED |
| DB-03 | T03–T07 | To create in task PR; NOT IMPLEMENTED |
| DB-04 | T03–T07 | To create in task PR; NOT IMPLEMENTED |
| DB-05 | T03–T07 | To create in task PR; NOT IMPLEMENTED |
| DB-06 | T03–T07 | To create in task PR; NOT IMPLEMENTED |
| DB-07 | T03–T07 | To create in task PR; NOT IMPLEMENTED |
| CTX-01 | T14 | To create in task PR; NOT IMPLEMENTED |
| CTX-02 | T14 | To create in task PR; NOT IMPLEMENTED |
| CTX-03 | T14 | To create in task PR; NOT IMPLEMENTED |
| INF-01 | T13–T16 | To create in task PR; NOT IMPLEMENTED |
| INF-02 | T13–T16 | To create in task PR; NOT IMPLEMENTED |
| SSE-01 | T18/T27 | To create in task PR; NOT IMPLEMENTED |
| SSE-02 | T18/T27 | To create in task PR; NOT IMPLEMENTED |
| API-01 | T09/T20 | To create in task PR; NOT IMPLEMENTED |
| MEM-01 | T29–T34 | To create in task PR; NOT IMPLEMENTED |
| MEM-02 | T29–T34 | To create in task PR; NOT IMPLEMENTED |
| MEM-03 | T29–T34 | To create in task PR; NOT IMPLEMENTED |
| MEM-04 | T29–T34 | To create in task PR; NOT IMPLEMENTED |
| MEM-05 | T29–T34 | To create in task PR; NOT IMPLEMENTED |
| MEM-06 | T29–T34 | To create in task PR; NOT IMPLEMENTED |
| MEM-07 | T29–T34 | To create in task PR; NOT IMPLEMENTED |
| MEM-08 | T29–T34 | To create in task PR; NOT IMPLEMENTED |
| JOB-01 | T32/T33 | To create in task PR; NOT IMPLEMENTED |
| JOB-02 | T32/T33 | To create in task PR; NOT IMPLEMENTED |
| OPS-01 | T02/T37/T39 | To create in task PR; NOT IMPLEMENTED |
| OPS-02 | T02/T37/T39 | To create in task PR; NOT IMPLEMENTED |
| SEC-01 | T08/T35/T40 | To create in task PR; NOT IMPLEMENTED |
| SEC-02 | T08/T35/T40 | To create in task PR; NOT IMPLEMENTED |
| OBS-01 | T38 | To create in task PR; NOT IMPLEMENTED |
| E2E-01 | T28/CLI-16 | To create in task PR; NOT IMPLEMENTED |
| PERF-01 | T39/CLI-16 | To create in task PR; NOT IMPLEMENTED |
| AUTH-RENEW-01 | T24/CLI-04 | To create in task PR; NOT IMPLEMENTED |
| RUN-STAND-01 | T21/T22 | To create in task PR; NOT IMPLEMENTED |
| RUN-STAND-02 | T21/T22 | To create in task PR; NOT IMPLEMENTED |
| OP-LOOKUP-01 | T23/CLI-07 | To create in task PR; NOT IMPLEMENTED |
| OP-LOOKUP-02 | T23/CLI-07 | To create in task PR; NOT IMPLEMENTED |
| PROJECT-MOVE-01 | T25/CLI-05 | To create in task PR; NOT IMPLEMENTED |
| SSE-STAND-01 | T18/T27 | To create in task PR; NOT IMPLEMENTED |
| CONTRACT-01 | T20/T28/CLI-03 | To create in task PR; NOT IMPLEMENTED |
| CLI-INTEGRATION-01 | T28/CLI-16 | To create in task PR; NOT IMPLEMENTED |


S01 contract-agreement coverage → `tests/contracts/test_contracts.py`,
`scripts/check_contracts.py`, `scripts/contract_protocols.py`, with governed
fixtures under `contracts/fixtures/`. GOV-02 receives OpenAPI 3.1/Draft 2020-12,
reference, fixture/error/version, exact raw-byte checksum and completeness checks.
Positive/negative scenarios support API-SESSION-01/API-01/SEC-01, SSE-01/SSE-02/
SSE-STAND-01, INF-02, LIFECYCLE-02 and OP-LOOKUP-01/02/CLI-REC-01 at the contract
level only. The registry supporting mapping preserves normative ownership and
not-implemented runtime status. The operation/source audit is
[recorded separately](docs/verification/S01_OPERATION_AUDIT.md), and
[execution evidence](docs/verification/S01.md) distinguishes historical provisional
runs from final agent locked contract/toolchain successes and verified human
full backend validation (105 tests; agent local socket IPC is denied). T09/T20/T24/T28 and CLI-03
must validate actual authentication/producer/consumer behavior; T10/T11/T26 and
CLI-05/08/10/11/14/15 validate runtime pagination/selection/scrolling. T12/T16/
T18/T19/T27 validate transactions, fences, provider cancellation and streaming.
No fixture proves database concurrency, TLS, cursor cryptography or runtime
conformance. Candidate 1.0.0 and human approval remain pending.

S01 independent-review R1 is resolved at the contract level by owner-approved
S01-DISCOVERY in [HTTP semantics](contracts/HTTP_SEMANTICS.md): distinct strict
ConversationDetail with required nullable active_run_id, snapshot/visibility and
read-only lifecycle rules, public discovery/status/SSE/history transcripts. R2
normalizes chronological timestamp instants and UUID identities for both keyset
operations. R3 binds authentication checks to referenced parameter/header
definitions and tests semantic mutations after refreshing disposable checksums.
Executable traceability: `tests/contracts/test_remediation.py`, the actual Make
failure probes in `tests/contracts/test_contracts.py`, and governed discovery and
listing fixtures. Runtime owners: T10/T11, conformance T20/T28 and CLI-03/10/11/14/15.
Renewed independent review and human approval remain pending.
