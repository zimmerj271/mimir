> **V1 binding deployment:** Single monorepo; shared `/contracts`; single-host loopback-only. See `../docs/decisions/FINAL_V1.md`.

# AGENTS.md — Coding-agent operating contract (v1)

**Applies to every coding task.** Read in order: this file, `CODING_STANDARDS.md`, `ARCHITECTURE.md`, the **one assigned** `milestones/Txx_*.md`, then referenced domain specifications and acceptance tests. Future-work appendices are **non-normative** and must not be implemented unless separately authorized.

## Assignment execution
1. Work on **one task brief and one feature branch per assignment**, never an entire milestone or the entire product. Do not begin the next brief autonomously.
2. At the start, report intended files, classes/interfaces, tests, and assumptions. Inspect existing code before changing it.
3. Implement only the task's in-scope functionality. Do not introduce new dependencies, APIs, database tables, provider technologies, architecture patterns or significant refactors outside scope without explicit approval.
4. Prefer narrow, reviewable changes. Preserve existing interfaces and migration history. **Never modify an applied Flyway migration**; add a new one. Before first application, reconcile candidate baseline explicitly.
5. Use test-driven or test-alongside development. Test success **and** failure, security, concurrency, and recovery paths relevant to the task. Never weaken tests to make implementation pass.
6. Run formatting, lint, static type checks, targeted tests and specified integration checks. If a command cannot run, explicitly report why; do not claim success.
7. Open one scoped PR for the task, request independent diff and test review, and obtain explicit user approval **before merge**. Finish with a short review packet: changed files, design rationale, public interfaces, validation commands and literal results, risks, unresolved questions, and a proposed next task **without starting it**.
8. Stop and request architectural review for conflicts, untestable acceptance requirements, unsafe cross-user behavior, Mem0 incompatibility, or changes to approved contracts. Do not silently invent alternate architecture.

## Architectural non-negotiables
- Python/FastAPI/uv backend. Flyway explicit SQL owns migrations; SQLAlchemy for application persistence, not automatic production schema DDL.
- PostgreSQL owns conversation archive, users, branch revision snapshots and runtime state. **Mem0 owns managed memory**, behind `MemoryService`; no application-owned memory revisions/projection framework.
- Go/Bubble Tea CLI first; React/assistant-ui web later. Both use same canonical REST/OpenAPI + SSE backend. No direct client access to Ollama, Mem0, Qdrant or databases.
- Authenticated owner scope on every operation and worker; PostgreSQL RLS and composite owner integrity. Do not substitute client-provided IDs for authenticated identity.
- Ollama behind a replaceable inference-provider interface. No unauthorized hosted inference fallback.
- Message creation and run identity durable before inference. Streaming-first; transport-independent events; SSE initially. No automatic generation replay after visible output.
- PostgreSQL queued jobs with leases and fencing; uncertain Mem0 outcomes must not be blindly replayed. Memory ingestion serialized per user initially.
- Conversation deletion retains managed memories; only full user purge removes managed memory. No public per-memory list/edit/delete UI or API in v1.
- One backend API process initially with in-process event distribution and logical worker pools. Keep scalability seams, do not add a distributed broker/microservices.

## Code quality gates
Follow `CODING_STANDARDS.md`. Code must be typed, class-oriented, dependency-injected and composition-first. Avoid unstructured standalone functions and large god classes; do not overbuild generalized frameworks for one task. No silent exception swallowing, fake placeholder business logic, unbounded retries, hardcoded credentials or opaque dynamic imports.

## Handoff template
- Scope: [task ID and objective]
- Files changed: [paths]
- Interfaces/behaviors: [contract summary]
- Tests: [command → passed/failed/not run, with counts]
- Quality checks: [format/lint/types/migrations]
- Risks and assumptions: [explicit]
- Architectural deviations: [none or approval reference]
- Next proposed task: [ID, not started]

## Feature-branch governance (v1.2)
- The canonical list of 41 backend task IDs, dependencies, and branch names is `TASK_INDEX.md`. Treat a task brief as the only authorized coding scope.
- One task/one branch/one PR, with no direct main-branch commits. Never merge on the implementation agent's own assertion.
- The reviewer evaluates architecture, OOP design, test strength, security, dependency and migration changes, and actual command results; the user decides whether to merge.
- If the task cannot be completed in one reviewable PR, stop and propose a split before enlarging scope.
- Rebase on current `main` and rerun applicable checks if upstream changes land before merge.

## Independent CLI build
Backend tasks T20–T28 implement API/integration capabilities, not the Go CLI. The independent CLI-01–CLI-16 tasks live under `../cli/`. Use OpenAPI/SSE fixtures and fake providers for backend acceptance; never make Go UI work a prerequisite for backend PRs.

## Toolchain mandate (all Python tasks)
- Use **uv exclusively** for project virtual environment, dependency management, locking and invoking project tools. T01 creates `pyproject.toml`, commits `uv.lock`, declares `requires-python`, dependency groups (`dev`, `test`) and a `src/` package layout; pin interpreter and uv version in CI/toolchain notes. Do not use Poetry, Pipenv, `pip install`, `requirements.txt` as a second dependency authority, or global tool installs in routine development.
- Bootstrap `uv sync --locked --all-groups`; run project commands using `uv run --locked ...`. Change dependencies with `uv add` / `uv remove` (and appropriate `--group`), review `uv.lock` diff, rerun `uv lock --check`. CI must fail on a stale lock. A security update requires a scoped, reviewed dependency PR.
- Prescribe Ruff format+lint, **mypy** strict for owned first-party packages (document any narrow exception), `pytest` + `pytest-asyncio`, `pytest-cov`; no untyped external boundary. Python test suites use isolated Postgres/Flyway services for real DB semantics, with deterministic fake inference.
- Standard task checks after T01: `uv run --locked ruff format --check .`; `uv run --locked ruff check .`; `uv run --locked mypy src`; `uv run --locked pytest -q`; integration/migration gates per `../QUALITY_GATES.md`. If a command is impossible before bootstrap, report not run, never fabricate a pass.


## Mandatory feature-branch STOP / human-and-agent PR review checkpoint

1. **Start** only the assigned task from the latest merged `main` with its declared predecessors merged. Create one `feature/<task-id>-<slug>` branch; record base SHA, task ID and approved contract revision. Do not stack dependent work on an unmerged branch.
2. **Implement/test** only the assigned task; include relevant unit, integration, contract, negative/security and race/restart cases. Capture actual commands, versions, results and remaining risks; a failed/unrun gate must be disclosed, never represented as passed.
3. **STOP implementation** at the feature boundary. Open a PR with `PR_REVIEW_TEMPLATE.md` completed, scope/diff summary, contract/schema/dependency changes, test evidence, migration reversal/recovery guidance and review checklist. The implementation agent must not approve its own PR.
4. **Independent agent reviewer** reviews the full diff against the task, invariants, repository guardrails and contract; records findings and severity and verifies checks. The author addresses findings and obtains a new review on material changes.
5. **Human reviewer/owner** inspects the PR and evidence; explicit approval to merge is required **after** independent review. Silence, green CI, or agent approval alone is never consent. No bot auto-merge, direct main push, or self-merging on behalf of an unavailable human.
6. **Merge checkpoint** only an authorized human (or specifically delegated operator after affirmative human approval) merges. Record merge SHA and update task state/traceability. **Halt:** do not start or branch for the next task until merge is confirmed and the human assigns/authorizes subsequent work. If rejected, revise within the same branch and repeat review.

