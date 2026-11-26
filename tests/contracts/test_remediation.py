"""S01 R1–R3 regressions; contract agreement only, no runtime implementation."""

from __future__ import annotations

import copy
import hashlib
import json
import sys
import tempfile
import unittest
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))

import yaml  # type: ignore[import-untyped]  # noqa: E402
from check_contracts import ContractChecker, ContractError, read_json  # noqa: E402
from contract_protocols import (  # noqa: E402
    ConversationDiscovery,
    chronological_key,
    ordered_page,
)

# Path, replacement, expected diagnostic. None removes a key in that copy only.
AUTH_MUTATIONS: dict[str, tuple[tuple[str, ...], Any, str]] = {
    "csrf-value": (
        ("components", "parameters", "BrowserCSRF", "schema", "const"),
        "0",
        "BrowserCSRF",
    ),
    "csrf-name": (
        ("components", "parameters", "BrowserCSRF", "name"),
        "X-Wrong-CSRF",
        "BrowserCSRF",
    ),
    "csrf-location": (
        ("components", "parameters", "BrowserCSRF", "in"),
        "query",
        "BrowserCSRF",
    ),
    "csrf-unconditional": (
        ("components", "parameters", "BrowserCSRF", "required"),
        True,
        "BrowserCSRF",
    ),
    "csrf-type": (
        ("components", "parameters", "BrowserCSRF", "schema", "type"),
        "integer",
        "BrowserCSRF",
    ),
    "csrf-policy": (
        (
            "components",
            "parameters",
            "BrowserCSRF",
            "x-mimir-browser-requirement",
            "cli",
        ),
        "required",
        "BrowserCSRF",
    ),
    "origin-name": (
        ("components", "parameters", "BrowserOrigin", "name"),
        "Wrong-Origin",
        "BrowserOrigin",
    ),
    "origin-location": (
        ("components", "parameters", "BrowserOrigin", "in"),
        "query",
        "BrowserOrigin",
    ),
    "origin-unconditional": (
        ("components", "parameters", "BrowserOrigin", "required"),
        True,
        "BrowserOrigin",
    ),
    "origin-type": (
        ("components", "parameters", "BrowserOrigin", "schema", "type"),
        "integer",
        "BrowserOrigin",
    ),
    "origin-policy": (
        (
            "components",
            "parameters",
            "BrowserOrigin",
            "x-mimir-browser-requirement",
            "mutations",
        ),
        "optional",
        "BrowserOrigin",
    ),
    "login-cookie-missing": (
        (
            "paths",
            "/api/v1/auth/login",
            "post",
            "responses",
            "201",
            "headers",
            "Set-Cookie",
        ),
        None,
        "login Set-Cookie",
    ),
    "logout-cookie-missing": (
        (
            "paths",
            "/api/v1/auth/logout",
            "post",
            "responses",
            "204",
            "headers",
            "Set-Cookie",
        ),
        None,
        "logout Set-Cookie",
    ),
    "login-cookie-swapped": (
        (
            "paths",
            "/api/v1/auth/login",
            "post",
            "responses",
            "201",
            "headers",
            "Set-Cookie",
            "$ref",
        ),
        "#/components/headers/ExpiredSessionCookie",
        "login Set-Cookie",
    ),
    "logout-cookie-swapped": (
        (
            "paths",
            "/api/v1/auth/logout",
            "post",
            "responses",
            "204",
            "headers",
            "Set-Cookie",
            "$ref",
        ),
        "#/components/headers/SessionCookie",
        "logout Set-Cookie",
    ),
    "login-cache-missing": (
        (
            "paths",
            "/api/v1/auth/login",
            "post",
            "responses",
            "201",
            "headers",
            "Cache-Control",
        ),
        None,
        "login Cache-Control",
    ),
    "session-cache-miswired": (
        (
            "paths",
            "/api/v1/auth/session",
            "get",
            "responses",
            "200",
            "headers",
            "Cache-Control",
            "$ref",
        ),
        "#/components/headers/SessionCookie",
        "getSession Cache-Control",
    ),
    "logout-cache-missing": (
        (
            "paths",
            "/api/v1/auth/logout",
            "post",
            "responses",
            "204",
            "headers",
            "Cache-Control",
        ),
        None,
        "logout Cache-Control",
    ),
    "cache-schema": (
        ("components", "headers", "NoStore", "schema", "const"),
        "public",
        "NoStore",
    ),
    "cache-required": (
        ("components", "headers", "NoStore", "required"),
        False,
        "NoStore",
    ),
    "cookie-schema": (
        ("components", "headers", "SessionCookie", "schema", "type"),
        "integer",
        "SessionCookie",
    ),
    "cookie-unconditional": (
        ("components", "headers", "SessionCookie", "required"),
        True,
        "SessionCookie",
    ),
    "cookie-cli": (
        ("components", "headers", "SessionCookie", "x-mimir-cookie", "forbidden_for"),
        "web",
        "SessionCookie",
    ),
    "cookie-security": (
        ("components", "headers", "SessionCookie", "x-mimir-cookie", "secure"),
        False,
        "SessionCookie",
    ),
    "cookie-lifetime": (
        ("components", "headers", "SessionCookie", "x-mimir-cookie", "max_age"),
        "unbounded",
        "SessionCookie",
    ),
    "logout-expiry-policy": (
        ("components", "headers", "ExpiredSessionCookie", "x-mimir-cookie", "max_age"),
        None,
        "ExpiredSessionCookie",
    ),
    "logout-expiry-description": (
        ("components", "headers", "ExpiredSessionCookie", "description"),
        "Web logout cookie has Max-Age but no expiration requirement.",
        "ExpiredSessionCookie",
    ),
}


def mutate_auth(api: dict[str, Any], name: str) -> str:
    path, value, reason = AUTH_MUTATIONS[name]
    node = api
    for part in path[:-1]:
        node = node[part]
    if value is None:
        node.pop(path[-1])
    else:
        node[path[-1]] = value
    return "authentication " + reason


def write_api(directory: Path, api: dict[str, Any]) -> None:
    """Deliberately refresh the mutated artifact hash, not the real candidate."""
    path = directory / "openapi.yaml"
    path.write_text(yaml.safe_dump(api, sort_keys=False))
    manifest_path = directory / "manifest.json"
    manifest = read_json(manifest_path)
    manifest["files"]["openapi.yaml"] = hashlib.sha256(path.read_bytes()).hexdigest()
    manifest_path.write_text(json.dumps(manifest, indent=2) + "\n")


class RemediationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.checker = ContractChecker(ROOT / "contracts")
        self.checker.load()
        self.cases = read_json(ROOT / "contracts/fixtures/discovery-cases.json")

    def test_r1_public_discovery_transcripts(self) -> None:
        for case in self.cases:
            with self.subTest(case=case["name"]):
                requests = ConversationDiscovery(self.checker).check(case)
                self.assertEqual(requests, case["expected_requests"])
                self.assertTrue(all(request.startswith("GET ") for request in requests))

    def test_r1_detail_required_nullable_strict_and_summary_unchanged(self) -> None:
        positive = self.cases[0]["response"]
        self.checker.validate(positive, "#/components/schemas/ConversationDetail")
        self.checker.validate(
            {**positive, "active_run_id": None},
            "#/components/schemas/ConversationDetail",
        )
        for fault in (
            "omitted",
            "malformed",
            "integer",
            "boolean",
            "object",
            "unexpected",
        ):
            with self.subTest(fault=fault):
                body = copy.deepcopy(positive)
                if fault == "omitted":
                    del body["active_run_id"]
                elif fault == "unexpected":
                    body["nonterminal_run"] = True
                else:
                    body["active_run_id"] = {
                        "malformed": "bad-id",
                        "integer": 1,
                        "boolean": False,
                        "object": {},
                    }[fault]
                with self.assertRaises(ContractError):
                    self.checker.validate(
                        body, "#/components/schemas/ConversationDetail"
                    )
        with self.assertRaises(ContractError):
            self.checker.validate(positive, "#/components/schemas/Conversation")

    def test_r1_previous_missing_discovery_contract_fails(self) -> None:
        self.checker.api["paths"]["/api/v1/conversations/{id}"]["get"]["responses"][
            "200"
        ]["content"]["application/json"]["schema"] = {
            "$ref": "#/components/schemas/Conversation"
        }
        with self.assertRaisesRegex(ContractError, "discovery response"):
            self.checker.check_discovery()
        self.checker.api["components"]["schemas"].pop("ConversationDetail")
        with self.assertRaises(ContractError):
            ConversationDiscovery(self.checker).check(self.cases[0])

    def test_r1_eligibility_denial_and_recovery_regressions(self) -> None:
        for original in self.cases:
            with self.subTest(case=original["name"]):
                case = copy.deepcopy(original)
                if case["http_status"] != 200:
                    # Denials/failures cannot become successful null discovery.
                    case["http_status"] = 200
                    case["response"] = {
                        **case["snapshot"]["conversation"]["body"],
                        "active_run_id": None,
                    }
                elif case["response"]["active_run_id"] is None:
                    case["response"]["active_run_id"] = self.cases[0]["response"][
                        "active_run_id"
                    ]
                else:
                    case["response"]["active_run_id"] = None
                with self.assertRaises(ContractError):
                    ConversationDiscovery(self.checker).check(case)
        for name in ("completion-before-status", "completion-before-attachment"):
            case = copy.deepcopy(next(c for c in self.cases if c["name"] == name))
            case["history_response"]["items"] = []
            with self.assertRaisesRegex(ContractError, "canonical committed history"):
                ConversationDiscovery(self.checker).check(case)

    def test_r2_exact_reviewer_reproduction_both_keysets(self) -> None:
        for fields in (("created_at", "project_id"), ("updated_at", "conversation_id")):
            with self.subTest(fields=fields):
                old = {
                    fields[0]: "2026-11-20T00:00:00Z",
                    fields[1]: "0199a1b2-c3d4-7e8f-9123-000000000001",
                }
                new = {
                    fields[0]: "2026-11-20T00:00:00.5Z",
                    fields[1]: "0199a1b2-c3d4-7e8f-9123-000000000002",
                }
                rows = [old, new]
                self.assertEqual(ordered_page(rows, fields, 2), [new, old])
                boundary = [new[field] for field in fields]
                self.assertEqual(ordered_page(rows, fields, 2, boundary), [old])
                self.assertEqual(ordered_page([old], fields, 2, boundary), [old])

    def test_r2_precision_uuid_and_exclusive_traversal(self) -> None:
        for fields in (("created_at", "project_id"), ("updated_at", "conversation_id")):
            with self.subTest(fields=fields):
                # Lexical UUID casing differs; sub-microsecond differences must survive.
                ident_a = "0199A1B2-C3D4-7E8F-9123-00000000000A"
                ident_b = "0199a1b2-c3d4-7e8f-9123-00000000000b"
                entries = [
                    ("2026-11-20T00:00:00Z", ident_a),
                    ("2026-11-20T00:00:00.000000000Z", ident_b),
                    ("2026-11-20T00:00:00.100000001Z", ident_a),
                    ("2026-11-20T00:00:00.1Z", ident_b),
                    ("2026-11-20T00:00:00.1000Z", ident_a),
                    ("2026-11-20T00:00:00.09Z", ident_a),
                ]
                rows = [{fields[0]: time, fields[1]: ident} for time, ident in entries]
                expected = [rows[i] for i in (2, 3, 4, 5, 1, 0)]
                self.assertEqual(ordered_page(rows, fields, 10), expected)
                boundary = [rows[3][field] for field in fields]
                # Equivalent timestamp/UUID spelling must be exactly the boundary.
                boundary[0] = "2026-11-20T00:00:00.100000000Z"
                boundary[1] = boundary[1].upper()
                self.assertEqual(ordered_page(rows, fields, 10, boundary), expected[2:])
                remaining = [row for row in rows if row is not rows[3]]
                self.assertEqual(
                    ordered_page(remaining, fields, 10, boundary), expected[2:]
                )
                for precision in ("0", "000", "0000000000"):
                    self.assertEqual(
                        chronological_key("2026-11-20T00:00:00Z", ident_a),
                        chronological_key(
                            "2026-11-20T00:00:00." + precision + "Z", ident_a.lower()
                        ),
                    )
                self.assertEqual(
                    chronological_key("2026-11-20T00:00:00.5Z", ident_a),
                    chronological_key("2026-11-20T01:00:00.500+01:00", ident_a),
                )

    def test_r3_authentication_mutations_fail_after_checksum_refresh(self) -> None:
        import shutil

        for name in AUTH_MUTATIONS:
            with self.subTest(mutation=name), tempfile.TemporaryDirectory() as tmp:
                directory = Path(tmp) / "contracts"
                shutil.copytree(ROOT / "contracts", directory)
                checker = ContractChecker(directory)
                checker.load()
                reason = mutate_auth(checker.api, name)
                write_api(directory, checker.api)
                checker.load()
                checker.check_manifest()  # prove failure is not hash integrity
                with self.assertRaisesRegex(ContractError, reason):
                    checker.check()
