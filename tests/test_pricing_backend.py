"""Actual OptionEngine -> OptionData pricing binding and safe native fallback."""
from importlib.machinery import EXTENSION_SUFFIXES
from pathlib import Path
from types import ModuleType

import pytest

from vnpy_optionmaster.base import OptionData
from vnpy_optionmaster.engine import PRICING_MODELS
from vnpy_optionmaster.pricing import black_76, black_scholes, binomial_tree


_ROOT = Path(__file__).resolve().parents[1] / "vnpy_optionmaster" / "pricing" / "cython_model"
_MODELS = (
    ("Black-76 欧式期货期权", "black_76_cython", black_76),
    ("Black-Scholes 欧式股票期权", "black_scholes_cython", black_scholes),
    ("二叉树 美式期货期权", "binomial_tree_cython", binomial_tree),
)


@pytest.mark.parametrize(("label", "ext", "python_model"), _MODELS)
def test_real_engine_models_bind_cython_when_native_extension_exists(
    label: str, ext: str, python_model: ModuleType
) -> None:
    available = any((_ROOT / ext / (ext + suffix)).is_file()
                    for suffix in EXTENSION_SUFFIXES if suffix != ".so")
    if not available:
        pytest.skip("Native extension not available on this Python ABI")
    selected = PRICING_MODELS[label]
    assert selected is not python_model, "A verified native extension should be selected"
    assert selected.__name__ == ext
    option = object.__new__(OptionData)
    option.set_pricing_model(selected)
    assert option.calculate_price.__module__ == ext
    assert option.calculate_greeks.__module__ == ext
    assert option.calculate_impv is python_model.calculate_impv


def test_missing_compiled_extension_falls_back_with_reason(tmp_path: Path) -> None:
    from vnpy_optionmaster.pricing.backend import select_backend

    selection = select_backend(black_76, "black_76_cython", root=tmp_path)
    assert selection.module is black_76
    assert selection.backend == "python"
    assert "missing" in selection.reason.lower()


def test_stale_windows_abi_cannot_be_loaded_on_mac(tmp_path: Path) -> None:
    from vnpy_optionmaster.pricing.backend import select_backend

    directory = tmp_path / "black_76_cython"
    directory.mkdir()
    (directory / "black_76_cython.cp310-win_amd64.pyd").write_bytes(b"not a compatible extension")
    selection = select_backend(black_76, "black_76_cython", root=tmp_path)
    assert selection.module is black_76
    assert selection.backend == "python"


def test_native_import_error_falls_back_without_crashing(monkeypatch, tmp_path: Path) -> None:
    import vnpy_optionmaster.pricing.backend as backend

    directory = tmp_path / "black_76_cython"
    directory.mkdir()
    (directory / ("black_76_cython" + EXTENSION_SUFFIXES[0])).touch()

    def broken_loader(*_args, **_kwargs):
        raise ImportError("compiled module ABI mismatch")

    monkeypatch.setattr(backend, "_load_binary", broken_loader)
    selected = backend.select_backend(black_76, "black_76_cython", root=tmp_path)
    assert selected.module is black_76
    assert selected.backend == "python"
    assert "abi mismatch" in selected.reason.lower()


def test_wrong_native_price_or_greek_is_rejected_before_exposure(
    monkeypatch, tmp_path: Path
) -> None:
    import vnpy_optionmaster.pricing.backend as backend

    directory = tmp_path / "black_76_cython"
    directory.mkdir()
    (directory / ("black_76_cython" + EXTENSION_SUFFIXES[0])).touch()
    fake = ModuleType("black_76_cython")
    fake.calculate_price = lambda *args: 999.0
    fake.calculate_greeks = black_76.calculate_greeks
    fake.calculate_impv = black_76.calculate_impv
    monkeypatch.setattr(backend, "_load_binary", lambda *args: fake)

    selected = backend.select_backend(black_76, "black_76_cython", root=tmp_path)
    assert selected.module is black_76
    assert selected.backend == "python"
    assert "validation" in selected.reason.lower()


def test_good_native_backend_frozen_at_option_binding(monkeypatch, tmp_path: Path) -> None:
    import vnpy_optionmaster.pricing.backend as backend

    directory = tmp_path / "black_76_cython"
    directory.mkdir()
    (directory / ("black_76_cython" + EXTENSION_SUFFIXES[0])).touch()
    native = ModuleType("black_76_cython")
    native.calculate_price = black_76.calculate_price
    native.calculate_greeks = black_76.calculate_greeks
    native.calculate_impv = black_76.calculate_impv
    monkeypatch.setattr(backend, "_load_binary", lambda *args: native)

    selected = backend.select_backend(black_76, "black_76_cython", root=tmp_path)
    assert selected.backend == "cython"
    option = object.__new__(OptionData)
    option.set_pricing_model(selected.module)
    frozen = option.calculate_price
    assert frozen(100.0, 100.0, 0.02, 0.5, 0.2, 1) > 0
    monkeypatch.setattr(native, "calculate_price", lambda *args: 123456.0)
    assert option.calculate_price is frozen


def test_native_price_greeks_need_not_expose_a_native_iv_solver(
    monkeypatch, tmp_path: Path
) -> None:
    import vnpy_optionmaster.pricing.backend as backend

    directory = tmp_path / "black_76_cython"
    directory.mkdir()
    (directory / ("black_76_cython" + EXTENSION_SUFFIXES[0])).touch()
    native = ModuleType("black_76_cython")
    native.calculate_price = black_76.calculate_price
    native.calculate_greeks = black_76.calculate_greeks
    # Intentionally no calculate_impv attribute in the native module.
    monkeypatch.setattr(backend, "_load_binary", lambda *args: native)

    selected = backend.select_backend(black_76, "black_76_cython", root=tmp_path)
    assert selected.backend == "cython"
    assert selected.module.calculate_impv is black_76.calculate_impv
    assert selected.module.calculate_greeks is black_76.calculate_greeks


def test_ui_shows_real_backend_and_python_iv(monkeypatch) -> None:
    from types import SimpleNamespace
    from vnpy.trader.ui import QtWidgets
    from vnpy_optionmaster.engine import PRICING_BACKENDS
    from vnpy_optionmaster.ui.widget import PortfolioDialog

    monkeypatch.setenv("QT_QPA_PLATFORM", "offscreen")
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    fake_engine = SimpleNamespace(
        get_portfolio_setting=lambda _name: {},
        get_portfolio=lambda _name: SimpleNamespace(_chains={}),
        get_underlying_symbols=lambda _name: [],
    )
    dialog = PortfolioDialog(fake_engine, "TEST")
    for label, selected in PRICING_BACKENDS.items():
        dialog.model_name_combo.setCurrentText(label)
        actual = dialog.backend_status_label.text()
        if selected.backend == "cython":
            assert "Cython" in actual and "Python（IV）" in actual
        else:
            assert "Python" in actual and "回退" in actual
        assert dialog.backend_status_label.toolTip() == selected.reason
    dialog.close()
    assert app is not None


def test_registry_backend_status_matches_selected_model() -> None:
    from vnpy_optionmaster.engine import PRICING_BACKENDS

    assert PRICING_MODELS.keys() == PRICING_BACKENDS.keys()
    for label, selected in PRICING_BACKENDS.items():
        assert PRICING_MODELS[label] is selected.module
        assert selected.backend in ("cython", "python")
        assert selected.impv_backend == "python"
        assert selected.reason


def test_native_greek_exception_reverts_to_python_at_startup(
    monkeypatch, tmp_path: Path
) -> None:
    import vnpy_optionmaster.pricing.backend as backend

    directory = tmp_path / "black_76_cython"
    directory.mkdir()
    (directory / ("black_76_cython" + EXTENSION_SUFFIXES[0])).touch()
    fake = ModuleType("black_76_cython")
    fake.calculate_price = black_76.calculate_price
    fake.calculate_greeks = lambda *args: (_ for _ in ()).throw(ArithmeticError("bad native Greeks"))
    fake.calculate_impv = black_76.calculate_impv
    monkeypatch.setattr(backend, "_load_binary", lambda *args: fake)

    selected = backend.select_backend(black_76, "black_76_cython", root=tmp_path)
    assert selected.module is black_76
    assert "ArithmeticError" in selected.reason
