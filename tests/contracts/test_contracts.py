"""S01 contract-only positive, mutation, fault and protocol agreement tests."""

from __future__ import annotations

import json
import os
import secrets
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))

import yaml  # type: ignore[import-untyped]  # noqa: E402
from check_contracts import (  # noqa: E402
    AUTH_POLICY,
    ERROR_CODES,
    ContractChecker,
    ContractError,
    read_json,
)
from contract_protocols import (  # noqa: E402
    SSETranscript,
    auth_outcome,
    cancellation,
    check_scenarios,
    cursor_agreement,
    history_page,
    ordered_page,
)
from test_remediation import mutate_auth, write_api  # noqa: E402


class ContractTests(unittest.TestCase):
    def setUp(self) -> None:
        self.checker = ContractChecker(ROOT / "contracts")
        self.checker.load()
        self.run_id = "0199a1b2-c3d4-7e8f-9123-123456789abc"

    def event(self, name: str, **values: Any) -> dict[str, Any]:
        return {"run_id": self.run_id, "event_version": 1, "event": name, **values}

    def stream(
        self, state: str = "running", sequence: int = 0, text: str = ""
    ) -> SSETranscript:
        stream = SSETranscript(self.checker)
        stream.attach(
            self.event(
                "run.snapshot", status=state, snapshot_sequence=sequence, text=text
            )
        )
        return stream

    def copy_contract(self, parent: Path) -> ContractChecker:
        path = parent / "contracts"
        shutil.copytree(ROOT / "contracts", path)
        checker = ContractChecker(path)
        checker.load()
        return checker

    def test_complete_candidate_passes_without_writing_bytes(self) -> None:
        before = {
            p: p.read_bytes() for p in (ROOT / "contracts").rglob("*") if p.is_file()
        }
        self.checker.check()
        self.assertTrue(all(p.read_bytes() == value for p, value in before.items()))

    def test_raw_byte_mutation_whitespace_and_line_endings_fail(self) -> None:
        for suffix in (b" ", b"\r\n", b"\n"):
            with self.subTest(suffix=suffix), tempfile.TemporaryDirectory() as name:
                checker = self.copy_contract(Path(name))
                path = checker.directory / "README.md"
                path.write_bytes(path.read_bytes() + suffix)
                before = path.read_bytes()
                with self.assertRaisesRegex(ContractError, "checksum"):
                    checker.check_manifest()
                self.assertEqual(before, path.read_bytes())

    def test_manifest_missing_extra_escape_and_symlinks_fail(self) -> None:
        for fault in (
            "missing",
            "extra",
            "traversal",
            "absolute",
            "backslash",
            "directory-symlink",
            "file-symlink",
            "nested-manifest",
            "self",
            "bad-hash",
            "approval",
        ):
            with self.subTest(fault=fault), tempfile.TemporaryDirectory() as name:
                checker = self.copy_contract(Path(name))
                manifest = read_json(checker.directory / "manifest.json")
                if fault == "missing":
                    (checker.directory / "README.md").unlink()
                elif fault == "extra":
                    (checker.directory / "extra.json").write_text("{}")
                elif fault == "directory-symlink":
                    (checker.directory / "escape").symlink_to(
                        Path(name), target_is_directory=True
                    )
                elif fault == "file-symlink":
                    (checker.directory / "README.md").unlink()
                    (checker.directory / "README.md").symlink_to(
                        ROOT / "contracts/README.md"
                    )
                elif fault == "nested-manifest":
                    (checker.directory / "nested").mkdir()
                    (checker.directory / "nested/manifest.json").write_text("{}")
                elif fault == "approval":
                    manifest["status"] = "approved"
                elif fault == "bad-hash":
                    manifest["files"]["README.md"] = "not-sha256"
                else:
                    path = {
                        "traversal": "../outside",
                        "absolute": "/tmp/outside",
                        "backslash": "fixtures\\outside",
                        "self": "manifest.json",
                    }[fault]
                    manifest["files"][path] = "0" * 64
                (checker.directory / "manifest.json").write_text(json.dumps(manifest))
                with self.assertRaises(ContractError):
                    checker.check_manifest()

    def test_versions_must_agree(self) -> None:
        for target in ("openapi", "errors", "sse", "VERSION", "manifest"):
            with self.subTest(target=target), tempfile.TemporaryDirectory() as name:
                checker = self.copy_contract(Path(name))
                if target == "openapi":
                    checker.api["info"]["version"] = "2.0.0"
                elif target == "errors":
                    checker.errors["version"] = "1.1.0"
                elif target == "sse":
                    checker.sse["x-contract-version"] = "1.0.1"
                elif target == "VERSION":
                    (checker.directory / "VERSION").write_text("2.0.0\n")
                else:
                    manifest = read_json(checker.directory / "manifest.json")
                    manifest["contract_version"] = "2.0.0"
                    (checker.directory / "manifest.json").write_text(
                        json.dumps(manifest)
                    )
                with self.assertRaises(ContractError):
                    checker.check_manifest()

    def test_corrupt_json_yaml_duplicate_keys_and_nonfinite_fail(self) -> None:
        for file, payload in (
            ("openapi.yaml", "paths: ["),
            ("openapi.yaml", "openapi: 3.1.0\nopenapi: 3.0.0"),
            ("errors.json", "{"),
            ("errors.json", '{"version":1,"version":2}'),
            ("errors.json", '{"x":NaN}'),
        ):
            with (
                self.subTest(file=file, payload_kind=payload[:8]),
                tempfile.TemporaryDirectory() as name,
            ):
                checker = self.copy_contract(Path(name))
                (checker.directory / file).write_text(payload)
                with self.assertRaises(ContractError):
                    checker.load()

    def test_openapi_semantic_validation_rejects_bad_parameters_and_schema(
        self,
    ) -> None:
        for fault in (
            "path-required",
            "response",
            "schema-type",
            "duplicate-operation",
            "broken-ref",
            "remote-ref",
        ):
            with self.subTest(fault=fault):
                checker = ContractChecker(ROOT / "contracts")
                checker.load()
                api = checker.api
                if fault == "path-required":
                    api["paths"]["/api/v1/runs/{run_id}"]["get"]["parameters"][0][
                        "required"
                    ] = False
                elif fault == "response":
                    api["paths"]["/api/v1/meta"]["get"]["responses"]["200"].pop(
                        "description"
                    )
                elif fault == "schema-type":
                    api["components"]["schemas"]["User"]["type"] = "invalid-type"
                elif fault == "duplicate-operation":
                    api["paths"]["/api/v1/meta"]["get"]["operationId"] = "login"
                else:
                    api["components"]["schemas"]["User"]["properties"]["user_id"] = {
                        "$ref": "#/components/schemas/Missing"
                        if fault == "broken-ref"
                        else "https://network.invalid/schema"
                    }
                with self.assertRaises(ContractError):
                    checker.check_schemas()
                    checker.check_semantics()

    def test_draft_2020_schema_and_unknown_reference_fail(self) -> None:
        for fault in ("draft", "type", "ref"):
            with self.subTest(fault=fault):
                checker = ContractChecker(ROOT / "contracts")
                checker.load()
                if fault == "draft":
                    checker.sse["$schema"] = "https://json-schema.org/draft-07/schema"
                elif fault == "type":
                    checker.sse["oneOf"][0]["type"] = "typo"
                else:
                    checker.sse["$ref"] = "#/missing"
                with self.assertRaises(ContractError):
                    checker.check_schemas()

    def test_required_null_unknown_field_and_format_constraints(self) -> None:
        good = {"format": "text/v1", "text": "hello"}
        self.checker.validate(good, "#/components/schemas/MessageContent")
        for bad in (
            {"format": "text/v1"},
            {"format": "text/v1", "text": None},
            {**good, "unknown": True},
            {**good, "format": "other"},
        ):
            with self.subTest(fault=list(bad)):
                with self.assertRaises(ContractError):
                    self.checker.validate(bad, "#/components/schemas/MessageContent")
        session = read_json(ROOT / "contracts/fixtures/session-cli.json")
        for field, value in (("user_id", "bad-uuid"), ("expires_at", "bad-time")):
            with self.subTest(field=field):
                with self.assertRaises(ContractError):
                    self.checker.validate(
                        {**session, field: value}, "#/components/schemas/Session"
                    )

    def test_all_fixture_membership_and_values_are_checked(self) -> None:
        for fault in (
            "unindexed",
            "missing",
            "invalid-payload",
            "credential-value",
            "error-status",
        ):
            with self.subTest(fault=fault), tempfile.TemporaryDirectory() as name:
                checker = self.copy_contract(Path(name))
                fixtures = checker.directory / "fixtures"
                if fault == "unindexed":
                    (fixtures / "unindexed.json").write_text("{}")
                elif fault == "missing":
                    (fixtures / "sse-delta.json").unlink()
                elif fault == "invalid-payload":
                    (fixtures / "sse-delta.json").write_text("{}")
                elif fault == "credential-value":
                    (fixtures / "error.json").write_text(
                        '{"access_token":"must-not-be-committed"}'
                    )
                else:
                    errors = read_json(fixtures / "http-errors.json")
                    errors[0]["http_status"] = 200
                    (fixtures / "http-errors.json").write_text(json.dumps(errors))
                with self.assertRaises((ContractError, KeyError)):
                    checker.check_fixtures()

    def test_catalog_codes_statuses_and_error_enum_agree(self) -> None:
        for fault in ("status", "code", "enum"):
            with self.subTest(fault=fault):
                checker = ContractChecker(ROOT / "contracts")
                checker.load()
                if fault == "status":
                    checker.errors["codes"]["operation_unknown"] = 410
                elif fault == "code":
                    checker.errors["codes"]["invented"] = 400
                else:
                    checker.api["components"]["schemas"]["Error"]["properties"][
                        "error"
                    ]["properties"]["code"]["enum"].remove("forbidden")
                with self.assertRaises(ContractError):
                    checker.check_semantics()
        self.assertEqual(ERROR_CODES["operation_unknown"], 404)
        self.assertEqual(ERROR_CODES["operation_expired"], 410)

    def test_approved_surface_auth_pagination_and_session_regressions_fail(
        self,
    ) -> None:
        for fault in (
            "refresh",
            "cookie",
            "both-required",
            "idle",
            "absolute",
            "renewal",
            "transport",
            "csrf",
            "https",
            "origin",
            "host",
            "cors",
            "limit",
            "cursor-error",
            "message-state",
            "deletion",
            "lookup",
            "login",
            "cache",
            "cookie-scope",
        ):
            with self.subTest(fault=fault):
                checker = ContractChecker(ROOT / "contracts")
                checker.load()
                api = checker.api
                if fault == "refresh":
                    api["paths"]["/api/v1/auth/refresh"] = {
                        "post": {"operationId": "refresh"}
                    }
                elif fault == "cookie":
                    api["components"]["securitySchemes"]["cookieAuth"]["name"] = (
                        "unsafe"
                    )
                elif fault == "both-required":
                    api["paths"]["/api/v1/auth/session"]["get"]["security"] = [
                        {"bearerAuth": [], "cookieAuth": []}
                    ]
                elif fault in (
                    "idle",
                    "absolute",
                    "renewal",
                    "transport",
                    "csrf",
                    "https",
                    "origin",
                    "host",
                    "cors",
                ):
                    field = {
                        "idle": "idle_days",
                        "absolute": "absolute_days",
                        "renewal": "implicit_renewal",
                        "transport": "stored_client_type_binding",
                        "csrf": "csrf_value",
                        "https": "browser_https_required",
                        "origin": "exact_trusted_origin",
                        "host": "trusted_host_required",
                        "cors": "credentialed_cross_origin",
                    }[fault]
                    api["x-mimir-authentication"][field] = "invalid"
                elif fault == "limit":
                    api["paths"]["/api/v1/projects"]["get"]["parameters"][1]["schema"][
                        "maximum"
                    ] = 201
                elif fault == "cursor-error":
                    del api["paths"]["/api/v1/projects"]["get"]["responses"]["422"]
                elif fault == "message-state":
                    api["components"]["schemas"]["Message"]["properties"]["status"][
                        "enum"
                    ].append("pending")
                elif fault == "deletion":
                    api["paths"]["/api/v1/conversations/{id}"]["delete"]["responses"][
                        "204"
                    ] = {}
                elif fault == "lookup":
                    del api["paths"][
                        "/api/v1/operations/{operation_type}/{idempotency_key}"
                    ]["get"]["responses"]["410"]
                elif fault == "login":
                    api["paths"]["/api/v1/auth/login"]["post"]["responses"]["201"][
                        "content"
                    ]["application/json"]["schema"] = {
                        "$ref": "#/components/schemas/CliLoginResponse"
                    }
                elif fault == "cache":
                    del api["paths"]["/api/v1/auth/session"]["get"]["responses"]["200"][
                        "headers"
                    ]["Cache-Control"]
                else:
                    api["components"]["headers"]["SessionCookie"]["description"] = (
                        "insecure"
                    )
                with self.assertRaises((ContractError, KeyError)):
                    checker.check_semantics()

    def test_cli_web_login_shapes_and_metadata_omit_credentials(self) -> None:
        cli_session = read_json(ROOT / "contracts/fixtures/session-cli.json")
        web = read_json(ROOT / "contracts/fixtures/login-web.json")
        cli = {"access_token": secrets.token_urlsafe(32), "session": cli_session}
        self.checker.validate(cli, "#/components/schemas/CliLoginResponse")
        self.checker.validate(web, "#/components/schemas/WebLoginResponse")
        for payload, schema in (
            (web, "CliLoginResponse"),
            (cli, "WebLoginResponse"),
            ({**web, "access_token": None}, "WebLoginResponse"),
            ({"session": cli_session}, "WebLoginResponse"),
            ({**cli, "session": web["session"]}, "CliLoginResponse"),
        ):
            with self.subTest(schema=schema):
                with self.assertRaises(ContractError):
                    self.checker.validate(payload, "#/components/schemas/" + schema)
        with self.assertRaises(ContractError):
            self.checker.validate(
                {**cli_session, "access_token": None}, "#/components/schemas/Session"
            )
        self.assertEqual(AUTH_POLICY["idle_days"], 30)
        self.assertEqual(AUTH_POLICY["absolute_days"], 90)

    def test_utf8_bytes_not_character_count_and_no_truncation(self) -> None:
        for text in ("a" * 1048576, "é" * 524288, "😀" * 262144):
            payload = {"format": "text/v1", "text": text}
            self.checker.validate(payload, "#/components/schemas/MessageContent")
            self.assertEqual(payload["text"], text)
        for text in ("a" * 1048577, "é" * 524289, "😀" * 262145, "\ud800"):
            with self.subTest(characters=len(text)):
                with self.assertRaises(ContractError):
                    self.checker.validate(
                        {"format": "text/v1", "text": text},
                        "#/components/schemas/MessageContent",
                    )
                with self.assertRaises(ContractError):
                    self.checker.validate(
                        {"prompt": text}, "#/components/schemas/StandaloneSubmit"
                    )

    def test_sse_replacement_control_sequence_and_reattach(self) -> None:
        stream = self.stream(text="old", sequence=10)
        stream.attach(
            self.event(
                "run.snapshot", status="running", snapshot_sequence=3, text="Hello"
            )
        )
        self.assertEqual(stream.text, "Hello")
        stream.frame(": heartbeat\n\n")
        stream.event(self.event("run.usage", input_tokens=1, output_tokens=1))
        self.assertEqual(stream.sequence, 3)
        stream.event(self.event("message.delta", sequence=4, text="!"))
        self.assertEqual(stream.text, "Hello!")
        stream.event(self.event("run.completed", final_text="Hello!"))
        self.assertEqual(stream.terminal_events, 1)
        # Stream final_text is not an authorization to emit stdout: canonical GET
        # confirmation is explicitly required by the protocol contract below.
        self.assertIn(
            "GET /runs/{run_id}", (ROOT / "contracts/SSE_PROTOCOL.md").read_text()
        )

    def test_sse_delta_gaps_duplicates_reorders_mismatch_and_impossible_states(
        self,
    ) -> None:
        for sequence in (1, 2, 4, 100):
            with self.subTest(sequence=sequence):
                with self.assertRaises(ContractError):
                    self.stream(sequence=2).event(
                        self.event("message.delta", sequence=sequence, text="x")
                    )
        for event in (
            self.event("run.started"),
            self.event("message.delta", sequence=1, text="x", unknown=True),
            self.event(
                "message.delta",
                sequence=1,
                text="x",
                run_id="0199a1b2-c3d4-7e8f-9123-123456789abd",
            ),
        ):
            with self.assertRaises(ContractError):
                self.stream().event(event)
        stream = self.stream()
        event = self.event("message.delta", sequence=1, text="x")
        with self.assertRaises(ContractError):
            stream.frame("event: run.started\ndata: " + json.dumps(event) + "\n\n")
        with self.assertRaises(ContractError):
            self.stream("pending").event(event)
        with self.assertRaises(ContractError):
            SSETranscript(self.checker).event(event)
        with self.assertRaises(ContractError):
            self.stream("pending").event(self.event("run.completed", final_text="x"))

    def test_sse_all_terminal_states_reject_later_active_events(self) -> None:
        for terminal in ("completed", "failed", "cancelled", "interrupted"):
            with self.subTest(terminal=terminal):
                stream = self.stream()
                extra = (
                    {"final_text": "x"}
                    if terminal == "completed"
                    else {"error_code": "dependency_unavailable"}
                    if terminal == "failed"
                    else {}
                )
                stream.event(self.event("run." + terminal, **extra))
                with self.assertRaises(ContractError):
                    stream.event(self.event("message.delta", sequence=1, text="x"))
                with self.assertRaises(ContractError):
                    self.stream(terminal).event(self.event("run.started"))

    def test_sse_required_unknown_null_fields_and_control_sequence_fail(self) -> None:
        good = self.event("run.started")
        for event in (
            {**good, "sequence": 1},
            {**good, "event_version": 2},
            {**good, "run_id": None},
            {"event": "run.started"},
        ):
            with self.assertRaises(ContractError):
                self.checker.validate(event, "sse")
        for frame in (
            "event: run.started\n\n",
            "data: {}\n\n",
            "event: run.started\ndata: {}\ndata: {}\n\n",
            "event: run.started\ndata: invalid\n\n",
        ):
            with self.assertRaises(ContractError):
                self.stream("pending").frame(frame)

    def test_cancellation_status_snapshot_flag_and_result_consistency(self) -> None:
        cases = read_json(ROOT / "contracts/fixtures/cancellation.json")
        for case in cases:
            cancellation(self.checker, case["http_status"], case["run"])
            with self.assertRaises(ContractError):
                cancellation(
                    self.checker,
                    200 if case["http_status"] == 202 else 202,
                    case["run"],
                )
            with self.assertRaises(ContractError):
                cancellation(
                    self.checker,
                    case["http_status"],
                    {
                        **case["run"],
                        "cancellation_requested": not case["run"][
                            "cancellation_requested"
                        ],
                    },
                )
        terminal = next(c["run"] for c in cases if c["run"]["status"] == "failed")
        with self.assertRaises(ContractError):
            self.checker.validate(
                {**terminal, "final_text": "fabricated"}, "#/components/schemas/Run"
            )

    def test_pagination_ties_exclusive_boundaries_and_removed_origin(self) -> None:
        for case in read_json(ROOT / "contracts/fixtures/listing-cases.json"):
            rows = ordered_page(
                case["rows"], tuple(case["fields"]), case["limit"], case.get("boundary")
            )
            self.assertEqual([r[case["fields"][1]] for r in rows], case["expected_ids"])
            if case.get("boundary"):
                remaining = [
                    r
                    for r in case["rows"]
                    if r[case["fields"][1]] != case["boundary"][1]
                ]
                self.assertEqual(
                    rows,
                    ordered_page(
                        remaining,
                        tuple(case["fields"]),
                        case["limit"],
                        case["boundary"],
                    ),
                )

    def test_history_inherited_local_boundaries_survive_append(self) -> None:
        first = history_page(["r1", "r2", "r3"], ["r4", "r5"], 2)
        self.assertEqual([r["ordinal"] for r in first], [4, 5])
        older = history_page(["r1", "r2", "r3"], ["r4", "r5"], 2, first[0]["ordinal"])
        self.assertEqual([r["ordinal"] for r in older], [2, 3])
        self.assertEqual(
            older,
            history_page(
                ["r1", "r2", "r3"], ["r4", "r5", "r6"], 2, first[0]["ordinal"]
            ),
        )
        self.assertEqual(history_page([], [], 100), [])

    def test_cursor_scope_and_integrity_denial_are_fixture_agreement_only(self) -> None:
        cases = read_json(ROOT / "contracts/fixtures/cursor-cases.json")
        for case in cases:
            if case["valid"]:
                cursor_agreement(case["scope"], case["cursor"])
            else:
                with self.assertRaises(ContractError):
                    cursor_agreement(case["scope"], case["cursor"])
        self.assertEqual(ERROR_CODES["invalid_request"], 422)
        # No cryptographic cursor implementation or live authorization claim.

    def test_auth_error_scenarios_and_cookie_scope_requirements(self) -> None:
        for case in read_json(ROOT / "contracts/fixtures/auth-cases.json"):
            allowed, status, code = auth_outcome(case["facts"])
            self.assertEqual(allowed, case["allowed"])
            if not allowed:
                self.assertEqual(
                    (status, code), (case["http_status"], case["error_code"])
                )
        text = (ROOT / "contracts/HTTP_SEMANTICS.md").read_text()
        for rule in (
            "Max-Age no greater",
            "remaining absolute",
            "matching name",
            "Cache-Control: no-store",
            "SHA-256",
            "No refresh token",
            "30 days",
            "90-day",
            "before worker claim",
            "Late provider results",
            "No frozen snapshot",
        ):
            self.assertIn(rule, text)

    def test_cookie_issuance_expiry_scope_and_lifetime_fixtures(self) -> None:
        cases = read_json(ROOT / "contracts/fixtures/cookie-policies.json")
        check_scenarios(self.checker, "cookie_policy", cases)
        for field, value in (
            ("http_only", False),
            ("secure", False),
            ("same_site", "Lax"),
            ("path", "/api"),
            ("domain", "browser.invalid"),
            ("max_age", 100000000),
            ("json_credential_absent", False),
            ("transport", "http"),
            ("cache_control", "public"),
        ):
            with self.subTest(field=field), self.assertRaises(ContractError):
                check_scenarios(
                    self.checker, "cookie_policy", [{**cases[0], field: value}]
                )
        with self.assertRaises(ContractError):
            check_scenarios(self.checker, "cookie_policy", [{**cases[1], "max_age": 1}])

    def test_each_request_rejects_unknown_fields_and_preserves_null_rules(self) -> None:
        index = read_json(ROOT / "contracts/fixtures/index.json")
        for entry in index["fixtures"]:
            reference = entry.get("schema", "")
            if reference.split("/")[-1] in {
                "ProjectCreate",
                "ProjectPatch",
                "ConversationCreate",
                "ConversationPatch",
                "MessageSubmit",
                "StandaloneSubmit",
                "UserPatch",
                "BranchCreate",
            }:
                value = read_json(ROOT / "contracts/fixtures" / entry["path"])
                with self.subTest(schema=reference), self.assertRaises(ContractError):
                    self.checker.validate(
                        {**value, "unexpected_field": True}, reference
                    )
        self.checker.validate(
            {
                "username": "synthetic-user",
                "password": secrets.token_urlsafe(20),
                "client_type": "cli",
            },
            "#/components/schemas/LoginRequest",
        )
        with self.assertRaises(ContractError):
            self.checker.validate(
                {"username": "synthetic-user", "client_type": "cli"},
                "#/components/schemas/LoginRequest",
            )
        self.checker.validate(
            {"expected_conversation_version": 1, "project_id": None},
            "#/components/schemas/ConversationPatch",
        )
        with self.assertRaises(ContractError):
            self.checker.validate(
                {"expected_conversation_version": None},
                "#/components/schemas/ConversationPatch",
            )

    def test_empty_listings_have_explicit_null_cursor(self) -> None:
        for name in ("ListProjects", "ListConversations", "ListMessages"):
            self.checker.validate(
                {"items": [], "next_cursor": None}, "#/components/schemas/" + name
            )
            with self.assertRaises(ContractError):
                self.checker.validate({"items": []}, "#/components/schemas/" + name)

    def test_documented_examples_are_validated(self) -> None:
        self.checker.api["components"]["schemas"]["MessageContent"]["examples"] = [
            {"format": "text/v1", "text": None}
        ]
        with self.assertRaises(ContractError):
            self.checker.check_examples()

    def test_ci_required_make_path_propagates_contract_failure(self) -> None:
        workflow = yaml.safe_load(
            (ROOT / ".github/workflows/bootstrap.yml").read_text()
        )
        self.assertIn(
            "make check",
            [step.get("run") for step in workflow["jobs"]["foundation"]["steps"]],
        )
        makefile = (ROOT / "Makefile").read_text()
        self.assertRegex(makefile, r"(?m)^check:.*contract-check")
        # Execute the actual Make target on a malformed contract. Its first checker
        # must fail before the recursive test-suite invocation can be reached.
        with tempfile.TemporaryDirectory() as name:
            root = Path(name)
            shutil.copy2(ROOT / "Makefile", root / "Makefile")
            shutil.copytree(
                ROOT / "scripts",
                root / "scripts",
                ignore=shutil.ignore_patterns("__pycache__"),
            )
            shutil.copytree(ROOT / "contracts", root / "contracts")
            backend = root / "backend"
            backend.mkdir()
            for filename in (
                "pyproject.toml",
                "uv.lock",
                ".python-version",
                "README.md",
            ):
                shutil.copy2(ROOT / "backend" / filename, backend / filename)
            shutil.copytree(ROOT / "backend/src", backend / "src")
            env = os.environ.copy()
            env.pop("VIRTUAL_ENV", None)
            env.pop("UV_PROJECT_ENVIRONMENT", None)
            # Positive control provisions a separate locked environment with the
            # actual uv executable. A dependency/bootstrap failure cannot count
            # as evidence that the malformed-contract probe worked.
            control = subprocess.run(
                [
                    "uv",
                    "run",
                    "--offline",
                    "--locked",
                    "python",
                    "../scripts/check_contracts.py",
                ],
                cwd=backend,
                env=env,
                capture_output=True,
                text=True,
                check=False,
                timeout=60,
            )
            self.assertEqual(control.returncode, 0, control.stderr)
            self.assertIn("PASS: candidate", control.stdout)
            original = yaml.safe_load((root / "contracts/openapi.yaml").read_text())
            for mutation in (
                "csrf-value",
                "login-cookie-missing",
                "logout-expiry-policy",
            ):
                import copy

                changed = copy.deepcopy(original)
                reason = mutate_auth(changed, mutation)
                write_api(root / "contracts", changed)
                failed = subprocess.run(
                    ["make", "contract-check"],
                    cwd=root,
                    env=env,
                    capture_output=True,
                    text=True,
                    check=False,
                    timeout=30,
                )
                self.assertNotEqual(failed.returncode, 0, mutation)
                self.assertIn(reason, failed.stderr)
                self.assertNotIn("PASS: candidate", failed.stdout)
                print(
                    f"Make semantic mutation {mutation}: exit {failed.returncode}; {reason}"
                )
            (root / "contracts/openapi.yaml").write_text("invalid: [")
            result = subprocess.run(
                ["make", "contract-check"],
                cwd=root,
                env=env,
                capture_output=True,
                text=True,
                check=False,
                timeout=30,
            )
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("FAIL: contract validation", result.stderr)
            self.assertNotIn("PASS: candidate", result.stdout)


if __name__ == "__main__":
    unittest.main()
