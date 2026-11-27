# S01 second independent-review remediation (R4/R5)

PR #11; branch `feature/s01-shared-contract-foundation-validated`.
Previously reviewed commit: `172b5bfc5a9c87e80eb352b2ac0e7d88d52087ad`.
Canonical main: `dd8a03340e54f6513d6f08a0385b3e070e493c7e`.
Execution date: 2026-11-26. The owner authorized R4/R5 corrections after the
[second independent review](https://github.com/zimmerj271/mimir/pull/11#issuecomment-7203595860).
That review independently verified and closed R1–R3 for S01. Those corrections
are preserved. Contract 1.0.0 remains candidate_pending_human_approval; renewed
independent review and explicit human contract/merge authorization remain required.

## Preparation and R4 reproduction

Read the complete review, root/backend AGENTS and existing implementation/evidence.
A fresh HTTPS Git fetch confirmed clean local and PR head at 172b5bfc, main ancestry,
and the existing publication branch. The approved uv 0.13.9/Python 3.14.7 environment
and writable offline cache remain available. No history was rewritten.

Before editing, the full checker passed the reviewed candidate. A recursive audit
of all canonical Run fixture payloads found **45 payloads, six invalid**, all in
`contracts/fixtures/discovery-cases.json`, in these scenarios:

- exclude-completed
- exclude-failed
- exclude-cancelled
- exclude-interrupted
- completion-before-status
- completion-before-attachment

The accepted counterexample was created_at `2026-11-24T00:00:00Z`, completed_at
`2026-11-20T00:00:01Z`. The other Run fixture payloads did not violate timestamp
ordering. V005's existing `completed_at >= created_at` constraint was inspected,
not modified.

## R4 correction and regression coverage

Discovery runs now consistently originate on November 20 in every snapshot/status/
terminal copy of their synthetic identity, before the November 20 completion and
attachment outcomes. Terminal-exclusion conversation metadata and recovered
canonical-message timestamps align with that synthetic completion timeline. This
avoids correcting a terminal response by contradicting its earlier creation time.
Run status, nullable fields, identities and discovery semantics remain unchanged.

The reusable `ContractChecker.check_run_lifecycle` rejects non-null completed_at
before created_at using the exact chronological_key introduced for R2, comparing
its exact instant component. The existing UUID and timestamp schema validation
still runs first. No new public timestamp format/precision, status or nullability
restriction is introduced. Canonical-type dispatch applies the same assertion to
Run schema references, actual HTTP Run response schema references, and canonical
Run schema/media examples. Cancellation and discovery already validate their
canonical payloads through that common entry point. An arbitrary open Error.details
object is not reclassified as a Run merely because it contains similar field names.

Three new tests in [test_remediation.py](../../tests/contracts/test_remediation.py):

- `test_r4_run_lifecycle_chronological_invariant`: nonterminal/null, after creation,
  at creation, the original impossible outcome, before/after within a nine-digit
  fractional second, and equivalent precision/UTC representations.
- `test_r4_response_and_examples_share_semantic_assertion`: actual Run response
  reference, schema examples and media examples all reject the same impossible
  outcome; open error-details semantics remain unchanged.
- `test_r4_impossible_fixture_rejected_after_checksum_refresh`: isolated disposable
  discovery fixture recreates the original defect, refreshes only its governed
  checksum, passes manifest validation, then fails the complete framework with
  `Run completed_at precedes created_at`.

The targeted run passed **3 tests in 1.636s**. A fresh post-correction audit validated
all **45 canonical Run payloads**, with zero lifecycle failures. The unmodified
corrected fixtures remain the positive control in the full mandatory suite.

## R5 factual dependency correction

The canonical graph was traversed without editing it. T11's predecessor closure is
S00, S01, T01, T02, T03, T04, T05, T06, T08, T09 and T10. **T07 is absent**; in
particular T08 depends on T04, so the former T09/T08 transitive explanation was
incorrect.

T07 is already merged into canonical main, and its required V005 schema is available
to T10/T11. Availability in the merged baseline is distinct from an edge or ancestor
in TASK_GRAPH.json. Corrected the claim in HTTP_SEMANTICS, the T11 brief and the
prior remediation evidence; a repository-wide Markdown search found no remaining
copy of that inaccurate claim. T10/T11 discovery obligations remain intact. No task
graph, branch-protection policy or other dependency policy was changed.

## External scanner matter — owner disposition pending

Read-only GitHub check-run inspection confirmed GitGuardian failure on reviewed
head 172b5bfc: check run **140169609073**, incident **41163132**, eight Generic High
Entropy occurrences in discovery-cases.json. Reported lines were 4, 79, 154, 229,
268, 326, 384 and 442. Each line was independently checked: all are
`authenticated_owner` and use the same literal synthetic fixture UUID. Its literal
origin was verified against the preserved fixture-generation script and its use
against the contract harness's synthetic owner-equality comparisons. It is an
owner identity in test data, not a session token, bearer credential, cookie,
password, provider key or production account lookup. No credential value is
included in this report.

This supports the reviewer's false-positive assessment; it does not dismiss the
failed scanner check. Scanner/owner disposition remains explicitly pending. No
suppression, scanner configuration change, identity replacement to evade scanning,
credential rotation or Git history rewrite was performed.

## External branch-protection matter — owner governance disposition pending

A read-only GitHub main-protection API read confirmed:

| Setting | Live repository | Checked-in approved policy |
|---|---|---|
| required_approving_review_count | 0 | 1 |
| require_last_push_approval | false | true |

The two foundation checks with app_id 15368, strict status checks and administrator
enforcement remain enabled in the observed live state. The prior head's current
Linux/macOS foundation runs succeeded, but its GitGuardian check still failed;
those results do not validate a future corrected head. This is a separate existing
owner-governance discrepancy. No branch protection, repository permission or
checked-in main-protection policy was modified. Explicit owner reconciliation or
disposition remains required before merge; this remediation does not grant it.

## Newly executed validation

All commands run from `/tmp/mimir-s01-work`, with:

```sh
export PATH=/home/zimmerj/.local/share/mise/installs/uv/0.13.9/.mise-bins:$PATH
export UV_CACHE_DIR=/tmp/mimir-s01-cache
export UV_PYTHON_DOWNLOADS=never
export UV_OFFLINE=true
```

The backend gate uses supported execution escalation for local IPC; no runtime or
test workaround changes the environment policy. These are new agent executions;
prior evidence is retained as historical evidence of its respective reviewed SHA.

| Command | Actual result |
|---|---|
| `uv lock --offline --check --project backend` | exit 0, 45 packages resolved |
| `uv sync --offline --locked --all-groups --project backend` | exit 0, 45 resolved / 43 checked |
| `uv run --locked --project backend python scripts/update_contract_manifest.py` | exit 0, 43 governed artifact hashes maintained deliberately |
| `uv run --locked --project backend python -m unittest discover -s tests/contracts -p test_remediation.py -k test_r4 -v` | exit 0, 3 tests in 1.636s |
| `make contract-check` | exit 0; 37 tests in 53.877s, including unchanged R1–R3 regressions and 3 new R4 tests |
| `make check` | exit 0; graph/registry/links/hygiene, 10 governance tests in 0.073s and 37 contract tests in 54.090s |
| `make backend-check` | exit 0; locked sync/check, 19 files already formatted, Ruff passed, strict mypy passed for 9 source files, 105 tests in 6.58s |

Manifest SHA-256:
`e31b8e9232d36a9b374d09f00ca631a5cbb82bd3e949c88ef8350c8de5f0297f`.
Unchanged dependency lock SHA-256:
`2abb85c1aa1e1de020f1c31acfe307db04c9d76fb81ffca3dba7747579ef8ac0`.
[Current input fingerprints](S01_SECOND_REMEDIATION_INPUTS.json) bind the revised
candidate (94 inputs; record SHA-256 `a06f1c941232fc10664375fc9f4c32dbf78d52cb6f534607df9bb08d73ecd8ca`). All 94 hashes were verified after the gates. The existing real Make authentication semantic mutation probes again returned exit 2 for CSRF value, missing login cookie and missing logout expiry policy, preserving R3. [Prior R1–R3 evidence](S01_REMEDIATION.md) and its fingerprints remain
historical, with the inaccurate R5 prose explicitly corrected.

## Limits and stopping boundary

No runtime application/CLI code, database migration, dependency, Make/CI enforcement,
task graph, scanner configuration or repository permission changed. These tests
prove contract-level agreement only; actual HTTP/SQL/RLS/concurrency/Go conformance
remains with the already named downstream owners. Rejection can use a separately
reviewed revert of the corrective commit; no SQL rollback or data restoration is
needed. The exact submitted commit is recorded in PR #11's updated evidence and
R4/R5 response. No independent acceptance of R4/R5, contract approval, merge or T09
is claimed. Both external owner matters remain open.
