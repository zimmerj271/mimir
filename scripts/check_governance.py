"""Offline, standard-library-only repository checks; no contract validation."""

import argparse
import json
from pathlib import Path
import re
import subprocess
import sys
from urllib.parse import unquote, urlsplit

ROOT = Path(__file__).resolve().parents[1]
CATALOG = "docs/acceptance/REQUIREMENTS.md"
LEGACY = "backend/ACCEPTANCE_TESTS.md"


def require(condition, message):
    if not condition:
        raise ValueError(message)


def local_file(root, name):
    path = (root / name).resolve()
    require(path.is_relative_to(root.resolve()), f"path escapes repository: {name}")
    require(path.is_file(), f"missing file: {name}")
    return path


def check_graph(root, graph):
    require(graph["schema_version"] == 1, "unknown task graph version")
    tasks = {task["id"]: task for task in graph["tasks"]}
    require(len(tasks) == len(graph["tasks"]), "duplicate task ID")
    visiting, visited = set(), set()

    def visit(task_id):
        require(task_id in tasks, f"unknown dependency: {task_id}")
        require(task_id not in visiting, f"task cycle: {task_id}")
        if task_id in visited:
            return
        visiting.add(task_id)
        task = tasks[task_id]
        local_file(root, task["file"])
        require(len(set(task["depends_on"])) == len(task["depends_on"]),
                f"duplicate dependency: {task_id}")
        for dependency in task["depends_on"]:
            visit(dependency)
        visiting.remove(task_id)
        visited.add(task_id)

    for task_id in tasks:
        visit(task_id)
    return tasks


def source_ids(root):
    catalog = local_file(root, CATALOG).read_text()
    legacy = local_file(root, LEGACY).read_text()
    return {
        CATALOG: set(re.findall(r"^\| ([A-Z][A-Z0-9-]*-\d+) \|", catalog, re.M)),
        LEGACY: set(re.findall(r"^- `([A-Z][A-Z0-9-]*-\d+)`", legacy, re.M)),
    }


def check_registry(root, tasks, registry):
    require(registry["schema_version"] == 1, "unknown acceptance registry version")
    expected = {(source, ident) for source, ids in source_ids(root).items() for ident in ids}
    seen = set()
    for row in registry["requirements"]:
        key = row["source"], row["id"]
        require(key not in seen, f"duplicate requirement: {key}")
        require(key in expected, f"unknown requirement: {key}")
        seen.add(key)
        require(row["owner_tasks"], f"unowned requirement: {key}")
        for task in row["owner_tasks"]:
            require(task in tasks, f"unknown owner task: {task}")
        require(row["kind"] in {"unit", "integration", "e2e", "not_implemented"},
                f"invalid test kind: {key}")
        require(row["status"] in {"implemented", "not_implemented"}, f"invalid status: {key}")
        for test in row["tests"]:
            local_file(root, test)
        require("last_verified_commit" in row and "evidence" in row, f"missing evidence fields: {key}")
        if row["status"] == "implemented":
            require(row["tests"] and row["kind"] != "not_implemented",
                    f"implemented requirement has no executable test: {key}")
        else:
            require(not row["tests"] and row["last_verified_commit"] is None,
                    f"unimplemented requirement claims verification: {key}")
        if row["last_verified_commit"] is not None:
            require(re.fullmatch(r"[0-9a-f]{40}", row["last_verified_commit"]),
                    f"invalid verified commit: {key}")
            require(row["evidence"], f"verified requirement lacks evidence: {key}")
    require(seen == expected, f"missing requirements: {sorted(expected - seen)}")

    # Keep both human-readable ownership sources synchronized with the registry.
    owners = {}
    for ident, owner, verifier in re.findall(
            r"^\| ([A-Z][A-Z0-9-]*-\d+) \| ([^|]+) \| ([^|]+) \|",
            local_file(root, CATALOG).read_text(), re.M):
        owners[CATALOG, ident] = {owner.strip(), verifier.strip()}
    seeds = re.findall(r"^\| ([A-Z][A-Z0-9-]*-\d+) \| ([^|]+) \|",
                       local_file(root, "TRACEABILITY.md").read_text(), re.M)
    require({ident for ident, _ in seeds} == source_ids(root)[LEGACY],
            "traceability seed has dangling or missing acceptance IDs")
    require(len(seeds) == len(source_ids(root)[LEGACY]), "duplicate traceability seed")
    for ident, text in seeds:
        expanded = re.sub(r"T(\d+)–T(\d+)",
                          lambda m: "/".join(f"T{i:02}" for i in range(int(m[1]), int(m[2]) + 1)),
                          text.strip())
        assigned = set(expanded.split("/"))
        if "CLI-16" in assigned:
            assigned.remove("CLI-16")
            assigned.add("CLI-16B" if ident == "PERF-01" else "CLI-16A")
        owners[LEGACY, ident] = assigned
    for row in registry["requirements"]:
        require(set(row["owner_tasks"]) == owners[row["source"], row["id"]],
                f"ownership differs from normative source: {row['id']} ({row['source']})")

    # Supporting task coverage does not reassign normative requirement ownership.
    for task_id, coverage in registry.get("task_coverage", {}).items():
        require(task_id in tasks, f"unknown coverage task: {task_id}")
        require(coverage["specification"] == tasks[task_id]["file"],
                f"coverage specification differs from task graph: {task_id}")
        references = [(item["source"], item["id"]) for item in coverage["requirements"]]
        require(references and len(references) == len(set(references)),
                f"missing or duplicate coverage requirements: {task_id}")
        require(set(references) <= expected, f"unknown coverage requirement: {task_id}")
        require(coverage["tests"], f"task coverage has no tests: {task_id}")
        for test in coverage["tests"]:
            local_file(root, test)


def task_requirement_ids(registry, task_id):
    references = {(item["source"], item["id"])
                  for item in registry.get("task_coverage", {}).get(task_id, {}).get("requirements", [])}
    return {row["id"] for row in registry["requirements"]
            if task_id in row["owner_tasks"] or (row["source"], row["id"]) in references}


def check_links(root, files):
    for name in files:
        if not name.endswith(".md"):
            continue
        path = local_file(root, name)
        for target in re.findall(r"\]\(([^\s)]+)\)", path.read_text()):
            target = target.strip("<>")
            parsed = urlsplit(target)
            if parsed.scheme or target.startswith("#"):
                continue
            relative = unquote(parsed.path)
            if relative:
                resolved = (root / relative.lstrip("/") if relative.startswith("/")
                            else path.parent / relative)
                require(resolved.resolve().is_relative_to(root.resolve()),
                        f"link escapes repository: {name}: {target}")
                require(resolved.exists(), f"broken local link: {name}: {target}")


def check_hygiene(root, files):
    # A narrow guard, not a comprehensive credential scanner. Never print file contents.
    sensitive = re.compile(r"(^|/)(\.env(?:\..+)?|secrets)(/|$)|\.(pem|key|p12)$")
    private_key = re.compile(rb"-----BEGIN (?:[A-Z0-9]+ )?PRIVATE KEY-----")
    token = re.compile(rb"(?:gh[pousr]_[A-Za-z0-9]{36,}|github_pat_[A-Za-z0-9_]{60,})")
    for name in files:
        require(not sensitive.search(name) or name.endswith(".env.example"),
                f"sensitive tracked path: {name}")
        data = local_file(root, name).read_bytes()
        require(not private_key.search(data) and not token.search(data),
                f"possible credential in tracked file: {name}")
        # Existing design documents retain their approved whitespace; git diff --check
        # enforces whitespace on changes, without rewriting protected specifications.


def tracked_files(root):
    return subprocess.check_output(["git", "ls-files", "-z"], cwd=root).decode().split("\0")[:-1]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--hygiene", action="store_true")
    args = parser.parse_args()
    try:
        files = tracked_files(ROOT)
        if args.hygiene:
            check_hygiene(ROOT, files)
            print(f"PASS: sensitive-path/credential guard ({len(files)} tracked files)")
        else:
            graph = json.loads((ROOT / "docs/governance/TASK_GRAPH.json").read_text())
            tasks = check_graph(ROOT, graph)
            registry = json.loads((ROOT / "docs/acceptance/registry.json").read_text())
            check_registry(ROOT, tasks, registry)
            check_links(ROOT, files)
            print(f"PASS: {len(tasks)} tasks, {len(registry['requirements'])} source-qualified requirements, local Markdown links")
    except (ValueError, KeyError, TypeError, OSError, subprocess.CalledProcessError) as exc:
        print(f"FAIL: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
