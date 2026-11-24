> **v1.2 feature-branch governance:** 41 reviewable **backend** tasks, including nine backend client-integration tasks, each with a named branch, PR, individual acceptance tests and required independent review/user merge approval. See `TASK_INDEX.md` and `AGENTS.md`.

# Mimir Server — Backend v1 authoritative design package

## Local provisioning and sessions (T08)

T08 adds internal authentication/provisioning services and the owner-authorized
forward-only `V006__restricted_auth_operations.sql`, based on merged main
`392943d1735b13916601d1807a12013a53807e99` (T04 dependency merged in PR #5).
V001–V005 and the draft public contracts are unchanged. HTTP routes, bearer/cookie
extraction, CSRF, origin guards and application startup remain T09/later work.

`AuthService` receives a repository, `PasswordService`, `SessionTokens` and clock.
`PostgresAuthRepository` uses SQLAlchemy 2.x asynchronous Core calls to the
restricted routines, rather than writable ORM mappings. Construct `AuthDatabase`
with a `sqlalchemy.URL` using `postgresql+psycopg` and `mimir_auth`; construct a
separate pool with `mimir_maintenance` for `ProvisioningService`. The composition
root must close both pools. Never inject migration/owner credentials into these
services. Database exceptions are normalized without SQL parameters, and secret
dataclass fields are excluded from repr; callers must never log password/token
arguments or enable SQL/driver parameter logging.

Passwords and exact case-sensitive usernames are never trimmed or normalized.
New accounts use UUIDv7 IDs and Argon2id v19 with 64 MiB memory, three iterations,
four lanes, 16-byte random salts and 32-byte hashes. The password work cap defaults
to two native worker operations, remains held during cancellation, and runs off
the event loop. Verification rejects malformed PHC strings, other algorithms,
parameters below 64 MiB/three iterations or above 256 MiB/ten iterations/eight
lanes, and salt/hash lengths outside 16–64 bytes. Empty passwords, invalid UTF-8
and passwords above 1 MiB UTF-8 are rejected without truncation. This is an
internal resource policy; no wire schema is changed. HTTP admission/rate limiting
is not implemented here. Unknown usernames perform dummy verification; wrong
passwords, disabled accounts and invalid session states share a generic rejection.
The composition root should share one `PasswordService` per process so its work
cap applies across requests; it is not a per-request dependency.

Each login issues an independent 256-bit opaque bearer; only its SHA-256 digest
crosses the database boundary. Sessions start with 30 days idle and an immutable
90-day absolute ceiling. Successful activity revalidates state under account and
session locks, advances last use/idle expiration monotonically within the ceiling,
and preserves the bearer for concurrent CLI instances. Expired or revoked sessions
cannot be revived, including expiry while waiting for a lock. Logout is idempotent
for well-formed tokens and revokes only that session. Administrator disablement
atomically disables the account and revokes all sessions. There is no re-enable,
password-change, refresh-token or purge operation in T08. Services return a
validated `Session`; T09/application persistence must derive transaction-local
actor context from that principal, never a client owner ID.

The local administrator command runs in a backend Python environment on the
deployment's private Compose network, with a separately mounted maintenance
credential file. It does not expose a registration route or require publishing a
database port. An operator supplies the existing `mimir_maintenance` secret as an
owned regular file with no group/other permissions (normally 0600). Symlinks and
unsafe files are rejected; account passwords are prompted twice on a non-echoing
terminal and are never command arguments or environment variables:

```sh
uv run --locked python -m mimir_backend.provision \
  --credential-file /run/secrets/mimir_maintenance_password \
  create-user --username Alice --display-name Alice
uv run --locked python -m mimir_backend.provision \
  --credential-file /run/secrets/mimir_maintenance_password \
  disable-user --user-id 'UUID_PRINTED_BY_CREATE_USER'
```

The command defaults to the private `postgres:5432` service and `mimir` database;
host/port/database flags are administrator connection settings, not CLI server
profiles. Deployment mounting/installer wiring and full application serving are
outside T08. No production account or volume is changed by the tests.

Runtime dependencies are exact-pinned in `pyproject.toml` and hashed in `uv.lock`:
SQLAlchemy 2.0.58, psycopg/binary 3.3.9 and argon2-cffi 25.1.0. Initial connected
sync contacts PyPI for them and their locked transitives. After locked sync, the
offline wheel/sdist test exports runtime artifacts from `uv.lock` to a temporary
`pylock.toml`, uses uv to install those locked artifacts and the built wheel in a
separate virtual environment, checks installed dependency compatibility and imports
the installed auth modules outside the checkout. No registry re-resolution or
additional dependency authority is needed.

`backend/tests/test_auth.py` covers deterministic policy, crypto, rejection,
bounded/cancelled password work, redaction and administrator secret handling.
`tests/integration/test_database.py::test_t08_*` exercises real services and
PostgreSQL 18/Flyway, including restricted-role denials, concurrent provision and
renewal, disable/revoke/expiry races, pooled context cleanup, admin commands,
fresh/repeat/restart and V005 upgrade/failure/recovery/drift. The real-engine
tests require a Linux Docker bridge reachable from the test host; they use a
unique disposable stack's private address without publishing PostgreSQL.
Run `make backend-check` and `make database-check`; literal results and the exact
review SHA belong to the T08 PR. See [traceability](../TRACEABILITY.md) and
[migration recovery](migrations/README.md). Native macOS real-engine support,
HTTP/CSRF and end-to-end client acceptance remain unverified by T08.

## Python package and quality toolchain (T01)

T01 supplies an independently installable `src/mimir_backend` package and quality
tools. It supplies no HTTP app, database connectivity, inference, memory provider,
migrations or runtime commands. The empty typed package is intentional.

Prerequisite S00 merged as `b5824cb12cdf70443634c0df7a183951f933cf03`; T01 branches
from that SHA. The canonical task graph governs prerequisites where older task
brief prose says "None". The approved design revision is FINAL_V1.md at that base.

Use **uv 0.12.10** and **CPython 3.14.7**, pinned by `tool.uv.required-version`,
`.python-version` and CI. `requires-python` restricts this initial toolchain to
Python 3.14. Interpreter compatibility with future provider dependencies remains
the responsibility of their assigned tasks, not a T01 support claim.

From `backend/`, run:

```sh
uv sync --locked --all-groups
uv lock --check
uv run --locked ruff format --check .
uv run --locked ruff check .
uv run --locked mypy src
uv run --locked pytest -q
uv build --offline
```

From the repository root, `make backend-check` runs locked sync, lock validation,
formatting, lint, strict typing and tests; `make check` retains root governance
validation. Both run on Linux/macOS in the existing required foundation CI jobs.
No backend path filter skips these checks. CI setup actions and tool releases are
pinned; neither cached environments nor a stale lock can substitute for sync.

Initial bootstrap explicitly contacts PyPI/files.pythonhosted.org for locked
development/test tools and the pinned uv build backend. uv may obtain the pinned
interpreter if absent; CI obtains it using setup-python. T01 introduced no runtime
dependencies; T08's additions are listed above. After bootstrap, reproduction, lock-rejection and wheel/sdist
tests use `--offline` and disable interpreter downloads. A truly empty cache
needs the initial connected sync; offline tests do not claim otherwise.

Ruff 0.17.1, mypy 2.4.0 and uv-build 0.12.10 are in the `dev` group; pytest 9.1.1,
pytest-asyncio 1.4.0 and pytest-cov 7.1.0 are in `test`. All direct tool versions
are exact pins, transitives have hashes in `uv.lock`, and the PEP 517 backend is
`uv_build==0.12.10`. Use `uv add`/`uv remove` for reviewed dependency updates;
never edit the lock by hand or introduce a second dependency authority.

Pytest uses strict config/markers, isolated import mode, warnings as errors and
function-scoped async loops. Coverage is configured and emits `coverage.xml`;
the T01 package had **zero executable statements**, so its reported 100%
was an empty-package measurement, not application coverage. No arbitrary coverage
threshold is invented by this foundation task. Strict mypy has no exceptions.

Acceptance coverage: GOV-01 supporting quality/evidence controls, recorded in
[TRACEABILITY.md](../TRACEABILITY.md) and the
[acceptance registry](../docs/acceptance/registry.json). Tests verify installation
outside the checkout, async cancellation/cleanup, fresh/repeated locked sync,
missing-lock and wrong-uv rejection on the full backend project, stale-lock
rejection on a dependency-free fixture with a successful unlocked control,
wheel/sdist contents, rebuilding from
the sdist through PEP 517 with the locked dev build backend (no build isolation
or network), and importing the resulting wheel without the source checkout. They
test tooling guarantees; they establish no application auth/concurrency behavior.

Contract semantics and producer/consumer validation remain assigned to S01 and
later owners. PostgreSQL/Flyway, RLS, provider, auth, E2E and native CLI tests are
not implemented by T01. No shared contract or approved architecture is changed.

Independent agent review and explicit human approval/merge authorization remain
pending until recorded on the PR. To roll back T01, use a reviewed revert PR;
there is no runtime data or migration rollback. No successor task is started.

**Design baseline:** 2026-10-16 · **Status:** architecture approved, implementation and integration validation pending.

This package supersedes `local-llm-design-v0.4` and the database design drafts v0.1–v0.3 for **v1 implementation**. `ARCHITECTURE.md` is normative for approved architecture; `DATABASE_DESIGN.md`, `API_CONTRACT.md`, `IMPLEMENTATION_PLAN.md`, `ACCEPTANCE_TESTS.md`, and `OPERATIONS.md` refine it. Where a proposed implementation detail is not yet validated, it is identified explicitly.

## Reading order
1. `ARCHITECTURE.md` — approved decisions and scope, with future-work appendix at the end
2. `DATABASE_DESIGN.md` — v1 relational domain and Flyway implementation requirements
3. `API_CONTRACT.md` — REST/SSE, concurrency, authentication and failure semantics
4. `IMPLEMENTATION_PLAN.md` — milestones and agent assignment boundaries
5. `ACCEPTANCE_TESTS.md` — executable test targets and release gates
6. `OPERATIONS.md` — deployment, runtime, backup, recovery and observability
7. `migrations/README.md` — candidate SQL baseline status
8. `appendix/v2-reference/` — archived obsolete advanced memory-engine designs, **non-normative**

## Artifact/validation honesty
A v1 candidate Flyway SQL baseline is included for agent review and execution. **It has not been run against PostgreSQL/Flyway in this creation step**; do not treat its constraints as verified. Full executable integration tests are specified but **not yet implemented or passed**. M1 is responsible for finishing migration constraints, implementing tests, validating real PostgreSQL and providing a report. Do not deploy the SQL based solely on this package.

## Shared monorepo contract
The canonical Python backend API serves both a Go/Bubble Tea CLI (first) and TypeScript/React assistant-ui web frontend (later). This package specifies backend behavior; the independent implementation-ready Mimir CLI/TUI package is in `../cli/`.

## Independent flow
This folder contains the **backend build only**, with its original v1.2 architecture, documents, 41 tasks, and candidate SQL updated for newly approved CLI-facing backend requirements. The Go CLI/TUI is designed and planned separately in `../cli/`. Do not treat T20–T28 as Go tasks.
