# Reproducible implementation quality and release gates

**Design only:** Commands are expected deliverables of bootstrap tasks; they have **not** been executed successfully against application code in this design package.

## Per-PR mandatory checks
1. Scope/branch/PR and reviewed dependency diff; `AGENTS.md` precedence and acceptance case references.
2. Backend: `uv sync --locked --all-groups`; `uv run --locked ruff format --check .`; `uv run --locked ruff check .`; `uv run --locked mypy src`; `uv run --locked pytest -q` (targeted and full as task requires). Database tasks must test Flyway migrate/validate on fresh **and upgrade-path** PostgreSQL instances, RLS on/off failure tests and rollback/recovery exercise. Pin dependencies before running.
3. CLI: `go vet ./...`; `go test ./...`; `go test -race ./...`; `go build ./cmd/mimir`; zero `gofmt -l` output; clean `go mod tidy` diff; PTY and fake REST/SSE integration tests for applicable changes.
4. Contract: schema validation + backend golden response/event producer tests + generated client/consumer compatibility; contract diff reviewed before merge. Doc links/IDs and task-to-acceptance matrix checked in CI.
5. Security: no committed secrets or sample user data; ownership/RLS, injection and auth tests on changed boundaries. No undisclosed dependency network access.

## Build and release requirements
- CI matrix on Linux/macOS CLI runners; database/Flyway integration against supported PostgreSQL. Full E2E with a fake model plus opt-in local model smoke test; deterministic fixtures remain gateable offline.
- Real backup restore drill, deletion restoration fences, crash/restart under active run, recovery after retained/expired op, no silent retransmission; evaluate privacy and memory regression suite `EVALS.md`.
- Produce a provenance manifest: Git SHAs, interpreter/Go/compiler versions, dependency locks, migration checksums, contract revision, command log, test report, open defects, risk acceptance, and reviewer/user approval.
- Do not declare release-ready if required gates are skipped or contract decisions unresolved. Clearly mark gates blocked/not-run rather than passed.

## V1 resolution and review authority
The selected v1 behavior baseline is `CONTRACT_DECISIONS_V1.md`, with lifecycle rules in `STATE_MACHINES.md`, context format in `PROMPT_AND_MEMORY_CONTRACT.md`, and wire schemas in `contracts/`. Older wording describing G01–G15 as unanswered or suggesting T20 may choose public payload shapes is superseded. Every feature PR must pass the **mandatory stop / independent-agent + human review** in `PR_REVIEW_TEMPLATE.md`; no subsequent branch work before authorized merge and reassignment.
