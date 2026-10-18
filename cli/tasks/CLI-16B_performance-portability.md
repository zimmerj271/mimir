# CLI-16B — Terminal/PTY performance and Linux/macOS platform conformance

**Type:** verification. **Depends on:** CLI-12B, CLI-14, CLI-15. One authorized task, one feature branch/PR; independent agent review then human merge authorization; stop before next task.

## Owned work
Terminal/PTY performance and Linux/macOS platform conformance. Implement the relevant acceptance matrix in `docs/acceptance/REQUIREMENTS.md`; compiled binary execution, real PTY/native hardware verification, or release archive reproducibility as appropriate. No features or provider API changes.

## Exit gate
Real recorded commands and outcomes, negative and failure-path evidence, contract compatibility; unresolved blockers escalated, never silently ignored.
