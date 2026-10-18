> **V1 binding deployment:** Single monorepo; shared `/contracts`; single-host loopback-only. See `../docs/decisions/FINAL_V1.md`.

# Mimir CLI — Coding agent operating contract

Applies to every CLI-01–CLI-16 assignment in the **separate Go repository**. Read root handoff `../AGENTS.md` if available, then this file, `CODING_STANDARDS.md`, `ARCHITECTURE.md`, assigned `tasks/CLI-xx_*.md`, shared approved contracts and corresponding acceptance cases. Historical design notes are not authorization to add scope.

## Required Go project system
- **Use native Go modules**; there is no Go equivalent to `uv` needed. CLI-01 initializes one `go.mod` at repository root with approved, real module import path (do **not** invent an organization/URL), declares supported minimum `go` version and a pinned `toolchain` where needed. Commit `go.mod` and `go.sum`, verify `go mod tidy` leaves no unexpected diff. Do not use GOPATH mode, vendoring, or `replace` directives pointing to developer-local directories. Avoid a `go.work` workspace for this one-module repo.
- Choose and record a tested, currently supported stable Go release at CLI-01, not a guessed version in design docs. CI must use the same selected toolchain. Review every new runtime dependency for maintenance, license, security, binary size, platform support and compatibility. Prefer the standard library; approved baseline includes Cobra, Bubble Tea and task-authorized Charm components.
- No Java/Python-style OOP mandate in Go. Follow idiomatic Go: small packages and interfaces defined by consumers; explicit constructors/dependencies; composition, not class emulation; errors wrapped with `%w`, inspected via `errors.Is/As`; `context.Context` first when relevant; mutex/ownership discipline; no `panic` for user/network failures; no hidden global mutable application state. See `CODING_STANDARDS.md`.

## Commands and evidence
- Required checks (after CLI-01): `gofmt -l` must return no `.go` files; `go vet ./...`; `go test ./...`; `go test -race ./...` on supported runners; `go build ./cmd/mimir`; `go mod tidy` with clean `go.mod`/`go.sum` diff. Record results; never report a green gate without execution. Additional `golangci-lint` or `govulncheck` only if pinned/configured in CLI-01; do not download floating latest binaries during CI.
- Test interactive behavior with PTYs, input golden cases, and deterministic fake REST/SSE backend; ensure stdout/stderr separation, terminal restoration after failure, no residual raw-mode state and no secret leakage. Linux/macOS required; Windows deferred.


## PR handoff
Task ID and base commit; changed paths; public contract changes; dependency diff; tests/checks with exact command output; security/terminal checks; risks/unanswered decisions; reviewer result; user approval status; next **proposed** task only.

## Mandatory feature-branch STOP / human-and-agent PR review checkpoint

1. **Start** only the assigned task from the latest merged `main` with its declared predecessors merged. Create one `feature/<task-id>-<slug>` branch; record base SHA, task ID and approved contract revision. Do not stack dependent work on an unmerged branch.
2. **Implement/test** only the assigned task; include relevant unit, integration, contract, negative/security and race/restart cases. Capture actual commands, versions, results and remaining risks; a failed/unrun gate must be disclosed, never represented as passed.
3. **STOP implementation** at the feature boundary. Open a PR with `PR_REVIEW_TEMPLATE.md` completed, scope/diff summary, contract/schema/dependency changes, test evidence, migration reversal/recovery guidance and review checklist. The implementation agent must not approve its own PR.
4. **Independent agent reviewer** reviews the full diff against the task, invariants, repository guardrails and contract; records findings and severity and verifies checks. The author addresses findings and obtains a new review on material changes.
5. **Human reviewer/owner** inspects the PR and evidence; explicit approval to merge is required **after** independent review. Silence, green CI, or agent approval alone is never consent. No bot auto-merge, direct main push, or self-merging on behalf of an unavailable human.
6. **Merge checkpoint** only an authorized human (or specifically delegated operator after affirmative human approval) merges. Record merge SHA and update task state/traceability. **Halt:** do not start or branch for the next task until merge is confirmed and the human assigns/authorizes subsequent work. If rejected, revise within the same branch and repeat review.

