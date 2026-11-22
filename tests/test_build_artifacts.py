"""Packaging contract: universal distribution must not ship stale platform binaries."""
from pathlib import Path
from tarfile import open as open_tar
from zipfile import ZipFile

import pytest


_DIST = Path(__file__).resolve().parent.parent / "dist"


def test_universal_wheel_contains_no_platform_binaries() -> None:
    wheels = sorted(_DIST.glob("*-py3-none-any.whl"))
    if not wheels:
        pytest.skip("build a wheel first with uv build")
    with ZipFile(wheels[-1]) as archive:
        unsafe = [name for name in archive.namelist() if name.endswith((".pyd", ".so"))]
    assert not unsafe, f"platform binaries inside universal wheel: {unsafe}"


def test_portable_wheel_selects_python_backend_without_compiled_native(tmp_path) -> None:
    """Import the built wheel itself from an isolated cwd, not source checkout."""
    import os
    import subprocess
    import sys

    wheels = sorted(_DIST.glob("*-py3-none-any.whl"))
    if not wheels:
        pytest.skip("build a wheel first with uv build")
    wheel = wheels[-1]
    with ZipFile(wheel) as archive:
        assert "vnpy_optionmaster/pricing/backend.py" in archive.namelist()

    env = os.environ.copy()
    env["PYTHONPATH"] = str(wheel)
    test_program = (
        "from vnpy_optionmaster.engine import PRICING_BACKENDS;"
        "assert len(PRICING_BACKENDS) == 3;"
        "assert all(v.backend == 'python' for v in PRICING_BACKENDS.values());"
        "print('WHEEL_BACKEND: Python fallback for all models')"
    )
    result = subprocess.run(
        [sys.executable, "-c", test_program],
        cwd=tmp_path,
        env=env,
        capture_output=True,
        text=True,
        timeout=30,
        check=False
    )
    assert result.returncode == 0, (result.stdout, result.stderr)
    assert "WHEEL_BACKEND: Python fallback" in result.stdout


def test_sdist_contains_no_prebuilt_platform_binaries() -> None:
    archives = sorted(_DIST.glob("*.tar.gz"))
    if not archives:
        pytest.skip("build sdist first with uv build")
    with open_tar(archives[-1], "r:gz") as archive:
        unsafe = [name for name in archive.getnames() if name.endswith((".pyd", ".so"))]
    assert not unsafe, f"prebuilt platform binaries inside sdist: {unsafe}"
