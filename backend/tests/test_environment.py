"""Offline reproduction, packaging and lock rejection tests for the toolchain."""

import os
import shutil
import subprocess
import sys
import tarfile
import zipfile
from pathlib import Path

import pytest

BACKEND = Path(__file__).resolve().parents[1]


@pytest.fixture
def project(tmp_path: Path) -> Path:
    project = tmp_path / "backend"
    project.mkdir()
    for name in ("pyproject.toml", "uv.lock", ".python-version", "README.md"):
        shutil.copy2(BACKEND / name, project / name)
    shutil.copytree(BACKEND / "src", project / "src")
    return project


def run_uv(project: Path, *args: str) -> subprocess.CompletedProcess[str]:
    uv = shutil.which("uv")
    assert uv is not None, "uv must be installed before running toolchain tests"
    env = os.environ.copy()
    env.pop("VIRTUAL_ENV", None)
    env["UV_PYTHON_DOWNLOADS"] = "never"
    return subprocess.run(
        [uv, "--offline", "--no-progress", *args],
        cwd=project,
        env=env,
        capture_output=True,
        text=True,
        check=False,
        timeout=45,
    )


def test_fresh_locked_environment_and_repeat_sync(project: Path) -> None:
    original = (project / "uv.lock").read_bytes()
    for _ in range(2):
        result = run_uv(
            project, "sync", "--locked", "--all-groups", "--python", sys.executable
        )
        assert result.returncode == 0, result.stderr
        assert (project / "uv.lock").read_bytes() == original
    result = run_uv(
        project,
        "run",
        "--locked",
        "python",
        "-I",
        "-c",
        "import mimir_backend, mypy, pytest, pytest_asyncio, pytest_cov; "
        "from importlib.metadata import version; "
        "assert version('mimir-backend') == '0.1.0'",
    )
    assert result.returncode == 0, result.stderr


@pytest.mark.parametrize("fault", ["missing_lock", "wrong_uv"])
def test_locked_sync_rejects_invalid_toolchain(project: Path, fault: str) -> None:
    manifest = project / "pyproject.toml"
    lock = project / "uv.lock"
    original = lock.read_bytes()
    if fault == "missing_lock":
        lock.unlink()
    else:
        manifest.write_text(
            manifest.read_text().replace(
                'required-version = "==0.12.10"', 'required-version = "==0.0.0"'
            )
        )
    result = run_uv(
        project, "sync", "--locked", "--all-groups", "--python", sys.executable
    )
    assert result.returncode != 0
    expected = "required" if fault == "wrong_uv" else "lockfile"
    assert expected in result.stderr.lower(), result.stderr
    if fault == "missing_lock":
        assert not lock.exists()
    else:
        assert lock.read_bytes() == original


def test_stale_lock_rejected_while_unlocked_control_succeeds(tmp_path: Path) -> None:
    # A stale graph is re-resolved by uv. Use no registry dependencies to isolate
    # --locked semantics from the availability of cached PyPI index metadata.
    project = tmp_path / "lock-fixture"
    project.mkdir()
    manifest = project / "pyproject.toml"
    manifest.write_text(
        '[project]\nname = "mimir-lock-fixture"\nversion = "0.1.0"\n'
        'requires-python = ">=3.14,<3.15"\n'
        '[tool.uv]\nrequired-version = "==0.12.10"\n'
    )
    result = run_uv(project, "lock", "--python", sys.executable)
    assert result.returncode == 0, result.stderr
    lock = project / "uv.lock"
    original = lock.read_bytes()
    manifest.write_text(
        manifest.read_text().replace('version = "0.1.0"', 'version = "0.1.1"')
    )
    result = run_uv(project, "sync", "--locked", "--python", sys.executable)
    assert result.returncode != 0
    assert "lockfile" in result.stderr.lower(), result.stderr
    assert lock.read_bytes() == original
    control = run_uv(project, "sync", "--python", sys.executable)
    assert control.returncode == 0, control.stderr
    assert lock.read_bytes() != original


def test_wheel_and_sdist_install_without_source_checkout(project: Path) -> None:
    result = run_uv(project, "build", "--python", sys.executable)
    assert result.returncode == 0, result.stderr
    (wheel,) = (project / "dist").glob("*.whl")
    (sdist,) = (project / "dist").glob("*.tar.gz")
    with zipfile.ZipFile(wheel) as archive:
        assert "mimir_backend/__init__.py" in archive.namelist()
        assert "mimir_backend/py.typed" in archive.namelist()
    with tarfile.open(sdist) as archive:
        assert any(
            name.endswith("src/mimir_backend/py.typed") for name in archive.getnames()
        )
    result = run_uv(
        project,
        "build",
        str(sdist),
        "--wheel",
        "--no-build-isolation",
        "--force-pep517",
        "--out-dir",
        str(project / "rebuilt"),
        "--python",
        sys.executable,
    )
    assert result.returncode == 0, result.stderr
    (rebuilt,) = (project / "rebuilt").glob("*.whl")
    outside = project.parent / "outside"
    outside.mkdir()
    result = run_uv(
        outside,
        "run",
        "--isolated",
        "--no-project",
        "--with",
        str(rebuilt),
        "--python",
        sys.executable,
        "python",
        "-I",
        "-c",
        "import mimir_backend; from importlib.resources import files; "
        "assert files(mimir_backend).joinpath('py.typed').is_file()",
    )
    assert result.returncode == 0, result.stderr
