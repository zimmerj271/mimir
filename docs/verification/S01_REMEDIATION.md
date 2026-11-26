# S01 independent-review remediation evidence

PR #11; publication branch `feature/s01-shared-contract-foundation-validated`.
Previously reviewed SHA: `f45ca6c7dc87f6f0aa97cf1ec166fb2bf41ad790`.
Base canonical main: `dd8a03340e54f6513d6f08a0385b3e070e493c7e`.
Execution date: 2026-11-26. The reviewed implementation/history is preserved;
corrections are additive commits. Contract 1.0.0 remains a candidate pending
renewed independent review and explicit human contract/merge approval.

## Authority and preparation

The owner accepted R1–R3 in the [independent review](https://github.com/zimmerj271/mimir/pull/11#issuecomment-7200813191)
and explicitly authorized these corrections and publication to the existing PR.
Root/component AGENTS, coding rules, approved S01 plan, decision addenda, actual
implementation, prior evidence and downstream task specifications were read.
A fresh HTTPS Git fetch with the existing gh credential helper confirmed clean
local/remote head f45ca6c, canonical main ancestry and PR target main. The existing
publication branch was selected without rebasing, cherry-picking or rewriting.
Supported execution escalation was used for Git metadata writes and the backend
gate's local IPC; no toolchain or sandbox workaround was added to application code.

## R1 — public active-run discovery

Owner-approved **S01-DISCOVERY** adds ConversationDetail only to getConversation
200, with the unchanged summary properties plus required nullable UUID active_run_id.
Shared YAML property definitions and separate closed schemas avoid closed-allOf
extension errors. List/create/patch/branch responses remain Conversation.

[Discovery fixtures](../../contracts/fixtures/discovery-cases.json) contain 21
public-response transcripts with separate synthetic read-snapshot facts. The
client constructs schema-validated existing conversation/run/status/SSE/history
GET paths using public identities only. Scenarios cover pending, running,
requested cancellation, explicit null, each terminal outcome, standalone/foreign/
other-conversation/inaccessible/fenced run exclusion, conversation denials and
runtime dependency failure. An archived first listing page is traversed to the
latest eligible active conversation. Completion before status inspection or
attachment recovers committed canonical history; a terminal SSE snapshot is
confirmed through Run GET. Every transcript is GET-only: no cancellation or
resubmission is fabricated. Metadata/version/timestamp must match the same
synthetic snapshot. No scenario proves real database concurrency or RLS.

Tests in [test_remediation.py](../../tests/contracts/test_remediation.py) validate
all payloads against canonical schemas, reject omitted/malformed/wrongly typed
active_run_id and unexpected fields, check null and unchanged summary responses,
reject eligibility/disclosure regressions and missing canonical completion history.
The checker asserts the actual getConversation schema reference. Reverting that
reference to Conversation and removing ConversationDetail independently prevents
the new assertions/transcript from passing. Before edits the observed reference
was exactly `{'$ref': '#/components/schemas/Conversation'}`.

T10 owns the owner-scoped snapshot/domain/repository lookup boundary; T11 owns its
runtime projection and HTTP response. Existing V005/partial uniqueness is reused.
T07 is already merged, and T11 has T07 as a transitive predecessor through T09/T08;
no task graph policy change was needed. T20/T28 own actual schema/protocol
conformance. CLI-03 typed consumption, CLI-10 selection, CLI-11 history, CLI-14
streaming and CLI-15 startup/resume/recovery obligations are now explicit in their
briefs and architectures. No downstream task implementation was started.

## R2 — exact chronological pagination keys

Before editing, the reviewer's exact timestamps reproduced this output:

```text
R2 baseline order: ['2026-11-20T00:00:00Z', '2026-11-20T00:00:00.5Z']
R2 baseline older page: []
```

The shared chronological_key parses the integral second/offset and retains the
fraction as an exact rational; UUID identities use UUID integer ordering. Both
sorting and exclusive-boundary filtering call that same normalization. There is
no float or microsecond truncation. New gate fixtures and tests exercise both
project and conversation keysets, whole/fractional seconds, differing fractional
precision, equal instants, UUID case/ties, nine-digit subsecond distinctions,
equivalent offset representations, exclusive traversal and removed boundary rows.
The exact reproduction now returns newer then older; its older page contains the
whole-second row, including when the newer origin record has been removed.
Existing filtering, limits, mutable-list semantics and visible-ordinal history
are preserved. This remains a reference model, not runtime SQL.

## R3 — referenced authentication agreement

Before editing, two isolated copies with refreshed OpenAPI checksums independently
passed the complete checker after (1) CSRF const became "0" and (2) login Set-Cookie
was removed. The reviewed checkout was not corrupted during reproduction.

The corrected checker inspects actual BrowserCSRF/BrowserOrigin name, header
location, string schema, CSRF constant, conditional required=false, complete
normative descriptions and structured browser-only requirement policy. Existing
mutation-operation reference assertions remain. It binds login/logout Set-Cookie
to SessionCookie/ExpiredSessionCookie, applicable Cache-Control to NoStore, then
inspects the resolved header definitions, types, conditional delivery, secure
cookie attributes, bounded login lifetime and logout Max-Age=0. Structured policy,
full normative descriptions and fixture agreement are checked together; isolated
security-word presence is no longer the test. CLI cookie delivery stays forbidden.

There are 27 independent disposable authentication mutations, each refreshing the
OpenAPI checksum, first passing check_manifest, then failing the full checker for
the intended authentication diagnostic. They cover CSRF value/name/location/type/
unconditional requirement/policy, Origin name/location/type/requirement/policy,
missing and swapped cookie wiring, missing/miswired cache control, NoStore schema/
requirement, cookie type/conditional delivery/security/lifetime and logout expiry
policy/description. The unmodified candidate is the positive control.

The actual Make target is invoked for three representative semantic mutations,
with a real independently provisioned locked environment and refreshed checksums:

```text
Make semantic mutation csrf-value: exit 2; authentication BrowserCSRF
Make semantic mutation login-cookie-missing: exit 2; authentication login Set-Cookie
Make semantic mutation logout-expiry-policy: exit 2; authentication ExpiredSessionCookie
```

Each fails in the real first checker before recursive unittest execution. The
existing malformed-YAML Make failure probe is retained. No executable, assertion,
interpreter or event-loop shim substitutes for the required tooling.

## Revised-candidate execution

Common environment, from `/tmp/mimir-s01-work`:

```sh
export PATH=/home/zimmerj/.local/share/mise/installs/uv/0.13.9/.mise-bins:$PATH
export UV_CACHE_DIR=/tmp/mimir-s01-cache
export UV_PYTHON_DOWNLOADS=never
export UV_OFFLINE=true
```

uv reports 0.13.9; locked interpreter reports Python 3.14.7. All runs below are
new agent execution, distinct from earlier human and independent-review evidence.
The 45-package lock is unchanged; no dependency or runtime version was modified.

| Command | Actual result |
|---|---|
| `uv lock --offline --check --project backend` | exit 0; 45 packages resolved |
| `uv sync --offline --locked --all-groups --project backend` | exit 0; 45 resolved, 43 checked |
| `uv run --locked --project backend python scripts/update_contract_manifest.py` | exit 0; 43 governed artifacts hashed, no approval granted |
| `make contract-check` | exit 0; schema/reference/semantic/fixture/manifest checks and 34 tests in 52.491s, including all seven R1–R3 regression tests and the Make semantic-failure probes |
| `make check` | exit 0; governance/links/registry/hygiene passed; 10 governance tests in 0.074s and 34 contract tests in 52.731s |
| `make backend-check` (supported execution escalation for local IPC) | exit 0; locked sync/check, 19 files already formatted, Ruff passed, strict mypy passed for 9 source files, 105 tests passed in 6.49s |
| `uv run --locked --project backend mypy --config-file backend/pyproject.toml scripts/check_contracts.py scripts/contract_protocols.py` | exit 0; 2 source files passed after correcting a local variable type collision |

During development, the first targeted run exposed a new path-reference validation
root omission and failed; adding the canonical paths at the validation root fixed
it. The next standalone targeted run passed seven tests in 31.001s. Subsequent
terminal-snapshot Run confirmation and fixture updates are covered by the final
34-test Make gate above. Initial Ruff diagnostics were fixed; the final backend
gate independently passed formatting/lint/types. Failed intermediate runs are not
reported as passes or substituted for final validation.

Manifest SHA-256:
`aa8bf7b0a5031a326f6231a6beed63c0cd24652e74400e647e4ff2250947a99d`.
Lock SHA-256:
`2abb85c1aa1e1de020f1c31acfe307db04c9d76fb81ffca3dba7747579ef8ac0`.
[Revised input fingerprints](S01_REMEDIATION_INPUTS.json) bind 92 current inputs: code,
contracts, fixtures, toolchain inputs, affected specifications and acceptance
mapping. Record SHA-256: `bf5b0d0ab3fffc124f2c4223f4973daa05b46aecac886eed84fbb76567c0072f`; all 92 hashes were verified after validation. The [original fingerprints](S01_QUALITY_INPUTS.json) and
[human transcript](S01_HUMAN_BACKEND_CHECK.txt) remain historical evidence of f45ca6c,
not validation of these new changes.

## Scope and remaining gates

The corrective diff preserves application runtime code, Go runtime code, SQL
migrations, dependencies, Make/CI/governance enforcement and previously reviewed
history. No deployment/data operation occurred. Rejection before downstream work
can be handled by a separately reviewed revert of the corrective commit, without
SQL rollback or data restoration. No real HTTP/SQL/RLS/snapshot-concurrency, Go
attachment, provider or new container conformance is claimed. Current PR CI is
not inferred from local checks or the prior reviewed revision's green CI.

R1–R3 are implemented for renewed independent review, not independently accepted
or marked resolved on the reviewer's behalf. No contract approval, merge, T09 or
successor work is authorized by these results. The submitted exact SHA is recorded
in the updated PR evidence and consolidated response.
