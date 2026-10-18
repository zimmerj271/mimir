# S00 bootstrap operations

Acceptance IDs: GOV-01 (source: docs/acceptance/REQUIREMENTS.md).

Base main: `6311100d9f084c4dc682e078cce8a5d8dfe7d22a`.
Approved specification: `docs/decisions/FINAL_V1.md` at that base.
S00 has no prerequisites. Contract revision remains the existing **draft** 1.0.0;
S00 does not validate/freeze its manifest or approve public behavior.

## Local checks and task boundaries

Run `make check` from the repository root with Python 3.11+ and Make. Root
governance tools use only the Python standard library and never install packages,
fetch URLs or run application services. CI pins Python 3.14.7 on Ubuntu 24.04 and
macOS 15; those root checks are not native CLI support evidence. CI action commits
are pinned and credentials are not persisted in the checkout.

`make backend-check`, `make cli-check` and `make contract-check` fail explicitly
until their owning tasks implement prerequisites. They are not S00 passing gates.
T01 owns uv/locks/backend quality CI; CLI-01 owns Go modules/toolchains/native
build CI; S01 owns contract checks. Component jobs must become required as their
implementations land. Database/Flyway, PTY, E2E and real dependency gates remain
assigned to their documented tasks. Root checks do not replace those gates.

The offline checker verifies task DAG references and cycles, Markdown local file
links (not network links or heading anchors), the source-qualified acceptance
registry, and narrow tracked credential/path patterns. It does not claim a full
secret scan, vulnerability audit, semantic contract validation or completion of
every MUST/SHALL scenario. No application tests or readiness claims are created.

## GitHub enforcement

[main-protection.json](main-protection.json) is the reviewed desired API payload.
An administrator applies it using `gh api --method PUT
repos/zimmerj271/mimir/branches/main/protection --input
docs/governance/main-protection.json`. Read it back using the same endpoint with
GET. Updating this file does not itself change GitHub settings.
Save the GET response to a local file and run `python3 scripts/check_protection.py
/path/to/response.json` to compare enforcement. That command reads local JSON;
it does not require credentials or mutate repository settings.

Required checks are `foundation (ubuntu-24.04)` and `foundation (macos-15)` from
GitHub Actions (app ID 15368). Protection requires an up-to-date branch, one
approving review, dismissed stale reviews, approval after the latest push,
resolved conversations, linear history, no force pushes/deletions and no admin
bypass. Auto-merge is off. GitHub personal repositories do not support user/team
push restrictions (the API rejects those with HTTP 422), so merger identity is
a procedural human authorization obligation rather than a push allowlist.

CODEOWNERS assigns the owner all paths, including AGENTS, contracts, architecture,
migrations, requirements, governance and CI. All paths receive the same strict
branch review gate. Required code-owner approval is disabled because the sole
owner may also author the PR and cannot approve their own PR. An eligible second
reviewer with write access must provide the required GitHub approval; independent
agent review must be recorded against the actual reviewed SHA. The human owner
then explicitly authorizes and performs/delegates merge. An implementation agent
must not create approval evidence or merge its own PR.

GitHub cannot prove that an agent review used a separate session or that written
human approval follows that review. Those are mandatory documented review
obligations in [the PR template](../../PR_REVIEW_TEMPLATE.md), not facts inferred
from CI, labels or checkboxes. Protected changes require explicit human scope
authorization and reviewer inspection. S00's assignment explicitly authorizes
this governance/CI scaffolding; it does not authorize contract/design changes.

No bot auto-merge workflow or elevated `pull_request_target` execution is added.
PR CI has read-only permissions, does not interpolate PR text into shell code,
and only verifies packet completeness, branch/task identity and current base SHA.
Human reviewers still validate scope, prerequisites merged, evidence truth,
protected-path authorization and acceptance coverage. Green metadata is not an
approval. A material update requires fresh agent/human review.

## Recovery and stop

S00 creates no runtime data or migrations. Revert its implementation in a reviewed
PR if necessary; GitHub protection is external state and is not reverted by Git.
Any protection change requires explicit human authorization. Do not disable
required checks to work around a failing gate. After opening the S00 PR, stop:
independent review and explicit human merge approval are pending. No next task
is assigned and no release readiness is claimed.
