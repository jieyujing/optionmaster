"""Actual mocked gateway seams must not send against a changed risk snapshot."""
from vnpy.trader.constant import Direction, Offset

from test_hedge_orders import _run_hedge
from test_mark_quality import _real_option_pair
from test_algo_orders import _make_algo, _tick


def test_delta_hedge_rechecks_portfolio_after_sizing(monkeypatch):
    result = _run_hedge(
        pos_delta=-6.0, long_pos=0, long_pos_frozen=0,
        short_pos=0, short_pos_frozen=0,
    )
    result.calls.clear()
    result.hedge.active_orderids.clear()
    engine = result.hedge.option_engine
    portfolio = engine._portfolio
    source = engine.main_engine.get_converter

    def changed(gateway):
        portfolio.pos_delta -= 2.0
        return source(gateway)

    monkeypatch.setattr(engine.main_engine, "get_converter", changed)
    result.hedge.run()
    assert result.calls == []


def test_delta_hedge_rechecks_held_option_after_sizing(monkeypatch):
    result = _run_hedge(
        pos_delta=-6.0, long_pos=0, long_pos_frozen=0,
        short_pos=0, short_pos_frozen=0,
    )
    result.calls.clear()
    result.hedge.active_orderids.clear()
    _, option, _ = _real_option_pair()
    engine = result.hedge.option_engine
    engine._portfolio.options[option.vt_symbol] = option
    source = engine.main_engine.get_converter

    def stale(gateway):
        option.greeks_valid = False
        return source(gateway)

    monkeypatch.setattr(engine.main_engine, "get_converter", stale)
    result.hedge.run()
    assert result.calls == []


def test_electronic_eye_rechecks_risk_after_gateway_quote_read(monkeypatch):
    algo, main, engine = _make_algo(short_pos=0, net_pos=0, tick=_tick(10))
    algo.trading_active = True
    original = main.get_tick
    calls = 0

    def changed(symbol):
        nonlocal calls
        calls += 1
        tick = original(symbol)
        if calls == 2:
            algo.option.greeks_valid = False
        return tick

    monkeypatch.setattr(main, "get_tick", changed)
    orderid = engine.send_order(algo, algo.vt_symbol, Direction.LONG, Offset.OPEN, 1.8, 1)
    assert orderid == ""
    assert not main.calls


def test_quarantined_hedge_underlying_cannot_send_even_without_option_holdings():
    result = _run_hedge(
        pos_delta=-6.0, long_pos=0, long_pos_frozen=0,
        short_pos=0, short_pos_frozen=0,
    )
    result.calls.clear()
    result.hedge.active_orderids.clear()
    instrument = result.hedge.option_engine._instrument
    instrument.recovery_quarantined = True
    result.hedge.run()
    assert result.calls == []


def test_electronic_eye_quarantined_option_always_blocks_send():
    algo, main, engine = _make_algo(short_pos=0, net_pos=0, tick=_tick(10))
    algo.trading_active = False
    algo.option.recovery_quarantined = True
    assert engine.send_order(
        algo, algo.vt_symbol, Direction.LONG, Offset.OPEN, 1.8, 1
    ) == ""
    assert main.calls == []


def test_inactive_electronic_eye_cannot_send_through_direct_sender():
    algo, main, engine = _make_algo(
        short_pos=0, net_pos=0, tick=_tick(10), active=False,
    )
    assert not algo.trading_active
    assert engine.send_order(
        algo, algo.vt_symbol, Direction.LONG, Offset.OPEN, 1.8, 1
    ) == ""
    assert main.calls == []
