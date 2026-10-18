# Specification closure ledger — no open product-default questions

The previous G01–G15 list has been dispositioned with explicit v1 decisions in `CONTRACT_DECISIONS_V1.md` and normalized transition/prompt contracts. **No agent is permitted to invent alternative behavior.** These choices constitute the proposed normative freeze baseline pending human review of this revised ZIP; the task/branch approval process applies to changes.

| IDs | Closure location | Remaining verification (implementation, not design invention) |
|---|---|---|
| G01, G11 | `CONTRACT_DECISIONS_V1.md`, DB migration design | Execute Flyway/PG migration, RLS and restore tests; SQL draft is not deployable |
| G02, G09 | `contracts/openapi.yaml`, `contracts/sse.schema.json`, `contracts/errors.json`, fixtures | Validate against actual FastAPI and Go client code |
| G03, G14 | Task indexes, ownership rule and mandatory review protocol | Confirm source/package ownership in reviewed bootstrap PRs |
| G04–G07, G10, G15 | `CONTRACT_DECISIONS_V1.md` | Confirm host-specific module path and pinned versions at bootstrap PR |
| G08 | `STATE_MACHINES.md` | Enforce transitions via tests/transactions |
| G12 | `PROMPT_AND_MEMORY_CONTRACT.md` | Check provider adapter mapping by integration/evals |
| G13 | `TRACEABILITY.md`, `RELEASE_VALIDATION.md` | Replace planned test mappings with executable paths in implementation |

**Explicit status:** design decisions specified does **not** mean the complete code-facing contract has been independently validated or the SQL migrated. The package remains **design-frozen candidate, not runtime-proven** until the release validation gates pass. Human review is required before a coding agent implements any unresolved difference between prose and schema.
