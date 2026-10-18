# Cli task index — approved v1

Monorepo. Canonical dependency source: `../docs/governance/TASK_GRAPH.json`. **One human-authorized task → one branch → CI evidence → independent agent review → human approval and authorized merge → STOP until reassignment.** Tests and SQL described are not claimed executed. S01 must merge before public wire implementation.

| Task | Type | Owner/Scope | Prerequisites |
|---|---|---|---|
| [CLI-01](tasks/CLI-01_go-executable-and-quality-baseline.md) | feature | Go executable and quality baseline | S00 |
| [CLI-02](tasks/CLI-02_configuration,-paths-and-local-state-primitives.md) | feature | Configuration, paths and local state primitives | CLI-01 |
| [CLI-03](tasks/CLI-03_typed-rest-and-sse-transport.md) | feature | Typed REST and SSE transport | CLI-01, S01 |
| [CLI-04](tasks/CLI-04_authentication-and-persisted-sessions.md) | feature | Authentication and persisted sessions | CLI-02, CLI-03 |
| [CLI-05](tasks/CLI-05_project-and-conversation-resource-commands.md) | feature | Project and conversation resource commands | CLI-03, CLI-04 |
| [CLI-06](tasks/CLI-06_sticky-context-selection-and-resolution.md) | feature | Sticky context selection and resolution | CLI-02, CLI-05 |
| [CLI-07](tasks/CLI-07_recovery-journal-and-run-inspection.md) | feature | Recovery journal and run inspection | CLI-02, CLI-03, CLI-04 |
| [CLI-08](tasks/CLI-08_one-shot-inference-and-complete-history-export.md) | feature | One-shot inference and complete history export | CLI-03, CLI-04, CLI-05, CLI-06, CLI-07 |
| [CLI-09](tasks/CLI-09_bubble-tea-shell-and-terminal-lifecycle.md) | feature | Bubble Tea shell and terminal lifecycle | CLI-01, CLI-02 |
| [CLI-10](tasks/CLI-10_composer-and-resource-selectors.md) | feature | Composer and resource selectors | CLI-05, CLI-09 |
| [CLI-11](tasks/CLI-11_paged-canonical-message-store.md) | feature | Paged canonical message store | CLI-03, CLI-09 |
| [CLI-13](tasks/CLI-13_semantic-themes-and-appearance-controls.md) | feature | Semantic themes and appearance controls | CLI-02, CLI-09, CLI-12B |
| [CLI-14](tasks/CLI-14_run-streaming-and-tui-presentation-state-machine.md) | feature | Run streaming and TUI presentation state machine | CLI-03, CLI-09, CLI-10, CLI-11, CLI-12B |
| [CLI-15](tasks/CLI-15_cancellation,-conflicts-and-recovery-ux.md) | feature | Cancellation, conflicts and recovery UX | CLI-04, CLI-07, CLI-10, CLI-14 |
| [CLI-12A](tasks/CLI-12A_viewport-scrolling.md) | feature | Viewport pagination, stable anchor and scrolling behavior | CLI-09, CLI-11 |
| [CLI-12B](tasks/CLI-12B_markdown-render-cache.md) | feature | Markdown rendering, width/theme cache, bounded visual window | CLI-12A |
| [CLI-16A](tasks/CLI-16A_e2e.md) | verification | Compiled CLI/backend end-to-end acceptance | CLI-15, T28, T34 |
| [CLI-16B](tasks/CLI-16B_performance-portability.md) | verification | Terminal/PTY performance and Linux/macOS platform conformance | CLI-12B, CLI-14, CLI-15 |
| [CLI-16C](tasks/CLI-16C_packaging.md) | release | Go CLI binary archives, checksums, native platform installation | CLI-16A, CLI-16B |
