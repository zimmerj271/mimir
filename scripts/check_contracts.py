"""S01 offline contract validation. Never changes artifacts or approves a baseline.

JSON/OpenAPI are intentionally dynamic at this validation boundary. Library
imports lack typing metadata; narrow import exceptions do not cover owned code.
Errors identify the artifact/check, never echo payloads or credentials.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from collections.abc import Hashable, Mapping
from pathlib import Path, PurePosixPath
from typing import Any, cast

import yaml  # type: ignore[import-untyped]
from jsonschema import Draft202012Validator, FormatChecker  # type: ignore[import-untyped]
from openapi_spec_validator import OpenAPIV31SpecValidator

ROOT = Path(__file__).resolve().parents[1]
METHODS = {"get", "post", "put", "patch", "delete", "options", "head", "trace"}
TERMINAL = {"completed", "failed", "cancelled", "interrupted"}
VERSION = "1.0.0"
AUTH_POLICY = {
    "session_model": "opaque",
    "transports": {"cli": "bearerAuth", "web": "cookieAuth"},
    "mutually_exclusive": True,
    "stored_client_type_binding": True,
    "idle_days": 30,
    "absolute_days": 90,
    "implicit_renewal": True,
    "refresh_endpoint": False,
    "csrf_header": "X-Mimir-CSRF",
    "csrf_value": "1",
    "browser_https_required": True,
    "exact_trusted_origin": True,
    "trusted_host_required": True,
    "credentialed_cross_origin": False,
}
BROWSER_REQUIREMENTS = {
    "BrowserCSRF": {
        "clients": ["web"],
        "mutations": "required",
        "authenticated_get": "not_required",
        "cli": "not_required",
    },
    "BrowserOrigin": {
        "clients": ["web"],
        "mutations": "exact_configured_origin_required",
        "authenticated_get": "reject_unauthorized_if_present",
        "trust_from_host_or_forwarded": False,
    },
}
BROWSER_DESCRIPTIONS = {
    "BrowserCSRF": "Required with exact configured Origin for browser mutations including web login/logout. CLI bearer needs no CSRF. Failure is 403 forbidden. Conditional requirement cannot be enforced by security alternatives.",
    "BrowserOrigin": "Browser mutations require exact configured trusted origin; reject missing/null/malformed/unauthorized. Authenticated GET rejects unauthorized Origin if present. Host/forwarded headers do not establish trust.",
}
COOKIE_DESCRIPTIONS = {
    "SessionCookie": "Web login only; required for web, absent for CLI. __Host-mimir_session with HttpOnly; Secure; SameSite=Strict; Path=/; no Domain; Max-Age bounded by remaining absolute lifetime. Never include cookie values in examples.",
    "ExpiredSessionCookie": "Web logout expires __Host-mimir_session with Max-Age=0 and matching Path=/; HttpOnly; Secure; SameSite=Strict; no Domain. CLI logout has no session cookie.",
}
COOKIE_POLICIES = {
    name: {
        "name": "__Host-mimir_session",
        "http_only": True,
        "secure": True,
        "same_site": "Strict",
        "path": "/",
        "domain": None,
        "required_for": "web",
        "forbidden_for": "cli",
        "transport": "https",
        "max_age": "positive_bounded_by_remaining_absolute_seconds"
        if name == "SessionCookie"
        else 0,
    }
    for name in COOKIE_DESCRIPTIONS
}
ERROR_CODES = {
    "unauthenticated": 401,
    "forbidden": 403,
    "not_found": 404,
    "project_not_empty": 409,
    "conversation_version_conflict": 409,
    "idempotency_key_conflict": 409,
    "active_run_conflict": 409,
    "invalid_run_transition": 409,
    "operation_expired": 410,
    "operation_unknown": 404,
    "context_budget_exceeded": 422,
    "invalid_request": 422,
    "payload_too_large": 413,
    "rate_limited": 429,
    "dependency_unavailable": 503,
    "backend_timeout": 504,
    "ambiguous_credentials": 400,
}
# An intentional public-surface change requires a reviewed contract/version PR.
EXPECTED_OPERATIONS: dict[str, tuple[str, str]] = {
    "login": ("post", "/api/v1/auth/login"),
    "getSession": ("get", "/api/v1/auth/session"),
    "logout": ("post", "/api/v1/auth/logout"),
    "getMe": ("get", "/api/v1/users/me"),
    "patchMe": ("patch", "/api/v1/users/me"),
    "listProjects": ("get", "/api/v1/projects"),
    "createProject": ("post", "/api/v1/projects"),
    "getProject": ("get", "/api/v1/projects/{id}"),
    "patchProject": ("patch", "/api/v1/projects/{id}"),
    "deleteProject": ("delete", "/api/v1/projects/{id}"),
    "listConversations": ("get", "/api/v1/conversations"),
    "createConversation": ("post", "/api/v1/conversations"),
    "getConversation": ("get", "/api/v1/conversations/{id}"),
    "patchConversation": ("patch", "/api/v1/conversations/{id}"),
    "deleteConversation": ("delete", "/api/v1/conversations/{id}"),
    "listMessages": ("get", "/api/v1/conversations/{id}/messages"),
    "submitMessage": ("post", "/api/v1/conversations/{id}/messages"),
    "createBranch": ("post", "/api/v1/conversations/{id}/branches"),
    "submitStandalone": ("post", "/api/v1/runs/standalone"),
    "getRun": ("get", "/api/v1/runs/{run_id}"),
    "subscribeRun": ("get", "/api/v1/runs/{run_id}/events"),
    "cancelRun": ("post", "/api/v1/runs/{run_id}/cancel"),
    "lookupOperation": ("get", "/api/v1/operations/{operation_type}/{idempotency_key}"),
    "listModels": ("get", "/api/v1/models"),
    "healthLive": ("get", "/api/v1/health/live"),
    "healthReady": ("get", "/api/v1/health/ready"),
    "healthDependencies": ("get", "/api/v1/health/dependencies"),
    "getApiMetadata": ("get", "/api/v1/meta"),
}


class ContractError(ValueError):
    """Redacted, deterministic contract-check failure."""


def require(condition: object, label: str) -> None:
    if not condition:
        raise ContractError(label)


def unique_pairs(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        require(key not in result, "duplicate JSON/YAML mapping key")
        result[key] = value
    return result


class UniqueLoader(yaml.SafeLoader):  # type: ignore[misc]
    """Preserve safe YAML alias support while rejecting duplicate mapping keys."""

    def construct_mapping(self, node: Any, deep: bool = False) -> dict[str, Any]:
        self.flatten_mapping(node)
        return unique_pairs(
            [
                (
                    self.construct_object(key, deep=deep),
                    self.construct_object(value, deep=deep),
                )
                for key, value in node.value
            ]
        )


def read_json(path: Path) -> Any:
    try:
        return json.loads(
            path.read_text(),
            object_pairs_hook=unique_pairs,
            parse_constant=lambda _: (_ for _ in ()).throw(
                ContractError("nonfinite JSON")
            ),
        )
    except ValueError, OSError:
        raise ContractError(f"invalid JSON: {path.name}") from None


def walk(value: Any) -> Any:
    yield value
    if isinstance(value, dict):
        for child in value.values():
            yield from walk(child)
    elif isinstance(value, list):
        for child in value:
            yield from walk(child)


class ContractChecker:
    def __init__(self, directory: Path) -> None:
        require(not directory.is_symlink(), "contract directory symlink forbidden")
        self.directory = directory.resolve()
        self.api: dict[str, Any] = {}
        self.sse: dict[str, Any] = {}
        self.errors: dict[str, Any] = {}

    def load(self) -> None:
        try:
            self.api = yaml.load(
                (self.directory / "openapi.yaml").read_text(), Loader=UniqueLoader
            )
        except yaml.YAMLError, ValueError, OSError:
            raise ContractError("invalid OpenAPI YAML") from None
        require(isinstance(self.api, dict), "OpenAPI must be a mapping")
        self.sse = read_json(self.directory / "sse.schema.json")
        self.errors = read_json(self.directory / "errors.json")
        require(
            isinstance(self.sse, dict) and isinstance(self.errors, dict),
            "schema/catalog must be mappings",
        )

    @staticmethod
    def pointer(document: Any, reference: str) -> Any:
        # This initial canonical baseline uses document-local JSON Pointers only.
        # Reject before calling libraries, so no URI can cause network retrieval.
        require(
            reference.startswith("#/"), "nonlocal reference forbidden in this baseline"
        )
        value = document
        try:
            for part in reference[2:].split("/"):
                key = part.replace("~1", "/").replace("~0", "~")
                value = value[int(key)] if isinstance(value, list) else value[key]
        except KeyError, IndexError, TypeError, ValueError:
            raise ContractError("unresolved reference") from None
        return value

    def check_references(self, document: dict[str, Any]) -> None:
        for node in walk(document):
            if isinstance(node, dict):
                for key in ("$ref", "$dynamicRef"):
                    if key in node:
                        require(isinstance(node[key], str), "reference must be string")
                        self.pointer(document, node[key])
                mapping = node.get("discriminator", {}).get("mapping", {})
                for reference in mapping.values():
                    self.pointer(document, reference)

    def check_schemas(self) -> None:
        require(self.api.get("openapi") == "3.1.0", "OpenAPI 3.1.0 required")
        require(
            self.sse.get("$schema") == "https://json-schema.org/draft/2020-12/schema",
            "SSE must use Draft 2020-12",
        )
        self.check_references(self.api)
        self.check_references(self.sse)
        try:
            OpenAPIV31SpecValidator(cast(Mapping[Hashable, Any], self.api)).validate()
            Draft202012Validator.check_schema(self.sse)
            for schema in self.api["components"]["schemas"].values():
                Draft202012Validator.check_schema(schema)
        except Exception:
            # Third-party boundary: exceptions can embed entire private payloads.
            raise ContractError(
                "OpenAPI/JSON Schema semantic validation failed"
            ) from None

    def validate(self, instance: Any, reference: str) -> None:
        try:
            schema = (
                self.sse
                if reference == "sse"
                else {
                    "$ref": reference,
                    "components": self.api["components"],
                    "paths": self.api["paths"],
                }
            )
            if reference != "sse":
                self.pointer(self.api, reference)
            Draft202012Validator(schema, format_checker=FormatChecker()).validate(
                instance
            )
        except Exception:
            raise ContractError("fixture/schema validation failed") from None
        if reference != "sse":
            self.check_payload_semantics(instance, self.pointer(self.api, reference))
        self.check_bytes(instance)
        for node in walk(instance):
            if isinstance(node, dict) and node.get("error_code") is not None:
                require(
                    node["error_code"] in self.errors["codes"],
                    "unknown typed error code",
                )

    def check_payload_semantics(self, instance: Any, schema: Any) -> None:
        # Apply cross-field rules to the actual canonical type, including HTTP
        # response references and examples. Open Error.details is not a Run just
        # because it happens to contain similarly named fields.
        if schema is self.api["components"]["schemas"]["Run"] or (
            isinstance(schema, dict)
            and schema.get("$ref") == "#/components/schemas/Run"
        ):
            self.check_run_lifecycle(instance)

    @staticmethod
    def check_run_lifecycle(run: dict[str, Any]) -> None:
        from contract_protocols import chronological_key

        if run["completed_at"] is not None:
            require(
                chronological_key(run["completed_at"], run["run_id"])[0]
                >= chronological_key(run["created_at"], run["run_id"])[0],
                "Run completed_at precedes created_at",
            )

    @staticmethod
    def check_bytes(value: Any) -> None:
        # These fields carry canonical/provisional input or output, never truncate.
        for node in walk(value):
            if isinstance(node, dict):
                for field in ("text", "prompt", "final_text", "checkpoint_text"):
                    text = node.get(field)
                    if isinstance(text, str):
                        try:
                            require(
                                len(text.encode("utf-8")) <= 1048576,
                                "UTF-8 byte limit exceeded",
                            )
                        except UnicodeEncodeError:
                            raise ContractError("invalid UTF-8 text") from None

    def operations(self) -> dict[str, tuple[str, str]]:
        result: dict[str, tuple[str, str]] = {}
        for path, item in self.api["paths"].items():
            for method, op in item.items():
                if method not in METHODS:
                    continue
                opid = op.get("operationId")
                require(
                    isinstance(opid, str) and opid not in result,
                    "missing/duplicate operationId",
                )
                result[opid] = (method, path)
        return result

    def check_semantics(self) -> None:
        require(
            self.operations() == EXPECTED_OPERATIONS,
            "unapproved public operation surface",
        )
        require(
            self.api.get("x-mimir-authentication") == AUTH_POLICY,
            "authentication policy drift",
        )
        schemes = self.api["components"]["securitySchemes"]
        require(
            schemes["bearerAuth"]["type"] == "http"
            and schemes["bearerAuth"]["scheme"] == "bearer",
            "bearer scheme drift",
        )
        require(
            {key: schemes["cookieAuth"][key] for key in ("type", "in", "name")}
            == {"type": "apiKey", "in": "cookie", "name": "__Host-mimir_session"},
            "cookie scheme drift",
        )
        schemas = self.api["components"]["schemas"]
        self.check_discovery()
        self.check_authentication_headers()
        require(
            schemas["Message"]["properties"]["status"]["enum"] == ["completed"],
            "canonical message status drift",
        )
        require(
            self.errors.get("codes") == ERROR_CODES, "error code/status catalog drift"
        )
        require(
            self.errors.get("shape") == "#/components/schemas/Error",
            "error shape drift",
        )
        require(
            set(schemas["Error"]["properties"]["error"]["properties"]["code"]["enum"])
            == set(ERROR_CODES),
            "schema/catalog error mismatch",
        )
        public = {"login", "getApiMetadata", "healthLive", "healthReady"}
        for opid, (method, path) in self.operations().items():
            operation = self.api["paths"][path][method]
            require("refresh" not in path.lower(), "refresh route forbidden")
            expected_security: list[dict[str, list[str]]] = (
                [] if opid in public else [{"bearerAuth": []}, {"cookieAuth": []}]
            )
            require(
                operation.get("security", []) == expected_security,
                "credential alternatives drift",
            )
            for status, response in operation["responses"].items():
                if status.isdigit() and int(status) >= 400:
                    require(
                        response["content"]["application/json"]["schema"]
                        == {"$ref": "#/components/schemas/Error"},
                        "typed error envelope drift",
                    )
            if method in {"post", "patch", "delete", "put"}:
                parameters = operation.get("parameters", [])
                for name in ("BrowserCSRF", "BrowserOrigin"):
                    require(
                        {"$ref": f"#/components/parameters/{name}"} in parameters,
                        "conditional browser header missing",
                    )
            if "requestBody" in operation:
                require(
                    set(operation["requestBody"]["content"]) == {"application/json"},
                    "JSON content-type required",
                )
        for opid, limit, maximum, policy in (
            ("listProjects", 50, 200, "projects"),
            ("listConversations", 50, 200, "conversations"),
            ("listMessages", 100, 500, "messages"),
        ):
            method, path = self.operations()[opid]
            op = self.api["paths"][path][method]
            require(op.get("x-mimir-pagination") == policy, "pagination policy missing")
            param = next(p for p in op["parameters"] if p.get("name") == "limit")
            require(
                param["schema"]
                == {
                    "type": "integer",
                    "minimum": 1,
                    "maximum": maximum,
                    "default": limit,
                },
                "pagination limits drift",
            )
            require("422" in op["responses"], "cursor error response missing")
            expected_names = (
                {"cursor", "limit", "id"}
                if policy == "messages"
                else {"cursor", "limit"}
            )
            require(
                {p.get("name") for p in op["parameters"]} == expected_names,
                "unapproved pagination filter/sort parameter",
            )
        cancel = self.api["paths"]["/api/v1/runs/{run_id}/cancel"]["post"]
        for status in ("200", "202"):
            require(
                cancel["responses"][status]["content"]["application/json"]["schema"]
                == {"$ref": "#/components/schemas/Run"},
                "cancellation response drift",
            )
        require(
            not any(p.get("name") == "Idempotency-Key" for p in cancel["parameters"]),
            "cancel must not require operation key",
        )
        require(
            "202"
            in self.api["paths"]["/api/v1/conversations/{id}"]["delete"]["responses"],
            "deletion must be 202",
        )
        require(
            "204"
            not in self.api["paths"]["/api/v1/conversations/{id}"]["delete"][
                "responses"
            ],
            "synchronous deletion forbidden",
        )
        lookup = self.api["paths"][
            "/api/v1/operations/{operation_type}/{idempotency_key}"
        ]["get"]
        require(
            {"200", "404", "410"} <= lookup["responses"].keys(),
            "operation lookup distinction missing",
        )
        login = self.api["paths"]["/api/v1/auth/login"]["post"]
        require(
            login["responses"]["201"]["content"]["application/json"]["schema"]
            == {
                "oneOf": [
                    {"$ref": "#/components/schemas/CliLoginResponse"},
                    {"$ref": "#/components/schemas/WebLoginResponse"},
                ]
            },
            "login discriminator agreement drift",
        )
        require(
            schemas["MessageContent"]["properties"]["text"].get("x-max-utf8-bytes")
            == 1048576,
            "message UTF-8 bound missing",
        )
        require(
            schemas["StandaloneSubmit"]["properties"]["prompt"].get("x-max-utf8-bytes")
            == 1048576,
            "standalone UTF-8 bound missing",
        )
        for ident in ("S01-AUTH", "S01-CANCEL", "S01-PAGE", "S01-DISCOVERY"):
            require(
                ident in (self.directory / "HTTP_SEMANTICS.md").read_text(),
                "approved addendum missing",
            )

    def check_discovery(self) -> None:
        schemas = self.api["components"]["schemas"]
        summary = schemas["Conversation"]
        detail = schemas.get("ConversationDetail", {})
        require(
            self.api["paths"]["/api/v1/conversations/{id}"]["get"]["responses"]["200"][
                "content"
            ]["application/json"]["schema"]
            == {"$ref": "#/components/schemas/ConversationDetail"},
            "conversation discovery response must reference ConversationDetail",
        )
        require(
            "active_run_id" not in summary["properties"]
            and detail.get("type") == "object"
            and detail.get("additionalProperties") is False
            and detail.get("required") == summary["required"] + ["active_run_id"]
            and detail.get("properties")
            == {
                **summary["properties"],
                "active_run_id": {
                    "type": ["string", "null"],
                    "format": "uuid",
                    "readOnly": True,
                    "description": "S01-DISCOVERY: eligible nonterminal conversational run at the same read snapshot, or null; always present. Derived reference; no canonical version/timestamp update.",
                },
            },
            "conversation discovery strict schema/required nullable UUID drift",
        )
        for opid in ("createConversation", "patchConversation", "createBranch"):
            method, path = self.operations()[opid]
            responses = self.api["paths"][path][method]["responses"]
            status = "201" if opid != "patchConversation" else "200"
            require(
                responses[status]["content"]["application/json"]["schema"]
                == {"$ref": "#/components/schemas/Conversation"},
                "conversation summary response changed",
            )
        require(
            schemas["ListConversations"]["properties"]["items"]["items"]
            == {"$ref": "#/components/schemas/Conversation"},
            "conversation listing summary changed",
        )

    def check_authentication_headers(self) -> None:
        for name, header_name, schema in (
            ("BrowserCSRF", "X-Mimir-CSRF", {"type": "string", "const": "1"}),
            ("BrowserOrigin", "Origin", {"type": "string"}),
        ):
            parameter = self.pointer(self.api, f"#/components/parameters/{name}")
            require(
                parameter.get("name") == header_name
                and parameter.get("in") == "header"
                and parameter.get("required") is False
                and parameter.get("schema") == schema
                and parameter.get("description") == BROWSER_DESCRIPTIONS[name]
                and parameter.get("x-mimir-browser-requirement")
                == BROWSER_REQUIREMENTS[name],
                f"authentication {name} definition/conditional requirement drift",
            )
        for opid, status, cookie in (
            ("login", "201", "SessionCookie"),
            ("getSession", "200", None),
            ("logout", "204", "ExpiredSessionCookie"),
        ):
            method, path = self.operations()[opid]
            headers = self.api["paths"][path][method]["responses"][status].get(
                "headers", {}
            )
            require(
                headers.get("Cache-Control")
                == {"$ref": "#/components/headers/NoStore"},
                f"authentication {opid} Cache-Control wiring drift",
            )
            if cookie:
                require(
                    headers.get("Set-Cookie")
                    == {"$ref": f"#/components/headers/{cookie}"},
                    f"authentication {opid} Set-Cookie wiring drift",
                )
                definition = self.pointer(self.api, headers["Set-Cookie"]["$ref"])
                require(
                    definition.get("schema") == {"type": "string"}
                    and definition.get("required", False) is False
                    and definition.get("x-mimir-cookie") == COOKIE_POLICIES[cookie]
                    and definition.get("description") == COOKIE_DESCRIPTIONS[cookie],
                    f"authentication {cookie} issuance/security/expiry agreement drift",
                )
        no_store = self.pointer(self.api, "#/components/headers/NoStore")
        require(
            no_store.get("schema") == {"type": "string", "const": "no-store"}
            and no_store.get("required") is True,
            "authentication NoStore header drift",
        )

    def check_manifest(self) -> None:
        manifest = read_json(self.directory / "manifest.json")
        require(
            manifest.get("schema_version") == 1
            and manifest.get("hash_algorithm") == "sha256",
            "manifest format drift",
        )
        require(
            manifest.get("status") == "candidate_pending_human_approval",
            "checker cannot approve candidate",
        )
        require(
            set(manifest)
            == {
                "schema_version",
                "status",
                "contract_version",
                "hash_algorithm",
                "files",
            },
            "manifest keys drift",
        )
        actual: set[str] = set()
        for path in self.directory.rglob("*"):
            require(not path.is_symlink(), "contract symlink forbidden")
            if path.is_file() and path != self.directory / "manifest.json":
                actual.add(path.relative_to(self.directory).as_posix())
        files = manifest["files"]
        require(isinstance(files, dict), "manifest files must be mapping")
        for name, digest in files.items():
            require(
                isinstance(name, str) and isinstance(digest, str),
                "invalid manifest entry",
            )
            parts = PurePosixPath(name)
            require(
                not parts.is_absolute()
                and parts.as_posix() == name
                and ".." not in parts.parts
                and "\\" not in name
                and name != "manifest.json",
                "unsafe manifest path",
            )
            require(re.fullmatch("[0-9a-f]{64}", digest), "invalid sha256")
        require(set(files) == actual, "manifest membership mismatch")
        for name, digest in files.items():
            require(
                hashlib.sha256((self.directory / name).read_bytes()).hexdigest()
                == digest,
                "raw-byte checksum mismatch",
            )
        versions = [
            (self.directory / "VERSION").read_text().strip(),
            manifest["contract_version"],
            self.api["info"]["version"],
            self.errors["version"],
            self.sse["x-contract-version"],
        ]
        require(all(v == VERSION for v in versions), "contract version mismatch")
        require(
            all(
                case["properties"]["event_version"] == {"const": 1}
                for case in self.sse["oneOf"]
            ),
            "SSE major version drift",
        )

    def check_fixtures(self) -> None:
        from contract_protocols import check_scenarios

        index = read_json(self.directory / "fixtures/index.json")
        require(
            isinstance(index, dict) and set(index) == {"fixtures"},
            "fixture index format",
        )
        seen: set[str] = set()
        events: set[str] = set()
        for entry in index["fixtures"]:
            name = entry["path"]
            require(
                name not in seen and PurePosixPath(name).name == name,
                "duplicate/unsafe fixture path",
            )
            seen.add(name)
            value = read_json(self.directory / "fixtures" / name)
            for node in walk(value):
                if isinstance(node, dict):
                    require(
                        not {
                            "password",
                            "access_token",
                            "Authorization",
                            "Cookie",
                            "Set-Cookie",
                        }
                        & node.keys(),
                        "fixture contains credential field/value",
                    )
            if entry.get("schema"):
                self.validate(value, entry["schema"])
                if entry["schema"] == "sse":
                    events.add(value["event"])
            else:
                check_scenarios(self, entry["kind"], value)
        actual = {
            p.name
            for p in (self.directory / "fixtures").iterdir()
            if p.is_file() and p.name != "index.json"
        }
        require(seen == actual, "fixture index membership mismatch")
        require(
            events
            == {case["properties"]["event"]["const"] for case in self.sse["oneOf"]},
            "all eight SSE event fixtures required",
        )

    def check_examples(self) -> None:
        for node in walk(self.api):
            if not isinstance(node, dict):
                continue
            if "schema" in node:
                schema = {**node["schema"], "components": self.api["components"]}
                examples = [node["example"]] if "example" in node else []
                examples += [
                    v["value"]
                    for v in node.get("examples", {}).values()
                    if isinstance(v, dict) and "value" in v
                ]
                for value in examples:
                    try:
                        Draft202012Validator(
                            schema, format_checker=FormatChecker()
                        ).validate(value)
                    except Exception:
                        raise ContractError(
                            "invalid documented media example"
                        ) from None
                    self.check_payload_semantics(value, node["schema"])
            if "type" in node or "$ref" in node:
                values = node.get("examples", [])
                if isinstance(values, list):
                    for value in values:
                        try:
                            Draft202012Validator(
                                {**node, "components": self.api["components"]},
                                format_checker=FormatChecker(),
                            ).validate(value)
                        except Exception:
                            raise ContractError(
                                "invalid documented schema example"
                            ) from None
                        self.check_payload_semantics(value, node)

    def check(self) -> None:
        self.load()
        self.check_schemas()
        self.check_semantics()
        self.check_examples()
        self.check_manifest()
        self.check_fixtures()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--directory", type=Path, default=ROOT / "contracts")
    args = parser.parse_args()
    try:
        checker = ContractChecker(args.directory)
        checker.check()
    except ContractError as exc:
        print(f"FAIL: contract validation: {exc}", file=sys.stderr)
        return 1
    except KeyError, TypeError, OSError, StopIteration, AttributeError:
        print(
            "FAIL: contract validation: invalid artifact structure (no payloads logged)",
            file=sys.stderr,
        )
        return 1
    print(
        f"PASS: candidate {VERSION}; OpenAPI 3.1, Draft 2020-12, references, surface, errors, fixtures, protocols and raw-byte manifest; human approval pending"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
