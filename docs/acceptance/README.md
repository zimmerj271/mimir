# Executable traceability registry

[registry.json](registry.json) reconciles the IDs from
[REQUIREMENTS.md](REQUIREMENTS.md), [TRACEABILITY.md](../../TRACEABILITY.md),
and [backend acceptance scenarios](../../backend/ACCEPTANCE_TESTS.md).
`make governance-check` rejects missing/duplicate IDs, unknown owner tasks and
missing test files. `not_implemented` is an explicit gap, never a passing test.
`implemented` means executable tests exist; only command evidence at a specific
commit establishes that they passed. A null `last_verified_commit` makes no
commit-level verification claim. Review the S00 PR for its execution evidence.

The key is `(source, id)`: existing `SSE-01` and `MEM-01` through `MEM-03` appear
in both normative sources with distinct scope. Both assertions remain assigned;
S00 does not rename, replace or silently merge them. Legacy `CLI-16` ownership
references resolve to CLI-16A for E2E/integration and CLI-16B for performance,
following the approved split in the task graph. Range ownership is expanded.

The S00 GOV-01 unit suite checks structural enforcement and failure paths. Live
GitHub protection, independent review and human authorization additionally need
repository/PR evidence; a unit pass cannot establish those external facts.
All application requirements remain not implemented. Future owners must populate
tests and evidence before declaring their task ready/completed. S01 retains
ownership of wire semantics, manifests and contract checksums.
