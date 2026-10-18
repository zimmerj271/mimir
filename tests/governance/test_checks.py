"""GOV-01: structural governance, false-pass and metadata failure paths."""

import copy
import json
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))
from check_governance import (  # noqa: E402
    CATALOG, LEGACY, ROOT, check_graph, check_hygiene, check_links, check_registry,
)
from check_pr import check_pr  # noqa: E402
from check_protection import check_protection  # noqa: E402


class GovernanceTests(unittest.TestCase):
    def setUp(self):
        self.graph = json.loads((ROOT / "docs/governance/TASK_GRAPH.json").read_text())
        self.registry = json.loads((ROOT / "docs/acceptance/registry.json").read_text())
        self.tasks = check_graph(ROOT, self.graph)

    def test_repository_graph_and_registry(self):
        check_registry(ROOT, self.tasks, self.registry)
        self.assertEqual(self.tasks["S00"]["depends_on"], [])

    def test_graph_rejects_duplicate_unknown_cycle_and_missing_file(self):
        for fault in ("duplicate", "unknown", "cycle", "file"):
            with self.subTest(fault=fault):
                graph = copy.deepcopy(self.graph)
                if fault == "duplicate":
                    graph["tasks"].append(graph["tasks"][0])
                elif fault == "unknown":
                    graph["tasks"][0]["depends_on"] = ["MISSING"]
                elif fault == "cycle":
                    graph["tasks"][0]["depends_on"] = [graph["tasks"][0]["id"]]
                else:
                    graph["tasks"][0]["file"] = "absent.md"
                with self.assertRaises(ValueError):
                    check_graph(ROOT, graph)

    def test_registry_rejects_dangling_and_duplicate_ids(self):
        for fault in ("missing", "duplicate", "unknown", "owner", "wrong_owner", "test", "false_pass"):
            with self.subTest(fault=fault):
                registry = copy.deepcopy(self.registry)
                if fault == "missing":
                    registry["requirements"].pop()
                elif fault == "duplicate":
                    registry["requirements"].append(registry["requirements"][0])
                elif fault == "unknown":
                    registry["requirements"][0]["id"] = "MISSING-01"
                elif fault == "owner":
                    registry["requirements"][0]["owner_tasks"] = ["UNKNOWN"]
                elif fault == "wrong_owner":
                    registry["requirements"][0]["owner_tasks"] = ["S00"]
                elif fault == "test":
                    registry["requirements"][0]["tests"] = ["absent.py"]
                else:
                    registry["requirements"][0]["status"] = "implemented"
                with self.assertRaises(ValueError):
                    check_registry(ROOT, self.tasks, registry)

    def test_same_id_in_distinct_sources_is_preserved(self):
        rows = [row for row in self.registry["requirements"] if row["id"] == "MEM-03"]
        self.assertEqual({row["source"] for row in rows}, {CATALOG, LEGACY})
        self.assertEqual(len(rows), 2)

    def test_links_valid_broken_and_escape(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "target.md").write_text("target\n")
            doc = root / "doc.md"
            doc.write_text("[target](target.md#anchor) [web](https://example.com)\n")
            check_links(root, ["doc.md"])
            for target in ("missing.md", "../outside.md"):
                doc.write_text(f"[bad]({target})\n")
                with self.assertRaises(ValueError):
                    check_links(root, ["doc.md"])

    def test_secret_guards_report_path_without_secret(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            file = root / "sample.txt"
            file.write_text("safe metadata\n")
            check_hygiene(root, ["sample.txt"])
            for content in ("-----BEGIN " + "PRIVATE KEY-----", "ghp_" + "x" * 40):
                file.write_text(content)
                with self.assertRaises(ValueError) as error:
                    check_hygiene(root, ["sample.txt"])
                self.assertNotIn(content, str(error.exception))
            with self.assertRaisesRegex(ValueError, "sensitive tracked path"):
                check_hygiene(root, [".env"])

    def test_protection_rejects_missing_or_weakened_enforcement(self):
        desired = json.loads((ROOT / "docs/governance/main-protection.json").read_text())
        actual = copy.deepcopy(desired)
        for key in ("enforce_admins", "required_linear_history", "allow_force_pushes",
                    "allow_deletions", "required_conversation_resolution", "lock_branch"):
            actual[key] = {"enabled": desired[key]}
        check_protection(actual, desired)
        for fault in ("admin", "approval", "checks", "force_push", "stale"):
            with self.subTest(fault=fault):
                changed = copy.deepcopy(actual)
                if fault == "admin":
                    changed["enforce_admins"]["enabled"] = False
                elif fault == "approval":
                    changed["required_pull_request_reviews"]["required_approving_review_count"] = 0
                elif fault == "checks":
                    changed["required_status_checks"]["checks"].pop()
                elif fault == "force_push":
                    changed["allow_force_pushes"]["enabled"] = True
                else:
                    changed["required_pull_request_reviews"]["dismiss_stale_reviews"] = False
                with self.assertRaises(ValueError):
                    check_protection(changed, desired)

    def pr(self):
        template = (ROOT / "PR_REVIEW_TEMPLATE.md").read_text()
        lines = []
        for line in template.splitlines():
            if line.startswith("- ") and line.endswith(":"):
                lines.append(line + " S00 GOV-01 evidence NOT MERGED / STOPPED " + "a" * 40)
        return {"base": {"ref": "main", "sha": "a" * 40},
                "head": {"ref": "feature/s00-monorepo-bootstrap"},
                "body": "\n".join(lines)}

    def test_pr_metadata_accepts_complete_packet(self):
        self.assertEqual(check_pr(self.pr(), self.tasks), "S00")
        pr = self.pr()
        pr["head"]["ref"] = "feature/cli-12a-viewport"
        pr["body"] = pr["body"].replace("S00", "CLI-12A").replace("GOV-01", "CLI-TUI-02")
        self.assertEqual(check_pr(pr, self.tasks), "CLI-12A")

    def test_pr_metadata_rejects_missing_evidence_wrong_task_and_base(self):
        for fault in ("body", "branch", "task", "base", "target", "stop", "acceptance"):
            with self.subTest(fault=fault):
                pr = self.pr()
                if fault == "body":
                    pr["body"] = ""
                elif fault == "branch":
                    pr["head"]["ref"] = "main"
                elif fault == "task":
                    pr["head"]["ref"] = "feature/t01-python-toolchain"
                elif fault == "base":
                    pr["base"]["sha"] = "b" * 40
                elif fault == "stop":
                    pr["body"] = pr["body"].replace(" S00 GOV-01 evidence NOT MERGED / STOPPED", " S00 GOV-01 evidence")
                elif fault == "acceptance":
                    pr["body"] = pr["body"].replace("GOV-01", "INVALID-01")
                else:
                    pr["base"]["ref"] = "develop"
                with self.assertRaises(ValueError):
                    check_pr(pr, self.tasks)


if __name__ == "__main__":
    unittest.main()
