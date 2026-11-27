"""Deliberate S01 candidate hash maintenance; never called by validation/CI."""

import hashlib
import json
from pathlib import Path

DIRECTORY = Path(__file__).resolve().parents[1] / "contracts"


def main() -> None:
    paths = sorted(DIRECTORY.rglob("*"))
    if any(path.is_symlink() for path in paths):
        raise ValueError("contract symlinks forbidden")
    files = {
        path.relative_to(DIRECTORY).as_posix(): hashlib.sha256(
            path.read_bytes()
        ).hexdigest()
        for path in paths
        if path.is_file() and path != DIRECTORY / "manifest.json"
    }
    manifest = {
        "schema_version": 1,
        "status": "candidate_pending_human_approval",
        "contract_version": "1.0.0",
        "hash_algorithm": "sha256",
        "files": files,
    }
    (DIRECTORY / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
    print(f"Updated {len(files)} candidate hashes. Review diff; no approval granted.")


if __name__ == "__main__":
    main()
