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


def test_sdist_contains_no_prebuilt_platform_binaries() -> None:
    archives = sorted(_DIST.glob("*.tar.gz"))
    if not archives:
        pytest.skip("build sdist first with uv build")
    with open_tar(archives[-1], "r:gz") as archive:
        unsafe = [name for name in archive.getnames() if name.endswith((".pyd", ".so"))]
    assert not unsafe, f"prebuilt platform binaries inside sdist: {unsafe}"
