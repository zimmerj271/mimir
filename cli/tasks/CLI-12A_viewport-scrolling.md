# CLI-12A — Viewport pagination, stable anchor and scrolling behavior

**Type:** feature · **Depends on:** CLI-09, CLI-11

## Scope and deliverables
Viewport pagination, stable anchor and scrolling behavior. Respect approved `docs/decisions/FINAL_V1.md` and component interfaces. S00 delivers root Makefile targets and protected governance/CI scaffolding; S01 checks and freezes the authoritative machine-readable contract, manifests, schema/error/fixture consistency and semantic version. DEPLOY-01 verifies complete local connected installation with PostgreSQL, Qdrant, Flyway, backend, Ollama and native CLI, without remote access. CLI-12A manages viewport anchors and scrolling independently of formatting. CLI-12B manages sanitized Markdown and bounded cache independently of fetching history.

## Acceptance and prohibited scope
Implement the matching requirements and fault-path tests in `docs/acceptance/REQUIREMENTS.md`. Do not invent public behavior, alter migration history, weaken gates, expand v1 scope or add unapproved external services. Required CI checks must be executed and evidenced. Separate independent agent PR review and human approval before merge; **STOP** afterward.
