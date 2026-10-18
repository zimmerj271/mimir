# Backend task index — approved v1

Monorepo. Canonical dependency source: `../docs/governance/TASK_GRAPH.json`. **One human-authorized task → one branch → CI evidence → independent agent review → human approval and authorized merge → STOP until reassignment.** Tests and SQL described are not claimed executed. S01 must merge before public wire implementation.

| Task | Type | Owner/Scope | Prerequisites |
|---|---|---|---|
| [T01](milestones/T01_python-toolchain.md) | feature | Python project and quality toolchain | S00 |
| [T02](milestones/T02_compose-foundation.md) | feature | Compose and service isolation | T01 |
| [T03](milestones/T03_flyway-roles.md) | feature | Flyway bootstrap and database roles | T02 |
| [T04](milestones/T04_auth-schema.md) | feature | User, identity and session schema | T03 |
| [T05](milestones/T05_archive-core-schema.md) | feature | Conversation and project schema | T04 |
| [T06](milestones/T06_branch-schema.md) | feature | Immutable branch snapshot schema | T05 |
| [T07](milestones/T07_runtime-schema.md) | feature | Runs jobs outbox and idempotency schema | T06 |
| [T08](milestones/T08_auth-service.md) | feature | Local provisioning and session service | T04 |
| [T09](milestones/T09_auth-http.md) | feature | Auth HTTP endpoints and session guards | T08, S01 |
| [T10](milestones/T10_conversation-domain.md) | feature | Conversation/project services and repositories | T05, T06 |
| [T11](milestones/T11_conversation-http.md) | feature | Conversation/project REST resources | T09, T10, S01 |
| [T12](milestones/T12_message-submit.md) | feature | Atomic message submission and idempotency | T07, T10, T11 |
| [T13](milestones/T13_inference-contract.md) | feature | Inference provider protocol and deterministic fake | T01 |
| [T14](milestones/T14_context-assembler.md) | feature | Pure working-context assembler | T10, T13 |
| [T15](milestones/T15_ollama-adapter.md) | feature | Ollama provider implementation | T02, T13 |
| [T16](milestones/T16_run-executor.md) | feature | Durable run execution and checkpoints | T07, T12, T13, T14 |
| [T17](milestones/T17_event-bus.md) | feature | In-process event publisher and bounded subscriptions | T13 |
| [T18](milestones/T18_sse-delivery.md) | feature | SSE run stream snapshot handoff | T16, T17, S01 |
| [T19](milestones/T19_run-control.md) | feature | Run status cancellation and interruption endpoints | T16, T18, S01 |
| [T20](milestones/T20_shared-api-schema.md) | verification | Server conformance to already approved /contracts, no schema invention | S01, T09, T11, T18, T19 |
| [T21](milestones/T21_standalone-run-domain.md) | feature | Standalone durable inference domain | T07, T13, T14, T16 |
| [T22](milestones/T22_standalone-run-http.md) | feature | Standalone inference HTTP submission | T20, T21, T12, S01 |
| [T23](milestones/T23_operation-recovery.md) | feature | Accepted-operation lookup and recovery | T07, T12, T20, S01 |
| [T24](milestones/T24_silent-session-renewal.md) | verification | session renewal conformance (no duplicate auth implementation) | T04, T08, T09, T20 |
| [T25](milestones/T25_conversation-project-reassignment.md) | verification | project reassignment conformance (T10/T11 implement) | T10, T11, T20 |
| [T26](milestones/T26_conversation-creation-and-selection.md) | verification | idempotent conversation creation conformance (T11 implements) | T10, T11, T12, T20 |
| [T27](milestones/T27_run-recovery-contract.md) | verification | run snapshot/status/cancellation conformance (T18/T19 implement) | T18, T19, T20, T21, T22 |
| [T28](milestones/T28_backend-client-contract-gate.md) | verification | backend API/consumer protocol verification | T20, T22, T23, T24, T25, T26, T27 |
| [T29](milestones/T29_mem0-spike.md) | spike | Pinned Mem0 OSS compatibility spike | T02, T13 |
| [T30](milestones/T30_memory-port.md) | feature | Minimal memory service protocol and fake | T13 |
| [T31](milestones/T31_mem0-adapter.md) | feature | Mem0 adapter with tenant protection | T29, T30 |
| [T32](milestones/T32_job-queue.md) | feature | PostgreSQL jobs and lease claims | T07 |
| [T33](milestones/T33_memory-ingestion.md) | feature | Asynchronous committed-turn memory ingest | T12, T31, T32 |
| [T34](milestones/T34_memory-read.md) | feature | Orchestrator memory search and degraded mode | T14, T16, T31, T30 |
| [T35](milestones/T35_account-purge.md) | feature | Full user purge and deletion fences | T08, T31, T32 |
| [T36](milestones/T36_archive-deletion.md) | feature | Conversation/archive deletion semantics | T10, T33 |
| [T37](milestones/T37_backups.md) | feature | Encrypted backups and restoration runbook | T02, T31, T35 |
| [T38](milestones/T38_telemetry.md) | feature | Structured logs, traces and queue diagnostics | T19, T32 |
| [T39](milestones/T39_operational-hardening.md) | feature | Restart, shutdown and failure handling | T16, T32, T36, T37 |
| [T40](milestones/T40_integration-review.md) | feature | Security and full-workflow review | T28, T34, T35, T36, T37, T38, T39 |
| [T41](milestones/T41_release-gate.md) | feature | Release documentation and evidence | T40 |
