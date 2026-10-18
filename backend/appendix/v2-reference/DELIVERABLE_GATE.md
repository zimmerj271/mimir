# Required Final Database Design Handoff

**Status:** Approved design requirement; not yet implemented or validated.

## Deliverables
- Consolidated, ordered Flyway versioned SQL baseline implementing the approved v0.2 design.
- Explicit SQL for constraints, triggers, RLS, grants, immutable revisions, canonical event sequencing, branch integrity, lifecycle guards, and applicable privileged routines.
- Executable PostgreSQL integration tests, including negative/abuse cases and concurrency tests.
- Reproducible Flyway migration/validation instructions, supported PostgreSQL version matrix, and results.
- Defect report plus explicit sign-off record confirming all release-blocking tests passed.

## Required gate
Implementation agents may use this package as the database implementation contract **only after** passing the PostgreSQL validation gate specified in `DESIGN_REFINEMENTS_V02.md` and `tests/VALIDATION_GATE.md`.

The v0.2 SQL drafts bundled here remain **unvalidated design drafts**. This v0.3 revision adds the deliverable requirement; it does not claim the final migrations or tests have been written or executed.
