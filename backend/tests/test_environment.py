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
        "import mimir_backend, mypy, pytest, pytest_asyncio, pytest_cov, openapi_spec_validator, jsonschema, yaml; "
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
                'required-version = ">=0.13.4,<0.14.0"', 'required-version = "==0.0.0"'
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
        '[tool.uv]\nrequired-version = ">=0.13.4,<0.14.0"\n'
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
    # T08 adds runtime dependencies. Install their exact locked artifacts rather
    # than requiring unseeded registry index metadata for an offline re-resolve.
    # This temporary export is derived from uv.lock, never a second authority.
    runtime_lock = outside / "pylock.runtime.toml"
    result = run_uv(
        project,
        "export",
        "--locked",
        "--no-default-groups",
        "--no-emit-project",
        "--format",
        "pylock.toml",
        "--output-file",
        str(runtime_lock),
    )
    assert result.returncode == 0, result.stderr
    environment = outside / ".venv"
    result = run_uv(outside, "venv", "--python", sys.executable, str(environment))
    assert result.returncode == 0, result.stderr
    interpreter = environment / "bin/python"
    result = run_uv(
        outside, "pip", "sync", "--python", str(interpreter), str(runtime_lock)
    )
    assert result.returncode == 0, result.stderr
    result = run_uv(
        outside,
        "pip",
        "install",
        "--python",
        str(interpreter),
        "--no-deps",
        str(rebuilt),
    )
    assert result.returncode == 0, result.stderr
    # --no-deps uses the locked environment above; check still validates the
    # installed wheel's declared dependencies instead of bypassing compatibility.
    result = run_uv(outside, "pip", "check", "--python", str(interpreter))
    assert result.returncode == 0, result.stderr
    result = run_uv(
        outside,
        "run",
        "--no-project",
        "--python",
        str(interpreter),
        "python",
        "-I",
        "-c",
        "import mimir_backend, mimir_backend.auth, mimir_backend.auth_postgres; "
        "from importlib.resources import files; "
        "assert files(mimir_backend).joinpath('py.typed').is_file()",
    )
    assert result.returncode == 0, result.stderr


def test_s01_local_uv_range_and_exact_ci_deployment_pins() -> None:
    import re
    import tomllib

    from packaging.specifiers import SpecifierSet
    from packaging.version import Version

    root = BACKEND.parent
    config = tomllib.loads((BACKEND / "pyproject.toml").read_text())
    assert config["tool"]["uv"]["required-version"] == ">=0.13.4,<0.14.0"
    assert (BACKEND / ".python-version").read_text().strip() == "3.14.7"
    workflow = (root / ".github/workflows/bootstrap.yml").read_text()
    assert "version: '0.13.9'" in workflow
    dockerfile = (root / "deployment/Dockerfile").read_text()
    assert re.search(
        r"^FROM ghcr\.io/astral-sh/uv:0\.13\.9@sha256:"
        r"d3b601f0c7b118bc1753febf7c41cc80981df678445e0254284eecf84db79a7d AS uv$",
        dockerfile,
        re.MULTILINE,
    )
    result = subprocess.run(
        ["uv", "--version"], capture_output=True, text=True, check=True, timeout=15
    )
    policy = SpecifierSet(config["tool"]["uv"]["required-version"])
    assert Version(result.stdout.split()[1]) in policy
    for compatible in ("0.13.4", "0.13.9", "0.13.10"):
        assert Version(compatible) in policy
    for incompatible in ("0.13.3", "0.14.0", "0.14.1"):
        assert Version(incompatible) not in policy
