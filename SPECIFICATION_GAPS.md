# Final decisions resolved

All design gap decisions from review are in `docs/decisions/FINAL_V1.md`. This is not an invitation for implementing agents to invent missing public fields. Machine-readable contract and implementation tests must be validated in S01 and their assigned tasks. Prior proposed gap list is archived at `docs/appendices/HISTORICAL_SPECIFICATION_GAPS.md` as non-normative history.


The owner approved S01-AUTH (credential transports/browser controls), S01-CANCEL
(200/202 authoritative snapshots), and S01-PAGE (keysets/latest-active/history/
cursors) during S01. Their decisions are in
[FINAL_V1](docs/decisions/FINAL_V1.md) and
[HTTP semantics](contracts/HTTP_SEMANTICS.md). This records contract decisions,
not runtime implementation or passing producer/consumer conformance.

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
