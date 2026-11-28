"""Tokenless contract-agreement harnesses, never HTTP/runtime implementations.

These reference models exercise fixture expectations. They cannot establish real
CSRF enforcement, cryptographic cursor integrity, SQL atomicity or CLI behavior.
"""

from __future__ import annotations

import json
import re
from datetime import UTC, datetime
from fractions import Fraction
from typing import TYPE_CHECKING, Any
from urllib.parse import urlencode
from uuid import UUID

from check_contracts import TERMINAL, ContractError, require, unique_pairs

if TYPE_CHECKING:
    from check_contracts import ContractChecker


class SSETranscript:
    def __init__(self, checker: ContractChecker) -> None:
        self.checker = checker
        self.status: str | None = None
        self.run_id: str | None = None
        self.sequence = 0
        self.text = ""
        self.terminal_events = 0

    def attach(self, snapshot: dict[str, Any]) -> None:
        self.checker.validate(snapshot, "sse")
        require(
            snapshot["event"] == "run.snapshot", "attachment must begin with snapshot"
        )
        self.status = snapshot["status"]
        self.run_id = snapshot["run_id"]
        self.sequence = snapshot["snapshot_sequence"]
        self.text = snapshot["text"]  # replace, never append to old provisional text
        self.terminal_events = 0

    def frame(self, frame: str) -> None:
        lines = frame.splitlines()
        if lines and all(line.startswith(":") or line == "" for line in lines):
            return  # heartbeat comment never consumes text sequence
        event_lines = [line[6:].lstrip() for line in lines if line.startswith("event:")]
        data_lines = [line[5:].lstrip() for line in lines if line.startswith("data:")]
        require(
            len(event_lines) == len(data_lines) == 1,
            "one event and single-line JSON data required",
        )
        try:
            value = json.loads(data_lines[0], object_pairs_hook=unique_pairs)
        except ValueError:
            raise ContractError("invalid SSE JSON") from None
        self.checker.validate(value, "sse")
        require(event_lines[0] == value["event"], "SSE discriminator mismatch")
        self.event(value)

    def event(self, value: dict[str, Any]) -> None:
        self.checker.validate(value, "sse")
        event = value["event"]
        if self.status is None:
            self.attach(value)
            return
        require(value["run_id"] == self.run_id, "stream run identity mismatch")
        require(
            self.status not in TERMINAL,
            "no active events after terminal snapshot/event",
        )
        require(
            event != "run.snapshot", "new attachment required for replacement snapshot"
        )
        if event == "run.started":
            require(self.status == "pending", "run.started requires pending snapshot")
            self.status = "running"
        elif event == "message.delta":
            require(self.status == "running", "delta requires running state")
            require(
                value["sequence"] == self.sequence + 1,
                "gap/duplicate/reordered text sequence; reattach",
            )
            self.sequence = value["sequence"]
            self.text += value["text"]
        elif event == "run.usage":
            require(self.status == "running", "usage requires running state")
        else:
            state = event.removeprefix("run.")
            require(
                state in TERMINAL
                and (state != "completed" or self.status == "running"),
                "invalid terminal transition",
            )
            self.status = state
            self.terminal_events += 1
        self.checker.check_bytes({"text": self.text})


def cancellation(checker: ContractChecker, status: int, run: dict[str, Any]) -> None:
    checker.validate(run, "#/components/schemas/Run")
    if status == 202:
        require(
            run["status"] not in TERMINAL and run["cancellation_requested"] is True,
            "202 must be requested/nonterminal",
        )
    elif status == 200:
        require(
            run["status"] in TERMINAL and run["cancellation_requested"] is False,
            "200 must be unchanged terminal",
        )
    else:
        raise ContractError("invalid successful cancellation status")


def auth_outcome(case: dict[str, Any]) -> tuple[bool, int | None, str | None]:
    """Classify synthetic facts only; no headers, credentials, TLS or HTTP I/O."""
    authorization = case.get("authorization_count", 0)
    cookies = case.get("cookie_count", 0)
    if authorization > 1 or cookies > 1 or (authorization and cookies):
        return False, 400, "ambiguous_credentials"
    login = case.get("login", False)
    client = case.get("client_type") if login else case.get("stored_client_type")
    if login:
        if authorization or (client == "cli" and cookies):
            return False, 400, "ambiguous_credentials"
    elif (
        authorization + cookies != 1
        or not case.get("credential_valid", True)
        or ((authorization and client != "cli") or (cookies and client != "web"))
    ):
        return False, 401, "unauthenticated"
    browser = client == "web"
    if not case.get("trusted_host", True):
        return False, 403, "forbidden"
    if browser and not case.get("https", False):
        return False, 403, "forbidden"
    if (
        case.get("has_json_body", False)
        and case.get("content_type") != "application/json"
    ) or case.get("body_parse_error") in {"malformed_json", "invalid_utf8"}:
        return False, 422, "invalid_request"
    if browser:
        if case.get("mutation", False):
            if (
                case.get("origin") != "https://browser.invalid"
                or case.get("csrf") != "1"
            ):
                return False, 403, "forbidden"
        elif case.get("origin") not in (None, "https://browser.invalid"):
            return False, 403, "forbidden"
    return True, None, None


def chronological_key(timestamp: str, identity: str) -> tuple[Fraction, int]:
    """Exact instants, including sub-microsecond fractions, and UUID identities.

    datetime parses the integral second and offset only. Fraction retains every
    supplied fractional digit; neither float nor datetime precision truncates it.
    This is an agreement model, not a timestamp representation restriction.
    """
    match = re.fullmatch(
        r"(\d{4}-\d{2}-\d{2}[Tt]\d{2}:\d{2}:\d{2})(?:\.(\d+))?([Zz]|[+-]\d{2}:\d{2})",
        timestamp,
    )
    require(match is not None, "invalid keyset timestamp")
    assert match is not None
    try:
        instant = datetime.fromisoformat(match[1] + match[3].upper())
        elapsed = instant - datetime(1970, 1, 1, tzinfo=UTC)
        digits = match[2] or "0"
        seconds = Fraction(elapsed.days * 86400 + elapsed.seconds) + Fraction(
            int(digits), 10 ** len(digits)
        )
        return seconds, UUID(identity).int
    except ValueError, OverflowError:
        raise ContractError("invalid chronological keyset") from None


def ordered_page(
    rows: list[dict[str, Any]],
    fields: tuple[str, str],
    limit: int,
    boundary: list[str] | None = None,
) -> list[dict[str, Any]]:
    eligible = [
        r
        for r in rows
        if r.get("owned", True)
        and r.get("accessible", True)
        and not r.get("fenced", False)
    ]
    if boundary is not None:
        boundary_key = chronological_key(*boundary)
        eligible = [
            r
            for r in eligible
            if chronological_key(r[fields[0]], r[fields[1]]) < boundary_key
        ]
    return sorted(
        eligible,
        key=lambda r: chronological_key(r[fields[0]], r[fields[1]]),
        reverse=True,
    )[:limit]


class ConversationDiscovery:
    """Contract-only read/attach transcript; synthetic snapshot facts, no DB/HTTP.

    The client consumes only schema-validated public responses. Separate server
    facts check eligibility at one synthetic read snapshot, never proving RLS or
    SQL snapshot consistency. Requests are derived from public identities only.
    """

    def __init__(self, checker: ContractChecker) -> None:
        self.checker = checker

    def request(self, operation_id: str, identity: str) -> str:
        method, path = self.checker.operations()[operation_id]
        require(method == "get", "discovery/recovery never resubmits or cancels")
        operation = self.checker.api["paths"][path][method]
        for index, parameter in enumerate(operation["parameters"]):
            if parameter.get("in") == "path":
                escaped = path.replace("~", "~0").replace("/", "~1")
                self.checker.validate(
                    identity,
                    f"#/paths/{escaped}/{method}/parameters/{index}/schema",
                )
                return "GET " + path.replace("{" + parameter["name"] + "}", identity)
        raise ContractError("discovery request path parameter missing")

    def check(self, case: dict[str, Any]) -> list[str]:
        facts = case["snapshot"]
        conversation = facts["conversation"]
        conversation_id = conversation["body"]["conversation_id"]
        requests = []
        if "listing_pages" in case:
            method, path = self.checker.operations()["listConversations"]
            require(method == "get", "listing is read-only")
            cursor = None
            selected = None
            for page in case["listing_pages"]:
                requests.append(
                    "GET "
                    + path
                    + ("?" + urlencode({"cursor": cursor}) if cursor else "")
                )
                self.checker.validate(page, "#/components/schemas/ListConversations")
                selected = next(
                    (
                        row["conversation_id"]
                        for row in page["items"]
                        if row["status"] == "active"
                    ),
                    None,
                )
                if selected:
                    break
                cursor = page["next_cursor"]
                require(
                    cursor is not None, "no eligible selection at listing exhaustion"
                )
            require(
                selected == conversation_id,
                "latest eligible public conversation mismatch",
            )
        requests.append(self.request("getConversation", conversation_id))
        accessible = (
            conversation["exists"]
            and conversation["owner_id"] == case["authenticated_owner"]
            and conversation["accessible"]
            and not conversation["fenced"]
        )
        if not accessible or not facts.get("runtime_available", True):
            http_status = 404 if not accessible else 503
            code = "not_found" if http_status == 404 else "dependency_unavailable"
            self.checker.validate(case["response"], "#/components/schemas/Error")
            require(
                case["http_status"] == http_status
                and case["response"]["error"]["code"] == code
                and case["response"]["error"]["details"] == {},
                "discovery denial/dependency failure must not disclose run identity",
            )
            return requests
        self.checker.validate(conversation["body"], "#/components/schemas/Conversation")
        detail = case["response"]
        self.checker.validate(detail, "#/components/schemas/ConversationDetail")
        require(case["http_status"] == 200, "accessible discovery status")
        require(
            {key: detail[key] for key in conversation["body"]} == conversation["body"],
            "discovery changes no canonical metadata/version/timestamp",
        )
        eligible = []
        for candidate in facts["runs"]:
            run = candidate["body"]
            self.checker.validate(run, "#/components/schemas/Run")
            if (
                run["kind"] == "conversation"
                and run["status"] in {"pending", "running"}
                and candidate["owner_id"] == case["authenticated_owner"]
                and candidate["conversation_id"] == conversation_id
                and candidate["accessible"]
                and not candidate["fenced"]
            ):
                eligible.append(run["run_id"])
        require(len(eligible) <= 1, "one active conversational run per snapshot")
        require(
            detail["active_run_id"] == (eligible[0] if eligible else None),
            "active-run discovery eligibility mismatch",
        )
        run_id = detail["active_run_id"]
        if run_id is not None:
            requests.append(self.request("getRun", run_id))
            status_run = case["status_response"]
            self.checker.validate(status_run, "#/components/schemas/Run")
            require(
                status_run["run_id"] == run_id and status_run["kind"] == "conversation",
                "discovered/status run identity mismatch",
            )
            status: str | None = status_run["status"]
            if status not in TERMINAL:
                requests.append(self.request("subscribeRun", run_id))
                snapshot = case["attachment_snapshot"]
                require(snapshot["run_id"] == run_id, "attachment identity mismatch")
                stream = SSETranscript(self.checker)
                stream.attach(snapshot)
                status = stream.status
                if status in TERMINAL:
                    requests.append(self.request("getRun", run_id))
                    status_run = case["terminal_response"]
                    self.checker.validate(status_run, "#/components/schemas/Run")
                    require(
                        status_run["run_id"] == run_id
                        and status_run["kind"] == "conversation"
                        and status_run["status"] == status,
                        "terminal snapshot requires authoritative Run confirmation",
                    )
            if status not in TERMINAL:
                return requests
        requests.append(self.request("listMessages", conversation_id))
        self.checker.validate(
            case["history_response"], "#/components/schemas/ListMessages"
        )
        if run_id is not None and status == "completed":
            require(
                any(
                    message["role"] == "assistant"
                    and message["conversation_id"] == conversation_id
                    and message["content"]["text"] == status_run["final_text"]
                    for message in case["history_response"]["items"]
                ),
                "completion recovery requires canonical committed history",
            )
        return requests


def history_page(
    inherited: list[str], local: list[str], limit: int, boundary: int | None = None
) -> list[dict[str, Any]]:
    # Mirrors V004's inherited ordinals + inherited_count + local ordinal.
    visible: list[dict[str, Any]] = [
        {"ordinal": i + 1, "revision_id": revision}
        for i, revision in enumerate(inherited + local)
    ]
    eligible = [r for r in visible if boundary is None or r["ordinal"] < boundary]
    return eligible[-limit:]


def cursor_agreement(scope: dict[str, Any], cursor: dict[str, Any]) -> None:
    require(cursor.get("integrity_valid") is True, "fixture denotes tampered cursor")
    require(cursor.get("format_version") == 1, "unsupported cursor format")
    for field in ("owner", "resource", "parent", "filters", "ordering"):
        require(cursor.get(field) == scope.get(field), "cursor scope mismatch")
    require("boundary" in cursor, "cursor boundary missing")
    # Page size and conversation_version intentionally do not bind traversal.


def check_scenarios(checker: ContractChecker, kind: str, cases: Any) -> None:
    require(isinstance(cases, list) and cases, "scenario fixture must be nonempty list")
    for case in cases:
        if kind == "discovery":
            requests = ConversationDiscovery(checker).check(case)
            require(
                requests == case["expected_requests"],
                "discovery request transcript drift",
            )
        elif kind == "cancellation":
            cancellation(checker, case["http_status"], case["run"])
        elif kind == "cookie_policy":
            require(case["name"] == "__Host-mimir_session", "cookie name drift")
            require(
                case["http_only"] is True and case["secure"] is True,
                "cookie security flags missing",
            )
            require(
                case["same_site"] == "Strict" and case["path"] == "/",
                "cookie scope drift",
            )
            require(
                "domain" not in case and case["transport"] == "https",
                "host cookie/HTTPS requirement",
            )
            require(
                case["json_credential_absent"] is True,
                "browser JSON must omit credential",
            )
            require(
                case["cache_control"] == "no-store", "session response cache policy"
            )
            if case["phase"] == "login":
                require(
                    0 < case["max_age"] <= case["remaining_absolute_seconds"],
                    "cookie absolute lifetime bound",
                )
            else:
                require(
                    case["phase"] == "logout" and case["max_age"] == 0,
                    "logout must expire matching cookie",
                )
        elif kind == "visibility":
            expected = 401 if not case["authenticated"] else 404
            visibility_code = "unauthenticated" if expected == 401 else "not_found"
            require(
                not case["authenticated"]
                or not (case["exists"] and case["owned"] and case["accessible"]),
                "visibility fixture must represent denied lookup",
            )
            require(
                case["http_status"] == expected
                and case["error_code"] == visibility_code,
                "lookup existence disclosure",
            )
            require(case["kind"] in {"conversation", "standalone"}, "run kind missing")
            require(
                case["operation"] in {"getRun", "cancelRun", "subscribeRun"},
                "run operation missing",
            )
        elif kind == "errors":
            checker.validate(case["body"], "#/components/schemas/Error")
            require(
                checker.errors["codes"][case["body"]["error"]["code"]]
                == case["http_status"],
                "fixture error/status mismatch",
            )
        elif kind == "auth":
            allowed, status, code = auth_outcome(case["facts"])
            require(
                allowed == case["allowed"], "authentication scenario agreement failed"
            )
            if "http_status" in case:
                require(
                    status == case["http_status"] and code == case["error_code"],
                    "authentication error/status disagreement",
                )
        elif kind == "listing":
            rows = ordered_page(
                case["rows"], tuple(case["fields"]), case["limit"], case.get("boundary")
            )
            require(
                [row[case["fields"][1]] for row in rows] == case["expected_ids"],
                "listing keyset agreement failed",
            )
            active = next(
                (r[case["fields"][1]] for r in rows if r["status"] == "active"), None
            )
            if "latest_active" in case:
                require(
                    active == case["latest_active"],
                    "latest-active selection agreement failed",
                )
        elif kind == "history":
            require(
                history_page(
                    case["inherited"],
                    case["local"],
                    case["limit"],
                    case.get("boundary"),
                )
                == case["expected"],
                "visible history agreement failed",
            )
        elif kind == "cursor":
            try:
                cursor_agreement(case["scope"], case["cursor"])
            except ContractError:
                require(case["valid"] is False, "valid cursor fixture rejected")
                require(
                    case["http_status"] == 422
                    and case["error_code"] == "invalid_request",
                    "cursor error/status mismatch",
                )
            else:
                require(case["valid"] is True, "invalid cursor fixture accepted")
        elif kind == "sse_transcript":
            transcript = SSETranscript(checker)
            for frame in case["frames"]:
                transcript.frame(frame)
            require(
                transcript.text == case["expected_text"]
                and transcript.status == case["expected_status"],
                "transcript agreement failed",
            )
        else:
            raise ContractError("unknown fixture kind")
