# CLI-16A — Compiled CLI/backend end-to-end acceptance

**Type:** verification. **Depends on:** CLI-15, T28, T34. One authorized task, one feature branch/PR; independent agent review then human merge authorization; stop before next task.

## Owned work
Compiled CLI/backend end-to-end acceptance. Implement the relevant acceptance matrix in `docs/acceptance/REQUIREMENTS.md`; compiled binary execution, real PTY/native hardware verification, or release archive reproducibility as appropriate. No features or provider API changes.

## Exit gate
Real recorded commands and outcomes, negative and failure-path evidence, contract compatibility; unresolved blockers escalated, never silently ignored.
