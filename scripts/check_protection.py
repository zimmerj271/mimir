"""Compare a GitHub main-protection GET response against S00's desired settings."""

import json
from pathlib import Path
import sys

from check_governance import ROOT, require


def check_protection(actual, desired):
    checks = actual["required_status_checks"]
    require(checks["strict"] is True, "main does not require an up-to-date branch")
    require(checks["checks"] == desired["required_status_checks"]["checks"],
            "required CI checks/app identities differ")
    reviews = actual["required_pull_request_reviews"]
    for key, value in desired["required_pull_request_reviews"].items():
        require(reviews[key] == value, f"review protection differs: {key}")
    for key in ("enforce_admins", "required_linear_history", "allow_force_pushes",
                "allow_deletions", "required_conversation_resolution", "lock_branch"):
        require(actual[key]["enabled"] == desired[key], f"protection differs: {key}")


def main():
    try:
        actual = json.loads(Path(sys.argv[1]).read_text())
        desired = json.loads((ROOT / "docs/governance/main-protection.json").read_text())
        check_protection(actual, desired)
        print("PASS: live main protection matches S00 CI/review/admin/force-push/deletion settings")
    except (ValueError, KeyError, TypeError, OSError, IndexError) as exc:
        print(f"FAIL: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
