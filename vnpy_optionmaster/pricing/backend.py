"""Verified optional Cython pricing backends with safe Python startup fallback.

Do not use the checked-in legacy top-level Windows .pyd binaries. A native
extension must match the current interpreter ABI and pass numerical parity
before it is bound to any OptionData. The chosen module is frozen at startup;
never change pricing implementations while a portfolio is trading.
"""
from dataclasses import dataclass
from importlib import util
from importlib.machinery import EXTENSION_SUFFIXES
import logging
from math import isclose, isfinite
from pathlib import Path
from types import ModuleType


LOGGER = logging.getLogger(__name__)
CYTHON_ROOT = Path(__file__).resolve().parent / "cython_model"


@dataclass(frozen=True)
class BackendSelection:
    """The inspected startup result for one pricing model."""

    module: ModuleType
    backend: str
    reason: str
    native_path: Path | None = None
    impv_backend: str = "python"


def _matching_extension(name: str, root: Path) -> Path | None:
    """Only use Python ABI-tagged, platform-specific compiled modules."""
    for suffix in EXTENSION_SUFFIXES:
        # Generic .so/.pyd can be stale or built for another Python version.
        if suffix in (".so", ".pyd"):
            continue
        candidate = root / name / (name + suffix)
        if candidate.is_file():
            return candidate
    return None


def _load_binary(name: str, path: Path) -> ModuleType:
    """Load only our known optional Cython module at an exact ABI-tagged path."""
    spec = util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise ImportError("Cannot create compiled pricing module loader")
    module = util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _verify_native(native: ModuleType, python_model: ModuleType) -> None:
    """Check both production price/Greek entrypoints at key risk boundaries.

    This is a startup compatibility smoke test, not a proof that every
    numerical case or a live CTP deployment has been validated.
    """
    if not callable(getattr(native, "calculate_price", None)):
        raise ValueError("validation: calculate_price missing")
    if not callable(getattr(native, "calculate_greeks", None)):
        raise ValueError("validation: calculate_greeks missing")
    for s in (85.0, 100.0, 115.0):
        for t in (0.0, 0.25, 0.75):
            for v in (0.0, 0.2, 0.6):
                for cp in (-1, 1):
                    args = (s, 100.0, 0.05, t, v, cp)
                    expected_price = python_model.calculate_price(*args)
                    actual_price = native.calculate_price(*args)
                    if (not isfinite(expected_price) or not isfinite(actual_price)
                            or not isclose(actual_price, expected_price, abs_tol=1e-8, rel_tol=1e-7)):
                        raise ValueError(f"validation: price mismatch {args}")

                    expected_greeks = python_model.calculate_greeks(*args)
                    actual_greeks = native.calculate_greeks(*args)
                    if (len(actual_greeks) != len(expected_greeks)
                            or any(
                                not isfinite(got) or not isfinite(expected)
                                or not isclose(got, expected, abs_tol=1e-7, rel_tol=1e-6)
                                for got, expected in zip(actual_greeks, expected_greeks, strict=True)
                            )):
                        raise ValueError(f"validation: Greeks mismatch {args}")


def select_backend(
    python_model: ModuleType,
    extension_name: str,
    *,
    root: Path = CYTHON_ROOT,
) -> BackendSelection:
    """Prefer compatible and numerically verified Cython, else Python.

    Any import or validation failure falls back once at startup. An exception
    during subsequent trading is not a reason to silently hot-switch models.
    """
    path = _matching_extension(extension_name, root)
    if path is None:
        selection = BackendSelection(
            python_model, "python", "missing compatible native extension"
        )
        LOGGER.info("%s: Python fallback (%s)", extension_name, selection.reason)
        return selection

    try:
        native = _load_binary(extension_name, path)
        _verify_native(native, python_model)
    except Exception as exc:
        selection = BackendSelection(
            python_model, "python", f"{type(exc).__name__}: {exc}"
        )
        LOGGER.warning("%s: Python fallback (%s)", extension_name, selection.reason)
        return selection

    # Explicitly bind the audited Python IV inversion, independent of what
    # calculate_impv() the compiled binary happens to contain. A lightweight
    # ModuleType facade preserves OptionData.set_pricing_model's existing API.
    hybrid = ModuleType(extension_name)
    hybrid.__dict__.update({
        "calculate_price": native.calculate_price,
        "calculate_greeks": native.calculate_greeks,
        "calculate_impv": python_model.calculate_impv,
    })

    LOGGER.info("%s: verified Cython price/Greeks at %s; IV bound to Python", extension_name, path)
    return BackendSelection(
        hybrid, "cython", "verified at startup", native_path=path
    )
