"""Tokenless contract-agreement harnesses, never HTTP/runtime implementations.

These reference models exercise fixture expectations. They cannot establish real
CSRF enforcement, cryptographic cursor integrity, SQL atomicity or CLI behavior.
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any

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
            return False, None, None  # rejection approved; no new status decision
    elif (
        authorization + cookies != 1
        or not case.get("credential_valid", True)
        or ((authorization and client != "cli") or (cookies and client != "web"))
    ):
        return False, 401, "unauthenticated"
    browser = client == "web"
    if not case.get("trusted_host", True):
        return False, None, None
    if browser and not case.get("https", False):
        return False, None, None
    if (
        case.get("has_json_body", False)
        and case.get("content_type") != "application/json"
    ):
        return False, None, None
    if browser:
        if case.get("mutation", False):
            if (
                case.get("origin") != "https://browser.invalid"
                or case.get("csrf") != "1"
            ):
                return False, 403, "forbidden"
        elif case.get("origin") not in (None, "https://browser.invalid"):
            return False, None, None
    return True, None, None


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
        eligible = [
            r for r in eligible if tuple(r[f] for f in fields) < tuple(boundary)
        ]
    return sorted(eligible, key=lambda r: tuple(r[f] for f in fields), reverse=True)[
        :limit
    ]


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
        if kind == "cancellation":
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
