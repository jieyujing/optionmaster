"""Incomplete option-chain input must be explicit, never fabricate missing volatility."""
from types import SimpleNamespace

from vnpy_optionmaster.engine import OptionEngine
from vnpy_optionmaster.ui.manager import PricingVolatilityManager


class _Text:
    def __init__(self):
        self.value = None

    def setText(self, value):
        self.value = value

    def setValue(self, value):
        self.value = value

    def setEnabled(self, enabled):
        self.enabled = enabled


class _Check:
    def isChecked(self):
        return False


def _manager():
    put = SimpleNamespace(chain_index="95", strike_price=95.0, mid_impv=0.22, pricing_impv=0.22)
    call = SimpleNamespace(chain_index="105", strike_price=105.0, mid_impv=0.20, pricing_impv=0.20)
    chain = SimpleNamespace(
        chain_symbol="CHAIN", indexes=["95", "105"], atm_price=100.0, atm_index="",
        puts={"95": put}, calls={"105": call},
        options={"P": put, "C": call},
    )
    def row():
        return {
            "otm_impv": _Text(), "call_impv": _Text(), "put_impv": _Text(),
            "pricing_impv": _Text(), "check": _Check(),
        }
    cells = {("CHAIN", index): row() for index in chain.indexes}
    ui = SimpleNamespace(
        portfolio=SimpleNamespace(get_chain=lambda symbol: chain),
        cells=cells,
        chain_atm_index={},
        update_pricing_impv=lambda symbol: None,
    )
    return ui, chain


def test_missing_leg_rows_render_empty_not_fake_zero() -> None:
    ui, _ = _manager()
    PricingVolatilityManager.update_chain_impv(ui, "CHAIN")
    assert ui.cells[("CHAIN", "95")]["call_impv"].value == ""
    assert ui.cells[("CHAIN", "95")]["put_impv"].value == "22.0%"
    assert ui.cells[("CHAIN", "105")]["call_impv"].value == "20.0%"
    assert ui.cells[("CHAIN", "105")]["put_impv"].value == ""


def test_missing_leg_can_set_only_actual_contract() -> None:
    ui, chain = _manager()
    PricingVolatilityManager.set_pricing_impv(ui, 24.0, "CHAIN", "105")
    assert chain.calls["105"].pricing_impv == 0.24
    assert "105" not in chain.puts


def test_reset_and_fit_skip_missing_otm_side_gracefully() -> None:
    ui, chain = _manager()
    PricingVolatilityManager.reset_pricing_impv(ui, "CHAIN")
    PricingVolatilityManager.fit_pricing_impv(ui, "CHAIN")
    assert chain.calls["105"].pricing_impv > 0
    assert chain.puts["95"].pricing_impv > 0
    assert "95" not in chain.calls
    assert "105" not in chain.puts


def test_restore_saved_iv_does_not_require_both_legs(monkeypatch) -> None:
    import vnpy_optionmaster.engine as engine_mod
    ui, chain = _manager()
    chain.use_synthetic = False
    chain.underlying_adjustment = 0
    portfolio = SimpleNamespace(name="TEST", chains={"CHAIN": chain})
    engine = object.__new__(OptionEngine)
    engine.active_portfolios = {"TEST": portfolio}
    monkeypatch.setattr(engine_mod, "load_json", lambda path: {
        "pricing_impvs": {"TEST": {"CHAIN_105": 0.31, "CHAIN_95": 0.35}}
    })
    engine.load_data()
    assert chain.calls["105"].pricing_impv == 0.31
    assert chain.puts["95"].pricing_impv == 0.35


def test_missing_atm_pair_never_publishes_synthetic_quote() -> None:
    from vnpy_optionmaster.base import ChainData

    chain = object.__new__(ChainData)
    chain.atm_index = "100"
    chain.atm_price = 100.0
    chain.calls = {"100": SimpleNamespace(mid_price=3.0)}
    chain.puts = {}
    chain.underlying = SimpleNamespace(mid_price=100.0)
    chain.underlying_adjustment = 1.0

    chain.calculate_underlying_adjustment()
    chain.update_synthetic_price()

    assert chain.underlying.mid_price == 100.0
    assert chain.underlying_adjustment == 1.0


def test_missing_otm_contract_disables_pricing_control() -> None:
    ui, chain = _manager()
    chain.atm_price = 110.0  # the 105 call is ITM; its OTM put is absent
    PricingVolatilityManager.update_pricing_impv(ui, "CHAIN")
    missing_spin = ui.cells[("CHAIN", "105")]["pricing_impv"]
    assert missing_spin.enabled is False
    assert missing_spin.value is None
    valid_spin = ui.cells[("CHAIN", "95")]["pricing_impv"]
    assert valid_spin.enabled is True


def test_save_and_reload_put_only_strike_pricing_iv(monkeypatch) -> None:
    """A valid put-only strike must survive restart even without a matching call."""
    import vnpy_optionmaster.engine as engine_module

    ui, chain = _manager()
    chain.underlying_adjustment = 0.0
    chain.use_synthetic = False
    portfolio = SimpleNamespace(name="TEST", chains={"CHAIN": chain})
    engine = object.__new__(OptionEngine)
    engine.active_portfolios = {"TEST": portfolio}

    saved: dict = {}
    monkeypatch.setattr(engine_module, "save_json", lambda name, payload: saved.update(payload))
    engine.save_data()

    expected_iv = chain.puts["95"].pricing_impv
    assert saved["pricing_impvs"]["TEST"]["CHAIN_95"] == expected_iv

    chain.puts["95"].pricing_impv = 0.05
    monkeypatch.setattr(engine_module, "load_json", lambda name: saved)
    engine.load_data("TEST")
    assert chain.puts["95"].pricing_impv == expected_iv
