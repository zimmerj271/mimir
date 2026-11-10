# T04 — User, identity and session schema

**Milestone:** M1  
**Branch:** `feature/t04-auth-schema` (from updated `main`)  
**Dependencies:** T03  
**Status:** V002 implementation candidate and executable PostgreSQL tests included
on the T04 feature branch; independent review and human merge approval is complete.
Actual validation commands/results and the submitted SHA belong to the task PR.
The PR has also been completed and approved.

## Objective / authorized scope
User, auth identity/session tables, constraints, Argon2 storage contract and
restricted pre-authentication database lookup routines; SQL only. T04 supplies
`V002` after T03's applied `V001`; it does not modify migration history.

## Out of scope
Do not implement successor tasks, change shared contracts or introduce major dependencies without approval. No public per-memory CRUD, application-owned canonical memory tables, hidden direct client database/provider access, or future-web implementation.

## Additional normative Mimir integration requirement
Support absolute session expiration ceiling and last-use tracking for server-managed sliding idle extension; non-expiring credentials are not required.

## Human-approved implementation context

The human owner approved the following clarifications during the T04 assignment
and explicitly requested their inclusion in this brief and implementation.
The verified implementation base is merged T03 at
`b2a634622781fb474c4d4f0302ede8ff526da5fd`; refresh the base if `main` advances.

### Login comparison

- Login lookup and uniqueness use exact, case-sensitive comparison: `Alice` and
  `alice` are distinct logins. Do not lowercase, case-fold or otherwise normalize
  supplied usernames implicitly. Database uniqueness and lookup must agree.

### Pre-authentication access and tenant context

- Authentication must resolve an existing account before authenticated tenant
  context exists. Provide narrowly controlled internal database lookup routines,
  callable only by the backend authentication role, `mimir_auth`.
- The login lookup accepts an exact username and returns only the password
  verifier and account information needed by the trusted authentication service.
  Finding a username does not authenticate the caller or establish tenant context.
- The session lookup accepts the server-computed SHA-256 bearer-token hash and
  returns only the owner/session/account information needed to validate expiry,
  revocation and account status. Never accept or store a plaintext bearer token
  in these database interfaces.
- Routine execution permissions must not give the calling authentication role
  unrestricted identity/session table reads. Deny routine execution to PUBLIC
  and other runtime roles. Define explicit, narrowly scoped RLS policies and
  routine privileges for this pre-authentication boundary; `SECURITY DEFINER`
  alone does not bypass FORCE RLS. Preserve the existing role separation and
  prohibition on runtime ownership or BYPASSRLS.
- Use schema-qualified database objects, a fixed safe routine search path and
  parameterized lookup predicates. Caller-controlled settings or object names
  must not broaden the lookup or confer authenticated identity.
- After credential validation, trusted backend code establishes
  `app.current_user_id` separately in each application database transaction.
  That value must come from the validated identity, never an unverified
  client-supplied user ID. Transaction-local context ends at commit or rollback
  and must not leak across reused pooled connections. Lookup routines do not
  create accounts or leave authenticated tenant context behind.

### Task boundary

- T04 delivers the database tables, constraints, RLS/grants and lookup routines,
  plus real PostgreSQL/Flyway verification of those database behaviors.
- T08 retains administrator provisioning, Argon2id password verification, secure
  bearer generation, session issuance, authentication decisions, renewal and
  revocation service behavior. T09 retains HTTP routes and request guards.
  This clarification does not authorize implementing either successor task or
  changing public wire contracts.

## Required test scenarios and acceptance
Unique login and owner constraints verified on PostgreSQL; invalid FKs rejected. Add pertinent failure, auth, boundary and concurrency tests. Prefer deterministic unit tests where possible and real-service integration tests when persistence/provider guarantees are involved.

Include case-distinct login success and exact duplicate rejection, restricted
lookup success without tenant context, unknown lookup results, denied direct
credential-table reads, denied routine execution by other runtime roles, and
resistance to caller context/search-path manipulation. Verify that lookup does
not establish authenticated tenant context and that transaction-local tenant
scope clears on both commit and rollback. Exercise session lifetime constraints,
immutable absolute expiration and owner integrity, fresh and V001-to-V002
upgrades, concurrent uniqueness/updates, checksum validation and transactional
migration failure/recovery on PostgreSQL 18.

Acceptance coverage: `DB-01` and `DB-02` for the T04 schema subset; supporting
database coverage for `DB-ROLE-01`, `DB-MIG-01`, `SEC-01`, `API-SESSION-01` and
`AUTH-RENEW-01`. These references do not reassign acceptance ownership or claim
that later service, HTTP, full V001–V005 or release requirements are verified.

## Required engineering review
- Read `AGENTS.md`, `CODING_STANDARDS.md`, `ARCHITECTURE.md` and relevant domain documents before coding.
- Favor cohesive OOP services/models/adapters, composition and dependency injection. Do not introduce speculative frameworks or standalone business-logic functions.
- Formatting, linting, type checks, targeted tests and relevant integration checks must have **actual logged results**.
- Reviewer independently checks changed files, contracts, security, quality, tests and scope.
- No direct push to `main`; submit one PR from `feature/t04-auth-schema`, obtain user approval, then merge.
- Stop at this brief's boundary; do not start a dependent task automatically.

## PR evidence / completion template
- Branch and base commit; changed paths; concise design rationale.
- Acceptance cases → test files → exact commands → results or blockers.
- New or modified public interfaces, migrations, dependencies.
- Unresolved risks and architectural deviations (explicitly none if absent).
- Reviewer outcome and user approval (pending until granted).

## Agent hardening gate
Apply repository `AGENTS.md` and language `CODING_STANDARDS.md`. Before writing observable behavior, check `SPECIFICATION_GAPS.md` and the frozen `contracts/` artifacts. Stop for missing, contradictory, or unapproved interface/state semantics; do not infer them. Map relevant acceptance IDs and tests in `TRACEABILITY.md`, execute applicable `QUALITY_GATES.md` commands, and report literal results. This gate does not expand the authorized scope of this task.

### Blocking branch acceptance checkpoint
At completion, **STOP**. Open one task-scoped PR using the root `PR_REVIEW_TEMPLATE.md`. Require successful documented checks, independent coding-agent review, explicit human review and affirmative merge approval. Do not merge autonomously or begin the next task before merge confirmation and a fresh human assignment. See `AGENTS.md` and `QUALITY_GATES.md`.

**V1 normative references:** `CONTRACT_DECISIONS_V1.md`, `STATE_MACHINES.md`, `PROMPT_AND_MEMORY_CONTRACT.md`, `contracts/openapi.yaml`, `contracts/sse.schema.json`, and `contracts/errors.json` at unified-package root. These are consumed from the one shared `/contracts` directory in the monorepo; without separate copies.
