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
                else {"$ref": reference, "components": self.api["components"]}
            )
            if reference != "sse":
                self.pointer(self.api, reference)
            Draft202012Validator(schema, format_checker=FormatChecker()).validate(
                instance
            )
        except Exception:
            raise ContractError("fixture/schema validation failed") from None
        self.check_bytes(instance)
        for node in walk(instance):
            if isinstance(node, dict) and node.get("error_code") is not None:
                require(
                    node["error_code"] in self.errors["codes"],
                    "unknown typed error code",
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
        for opid, status in (
            ("login", "201"),
            ("getSession", "200"),
            ("logout", "204"),
        ):
            method, path = self.operations()[opid]
            headers = self.api["paths"][path][method]["responses"][status]["headers"]
            require(
                headers["Cache-Control"] == {"$ref": "#/components/headers/NoStore"},
                "no-store missing",
            )
        require(
            self.api["components"]["headers"]["NoStore"]["schema"]
            == {"type": "string", "const": "no-store"},
            "no-store header drift",
        )
        for name in ("SessionCookie", "ExpiredSessionCookie"):
            description = self.api["components"]["headers"][name]["description"]
            for term in (
                "__Host-mimir_session",
                "HttpOnly",
                "Secure",
                "SameSite=Strict",
                "Path=/",
                "no Domain",
                "Max-Age",
            ):
                require(term in description, "cookie scope/security drift")
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
        for ident in ("S01-AUTH", "S01-CANCEL", "S01-PAGE"):
            require(
                ident in (self.directory / "HTTP_SEMANTICS.md").read_text(),
                "approved addendum missing",
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
