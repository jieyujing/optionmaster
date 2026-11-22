"""Portfolio initialization may fail without leaking partially registered instruments."""
from types import SimpleNamespace

import pytest
from vnpy.trader.constant import Exchange, Product
from vnpy.trader.object import ContractData

from vnpy_optionmaster.engine import OptionEngine


class _Instrument:
    def __init__(self, symbol: str):
        self.vt_symbol = symbol
        self.exchange = Exchange.CFFEX
        self.underlying = object()
        self.long_pos = 0
        self.short_pos = 0
        self.net_pos = 0

    def update_holding(self, holding):
        self.long_pos = holding.long_pos
        self.short_pos = holding.short_pos
        self.net_pos = self.long_pos - self.short_pos


def _setup_init(*, fail_converter=False, fail_load=False):
    a = _Instrument("A.CFFEX")
    b = _Instrument("B.CFFEX")
    portfolio = SimpleNamespace(
        name="TEST", underlyings={a.vt_symbol: a, b.vt_symbol: b},
        options={}, chains={}, pos_delta=17.0,
        calculate_pos_greeks=lambda: None,
    )
    contracts = {
        symbol: ContractData(gateway_name=("MISSING" if fail_converter and symbol == b.vt_symbol else "TEST"),
                             symbol=symbol.split(".")[0], exchange=Exchange.CFFEX,
                             name=symbol, product=Product.FUTURES,
                             size=1, pricetick=0.2)
        for symbol in (a.vt_symbol, b.vt_symbol)
    }
    class _Main:
        def get_contract(self, symbol):
            return contracts.get(symbol)
        def get_converter(self, gateway_name):
            if gateway_name == "MISSING":
                return None
            return SimpleNamespace(get_position_holding=lambda symbol: SimpleNamespace(long_pos=2, short_pos=0))
    engine = object.__new__(OptionEngine)
    engine.main_engine = _Main()
    engine.portfolios = {"TEST": portfolio}
    engine.active_portfolios = {}
    engine.subscription_uncertain = {}
    engine.instruments = {}
    engine.get_portfolio = lambda name: portfolio
    calls = []
    engine.subscribe_data = lambda symbol: calls.append(symbol)
    def _load(portfolio_name):
        assert portfolio_name == "TEST"
        if fail_load:
            raise RuntimeError("load_data failed after position update")
    engine.load_data = _load
    return engine, (a, b), calls


def test_converter_preflight_prevents_early_subscriptions() -> None:
    engine, instruments, calls = _setup_init(fail_converter=True)
    with pytest.raises(RuntimeError, match="converter"):
        engine.init_portfolio("TEST")
    assert calls == []
    assert engine.instruments == {}
    assert engine.active_portfolios == {}
    assert [(x.long_pos, x.short_pos) for x in instruments] == [(0, 0), (0, 0)]


def test_load_failure_rolls_back_registered_instruments_and_holdings() -> None:
    engine, instruments, calls = _setup_init(fail_load=True)
    with pytest.raises(RuntimeError, match="load_data"):
        engine.init_portfolio("TEST")
    assert engine.active_portfolios == {}
    assert engine.instruments == {}
    assert [(x.long_pos, x.short_pos, x.net_pos) for x in instruments] == [
        (0, 0, 0), (0, 0, 0),
    ]
    assert calls == []


def test_success_subscribes_once_and_second_init_is_noop() -> None:
    engine, instruments, calls = _setup_init()
    assert engine.init_portfolio("TEST")
    assert len(calls) == 2
    assert set(engine.instruments) == {"A.CFFEX", "B.CFFEX"}
    assert [x.long_pos for x in instruments] == [2, 2]
    assert not engine.init_portfolio("TEST")
    assert len(calls) == 2


def test_retry_after_load_failure_keeps_exactly_one_subscription_per_symbol() -> None:
    engine, instruments, calls = _setup_init(fail_load=True)
    with pytest.raises(RuntimeError, match="load_data"):
        engine.init_portfolio("TEST")
    engine.load_data = lambda name: None  # configuration repaired
    assert engine.init_portfolio("TEST")
    assert set(calls) == {i.vt_symbol for i in instruments}
    assert len(calls) == 2
    assert not engine.init_portfolio("TEST")
    assert len(calls) == 2


def test_load_data_partial_pricing_mutation_is_rolled_back() -> None:
    engine, instruments, calls = _setup_init()
    option = SimpleNamespace(pricing_impv=0.21)
    chain = SimpleNamespace(
        underlying_adjustment=1.25, calls={"100": option}, puts={}, indexes=["100"],
    )
    portfolio = engine.get_portfolio("TEST")
    portfolio.chains["CHAIN"] = chain

    def corrupt_then_fail(name):
        assert name == "TEST"
        chain.underlying_adjustment = 11.0
        option.pricing_impv = 0.99
        raise RuntimeError("partial load mutation")

    engine.load_data = corrupt_then_fail
    with pytest.raises(RuntimeError, match="partial load"):
        engine.init_portfolio("TEST")
    assert chain.underlying_adjustment == 1.25
    assert option.pricing_impv == 0.21
    assert engine.active_portfolios == {}
    assert engine.instruments == {}
    assert calls == []


def test_subscribe_failure_rolls_back_local_state_but_external_effect_is_visible() -> None:
    engine, instruments, calls = _setup_init()

    def failing_subscribe(symbol):
        calls.append(symbol)
        if symbol == "B.CFFEX":
            raise RuntimeError("remote subscribe failed")

    engine.subscribe_data = failing_subscribe
    with pytest.raises(RuntimeError, match="remote subscribe"):
        engine.init_portfolio("TEST")
    assert engine.active_portfolios == {}
    assert engine.instruments == {}
    assert [(x.long_pos, x.short_pos) for x in instruments] == [(0, 0), (0, 0)]
    assert calls == ["A.CFFEX", "B.CFFEX"]  # not externally reversible without unsubscribe


def test_subscribe_failure_blocks_blind_retry_until_gateway_reset() -> None:
    engine, _, calls = _setup_init()
    def failed_subscribe(symbol):
        calls.append(symbol)
        if symbol == "B.CFFEX":
            raise RuntimeError("gateway subscription uncertain")
    engine.subscribe_data = failed_subscribe

    with pytest.raises(RuntimeError, match="gateway subscription uncertain"):
        engine.init_portfolio("TEST")
    assert engine.active_portfolios == {}
    assert engine.subscription_uncertain["TEST"] == {"A.CFFEX", "B.CFFEX"}

    with pytest.raises(RuntimeError, match="[Uu]ncertain"):
        engine.init_portfolio("TEST")
    assert calls == ["A.CFFEX", "B.CFFEX"]  # no blind second attempt


def test_gateway_log_failure_does_not_skip_portfolio_rollback() -> None:
    engine, instruments, calls = _setup_init()

    def failed_subscribe(symbol):
        calls.append(symbol)
        raise RuntimeError("remote reject")

    def failed_log(*args):
        raise OSError("disk full")

    engine.subscribe_data = failed_subscribe
    engine.main_engine.write_log = failed_log

    with pytest.raises(RuntimeError, match="remote reject"):
        engine.init_portfolio("TEST")
    assert engine.active_portfolios == {}
    assert engine.instruments == {}
    assert [x.net_pos for x in instruments] == [0, 0]
    assert engine.subscription_uncertain["TEST"] == {"A.CFFEX"}
