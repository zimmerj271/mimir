# Mimir — approved v1 monorepo implementation specification

**Design artifact only.** This package is not executable software, does not claim SQL migrations or integration tests have run, and requires the specified independent agent and human PR review at **every** task boundary.

## Product scope
Mimir is a private local-first LLM and managed-memory assistant. V1 runs on **one Linux/macOS host**. The native Go CLI/TUI and Python backend are independently buildable **within one monorepo**. The backend is Compose-deployed with PostgreSQL 18, Qdrant, Flyway and Ollama (container by default; separately selected local host Ollama supported); Mem0 is a Python memory-provider integration. The API is host loopback only; no LAN clients, TLS reverse proxy, cloud features or multi-host topology in v1.

## Start here
1. `AGENTS.md` — binding coding-agent authority and PR stops.
2. `docs/decisions/FINAL_V1.md` — reconciled approved decisions and precise boundaries.
3. `docs/governance/TASK_GRAPH.json` and `docs/governance/IMPLEMENTATION_SEQUENCE.md` — owned task DAG.
4. `contracts/` — initial machine-readable design draft; S01 must validate and freeze it before dependent implementation.
5. `backend/ARCHITECTURE.md`, `backend/DATABASE_DESIGN.md`, `backend/API_CONTRACT.md`, `backend/OPERATIONS.md` and component task briefs.
6. `cli/ARCHITECTURE.md`, `cli/TASK_INDEX.md` and task briefs.
7. `docs/acceptance/REQUIREMENTS.md`, `TRACEABILITY.md`, `QUALITY_GATES.md`, and `docs/verification/REPORT.md`.

**Important:** executable Go/Python source, real V001–V005 migrations, deployment files, tests and Makefile targets are **implementation task deliverables**, not pre-built products inside this specification. No untested SQL is presented as an installed migration. Future v2 work is non-normative (`docs/appendices/`).
