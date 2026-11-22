"""Automatic trading must not consume stale or missing market/Greek snapshots."""
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

from test_hedge_orders import _run_hedge
from test_algo_orders import _make_algo, _tick, _ALGO_BID_PRICE


def test_hedging_refuses_stale_underlying_quote() -> None:
    result = _run_hedge(
        pos_delta=-6.0, long_pos=0, long_pos_frozen=0,
        short_pos=0, short_pos_frozen=0,
    )
    assert result.calls
    result.calls.clear()
    result.hedge.active_orderids.clear()
    result.tick.datetime = datetime.now() - timedelta(minutes=10)
    result.hedge.run()
    assert result.calls == []


def test_hedging_refuses_missing_held_option_quote_even_if_greeks_valid() -> None:
    result = _run_hedge(
        pos_delta=-6.0, long_pos=0, long_pos_frozen=0,
        short_pos=0, short_pos_frozen=0,
    )
    result.calls.clear()
    result.hedge.active_orderids.clear()
    option = SimpleNamespace(
        net_pos=1, greeks_valid=True,
        tick=None, underlying=SimpleNamespace(tick=result.tick),
    )
    result.hedge.option_engine._portfolio.options["option"] = option
    result.hedge.run()
    assert result.calls == []


def test_electronic_eye_refuses_stale_option_or_underlying_quote() -> None:
    tick = _tick(10)
    tick.datetime = datetime.now()
    algo, main_engine, _ = _make_algo(short_pos=0, net_pos=0, tick=tick)
    algo.option.greeks_valid = True
    algo.option.underlying.tick = tick
    algo.long_allowed = True
    algo.short_allowed = False
    algo.target_pos = 0
    algo.max_pos = 5
    algo.max_order_size = 5
    algo.algo_bid_price = _ALGO_BID_PRICE
    algo.option.tick.datetime = datetime.now() - timedelta(minutes=15)
    algo.do_trading()
    assert main_engine.calls == []

    algo.option.tick.datetime = datetime.now()
    algo.option.underlying.tick = _tick(10)
    algo.option.underlying.tick.datetime = datetime.now() - timedelta(minutes=15)
    algo.do_trading()
    assert main_engine.calls == []


def test_missing_or_future_quote_timestamp_is_rejected() -> None:
    from vnpy_optionmaster.risk_safety import quote_is_fresh

    tick = _tick(10)
    now = datetime.now()
    tick.datetime = now
    assert quote_is_fresh(tick, now=now)
    tick.datetime = now - timedelta(seconds=31)
    assert not quote_is_fresh(tick, now=now)
    tick.datetime = now + timedelta(minutes=5)
    assert not quote_is_fresh(tick, now=now)
    tick.datetime = None
    assert not quote_is_fresh(tick, now=now)
    tick.datetime = datetime.now(timezone.utc)
    assert quote_is_fresh(tick)
