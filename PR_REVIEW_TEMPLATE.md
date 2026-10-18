# Feature PR gate and evidence template

**Every implementation task uses this template; no checkboxes may be silently omitted.**

- Task ID / assigned scope / related acceptance IDs:
- Branch / base `main` SHA / contract revision + checksum:
- Changed files; excluded work; dependency/security review:
- Behavior changes (including defaults, failure paths, user-visible interface):
- Schema/migration/restore implications and exact rollback procedure:
- Unit/integration/e2e/contract tests: command + exact outcome + environment:
- Threat-model checks (tenant isolation, prompt injection, credentials, logs):
- Static analysis, formatting, dependency/license/vulnerability results:
- Independent agent reviewer, date, review SHA, findings and dispositions:
- Human reviewer, date, affirmative approval and authorized merger:
- Actual merge commit SHA, or **NOT MERGED / STOPPED**:
- Next assignment: **NONE until human explicitly authorizes after merge**.

**Blocking review findings:** contract mismatch, uncontrolled external behavior, missing required tests, non-reproducible migrations, security flaw, failure to meet task exit criteria, or unapproved scope. Never bypass required gates by calling a spike a feature branch.
