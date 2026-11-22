"""Parity checks for locally compiled optional Cython pricing extensions.

These tests skip when the extensions were not built, e.g. regular CI on macOS.
To exercise them, build each cython_model/*/setup.py with the project venv.
"""
import importlib.util
from pathlib import Path
from types import ModuleType

import pytest

from vnpy_optionmaster.pricing import binomial_tree, black_76, black_scholes


_ROOT = Path(__file__).resolve().parent.parent / "vnpy_optionmaster" / "pricing" / "cython_model"


def _compiled(name: str) -> ModuleType:
    directory = _ROOT / name
    candidates = sorted(directory.glob(f"{name}*.so")) + sorted(directory.glob(f"{name}*.pyd"))
    if not candidates:
        pytest.skip(f"{name} Cython extension not built")
    spec = importlib.util.spec_from_file_location(name, candidates[0])
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.mark.parametrize(
    ("name", "python_model"),
    [
        ("black_76_cython", black_76),
        ("black_scholes_cython", black_scholes),
        ("binomial_tree_cython", binomial_tree),
    ],
)
@pytest.mark.parametrize("cp", [-1, 1])
@pytest.mark.parametrize("t", [0.0, 0.5])
@pytest.mark.parametrize("sigma", [0.0, 0.2])
def test_cython_model_matches_python_across_boundaries(
    name: str, python_model: ModuleType, cp: int, t: float, sigma: float
) -> None:
    compiled = _compiled(name)
    for spot in (95.0, 105.0):
        args = (spot, 100.0, 0.05, t, sigma, cp)
        assert compiled.calculate_price(*args) == pytest.approx(
            python_model.calculate_price(*args), abs=1e-8
        )
        assert compiled.calculate_greeks(*args) == pytest.approx(
            python_model.calculate_greeks(*args), abs=1e-7
        )


@pytest.mark.parametrize(
    ("name", "python_model"),
    [("black_76_cython", black_76), ("black_scholes_cython", black_scholes)],
)
def test_cython_deep_otm_implied_volatility_round_trip(name: str, python_model: ModuleType) -> None:
    compiled = _compiled(name)
    args = (100.0, 140.0, 0.02, 0.05, 0.6, 1)
    price = python_model.calculate_price(*args)
    assert compiled.calculate_impv(price, args[0], args[1], args[2], args[3], args[5]) == pytest.approx(0.6, abs=1e-4)
