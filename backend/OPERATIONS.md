# Backend operational contract — local-only v1

See `../docs/decisions/FINAL_V1.md` for approved requirements; this document defines operator behavior.

## Deployment
Compose on one Linux/macOS host; API published exclusively at host `127.0.0.1`, Postgres/Qdrant/Ollama container interfaces restricted to private Compose network. Container Ollama default, validated host-local alternative. Flyway one-shot after DB readiness; backend serving only after compatible schema. Pin images by digest, persist Postgres and Qdrant volumes across containers and uninstalls. No LAN mode, remote client, TLS reverse proxy or multi-node setup in v1. Installation bundle includes deployment manifest, migration invocation, backend, CLI and service provisioning. Hardware detection is advisory; GPU execution must be tested; NPU not used by Ollama v1.

## Configuration/security
Backend Pydantic Settings validated before serving, explicit deployment secrets from protected files, never committed plaintext credentials. No global secret overrides or dynamic config database. PostgreSQL role separation `mimir_owner` NOLOGIN / migrator / api / worker / auth / maintenance with RLS verified through actual role sessions. No routine BYPASSRLS or production owner-role connections. Never log secrets or user content. API auth required on loopback.

## Operational behavior
Readiness requires PostgreSQL and compatible Flyway schema; liveness is process-only. Restricted dependency diagnostics expose Qdrant, Ollama, embedding version, queues. Qdrant outage degrades inference without memory with indicator; uncertain user isolation fails closed. Ollama outage disables inference while preserving archive reads. Structured redacted metadata logs; bounded local rotation, no prompt/response/memory bodies or bearer credentials in logs. Metrics for request/run/queue timings and counts; no UUID metric labels or mandatory Grafana/OTel collector. Graceful shutdown stops acceptance/claims, fences unfinished execution and leaves journals intact.

## Manual offline backup and restore
On-demand only: stop mutating requests, quiesce inference/ingestion, snapshot compatible PostgreSQL and provider-owned Mem0/Qdrant state with deployment versions, embedding identity, manifest and checksums; resume writers after validation. Restore in isolated/quiescent state; validate versions, role policy, revisions, vector compatibility and operation ledgers, then reapply independently durable **account-deletion journal** before any service exposure. No scheduled backups, automated retention, PITR or promised RPO/RTO in v1. No backup credential embedded in archive. Ordinary uninstall, upgrade, restart MUST NOT delete data volumes.

## Operator interface
Deployment-owned `make status`, `make doctor` (read-only), `make logs`, `make backup`, `make restore`; these are implementation targets, not commands already existing in the design ZIP. Mutating recovery requires explicit authorization, never blind replay for uncertain Mem0 effects. No public memory CRUD API.
