# Mimir monorepo — authoritative agent instructions

**Status:** approved design handoff, not implemented application. One monorepo contains independently buildable Python backend, Go CLI, root contracts, deployment assets and integration tests. No repository splitting or cross-repository contract copies.

## Authority
1. Explicit human assignment and approved design decisions, security and deletion invariants.
2. Canonical approved `/contracts` OpenAPI/SSE/errors/fixtures when validated by S01; `docs/decisions/FINAL_V1.md` records reconciled requirements.
3. Component architectures, database design, API design and operating contract.
4. Assigned task brief, task graph and acceptance/quality specifications.
5. Idiomatic implementation discretion **only** within authorized scope.

If documents conflict, a public contract is incomplete, a security requirement is ambiguous, or a gate cannot pass: **STOP and escalate with evidence**. Do not invent API fields, bypass tenant scope, weaken tests, or modify protected artifacts. Prefer proven language-idiomatic patterns and composition; make extensions feasible through purposeful interfaces, not speculative frameworks.

## Paths and roles
- `backend/AGENTS.md`: uv-managed Python/FastAPI, SQLAlchemy 2.x, Flyway, Mem0 behind `MemoryService`.
- `cli/AGENTS.md`: Go modules, Cobra/Bubble Tea, credential and terminal safety.
- `contracts/`: one canonical backend-owned public wire contract, shared in monorepo. S01 establishes initial checksum and validation.
- `deployment/`: local-only Compose, images, secrets, migrations and install lifecycle.
- `docs/governance/`, `docs/acceptance/`: task ownership and evidence.

**Protected:** `/AGENTS.md`, component AGENTS, `/contracts/**`, approved architecture, Flyway history, CI enforcement, governance and requirement records. Modifying these requires explicit authorization; an ordinary feature task does not grant it. Applied SQL migrations are forward-only.

## Mandatory feature-branch STOP boundary
1. Human assigns **one** task. Verify its prerequisites have merged, approved specification revision, task scope and tests. Branch from latest `main`, record base SHA.
2. Implement only approved work, run required checks and report real output, missing tests and known limitations. Do not fabricate results or reduce gates to make CI green.
3. Submit one reviewable PR; **STOP**. A separate agent/session reviews actual diff and evidence (Critical/High block; Medium requires correction or explicit human acceptance; Low may be tracked).
4. Human reviews and explicitly authorizes merge; only an authorized human/delegate merges. Material changes require renewed agent/human review. No automatic merge.
5. Record merged SHA and **STOP again**. No next task without a fresh human assignment. Milestones require cumulative acceptance and human sign-off.

No agent may start, run or merge another task based solely on an earlier approval. Serial execution by default; independent parallel tasks need explicit human authorization and disjoint edit ownership. No secrets, prompts, bearer tokens or personal memory content in logs, artifacts or commits.
