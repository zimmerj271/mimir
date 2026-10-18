"""Installed package and async test infrastructure smoke checks for T01."""

import asyncio
import subprocess
import sys
from importlib import metadata, resources
from pathlib import Path

import pytest

import mimir_backend


def test_installed_package_has_metadata_and_typing_marker() -> None:
    assert metadata.version("mimir-backend") == "0.1.0"
    assert resources.files(mimir_backend).joinpath("py.typed").is_file()


def test_installed_package_imports_outside_repository(tmp_path: Path) -> None:
    result = subprocess.run(
        [sys.executable, "-I", "-c", "import mimir_backend"],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        check=False,
        timeout=15,
    )
    assert result.returncode == 0, result.stderr


@pytest.mark.asyncio
async def test_async_test_runner_propagates_cancellation() -> None:
    started = asyncio.Event()
    finalized = asyncio.Event()

    async def worker() -> None:
        try:
            started.set()
            await asyncio.Event().wait()
        finally:
            finalized.set()

    task = asyncio.create_task(worker())
    await asyncio.wait_for(started.wait(), timeout=5)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert finalized.is_set()
