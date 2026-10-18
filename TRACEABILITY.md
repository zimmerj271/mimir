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
