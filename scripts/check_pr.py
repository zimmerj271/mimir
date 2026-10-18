"""Validate assignment and evidence metadata without executing PR-supplied text."""

import json
from pathlib import Path
import re
import sys

from check_governance import require

ROOT = Path(__file__).resolve().parents[1]


def check_pr(pr, tasks):
    require(pr["base"]["ref"] == "main", "PR must target main")
    branch = pr["head"]["ref"]
    match = re.fullmatch(r"feature/([a-z0-9]+(?:-[0-9]+[a-z]?)?)-[a-z0-9-]+", branch)
    require(match, "expected feature/<task-id>-<slug> branch")
    task_id = match[1].upper()
    require(task_id in tasks, f"unknown assigned task: {task_id}")
    body = pr.get("body") or ""
    fields = {}
    template = (ROOT / "PR_REVIEW_TEMPLATE.md").read_text()
    for label in re.findall(r"^- (.+):$", template, re.M):
        prefix = f"- {label}:"
        values = [line[len(prefix):].strip() for line in body.splitlines()
                  if line.startswith(prefix)]
        require(len(values) == 1, f"missing or duplicate PR evidence: {label}")
        fields[label] = values[0]
        require(fields.get(label), f"missing PR evidence: {label}")
    require(re.search(rf"\b{re.escape(task_id)}\b", fields["Task ID / assigned scope / related acceptance IDs"]),
            "branch/task assignment mismatch")
    registry = json.loads((ROOT / "docs/acceptance/registry.json").read_text())
    acceptance = fields["Task ID / assigned scope / related acceptance IDs"]
    require(any(task_id in row["owner_tasks"] and re.search(rf"\b{re.escape(row['id'])}\b", acceptance)
                for row in registry["requirements"]), "no assigned acceptance ID in PR evidence")
    require(pr["base"]["sha"] in fields["Branch / base `main` SHA / contract revision + checksum"],
            "record current main base SHA; refresh evidence after upstream changes")
    require("NOT MERGED / STOPPED" in fields["Actual merge commit SHA, or **NOT MERGED / STOPPED**"],
            "implementation PR must stop before merge")
    return task_id


def main():
    try:
        event = json.loads(Path(sys.argv[1]).read_text())
        graph = json.loads((ROOT / "docs/governance/TASK_GRAPH.json").read_text())
        task_id = check_pr(event["pull_request"], {task["id"]: task for task in graph["tasks"]})
        print(f"PASS: {task_id} branch and PR evidence metadata (review content still requires human inspection)")
    except (ValueError, KeyError, TypeError, OSError, IndexError) as exc:
        print(f"FAIL: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
