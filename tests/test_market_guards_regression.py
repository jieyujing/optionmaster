"""Regression suite for session, L1 book, ingress, and execution safety.

Previously failing scenarios remain in the normal pytest collection.
"""
from datetime import datetime, timedelta
from types import SimpleNamespace

import pytest
from vnpy.event import Event
from vnpy.trader.event import EVENT_TICK

import vnpy_optionmaster.risk_safety as safety
import vnpy_optionmaster.engine as engine_module
from vnpy_optionmaster.engine import OptionEngine
from test_hedge_orders import _run_hedge, _tick, _contract


def _hedge_ready():
    result = _run_hedge(
        pos_delta=-6.0, long_pos=0.0, long_pos_frozen=0.0,
        short_pos=0.0, short_pos_frozen=0.0
    )
    # Use an active December 2026 contract, not the historical IF2412 fixture.
    result.contract.symbol = "IF2612"
    result.tick.symbol = "IF2612"
    result.hedge.vt_symbol = result.contract.vt_symbol
    result.calls.clear()
    result.hedge.active_orderids.clear()
    result.hedge.active = True
    return result


def _inject_tick(tick, collected):
    engine = object.__new__(OptionEngine)
    portfolio = SimpleNamespace(update_tick=lambda value: collected.append(value))
    engine.instruments = {tick.vt_symbol: SimpleNamespace(portfolio=portfolio)}
    from _session_test_support import synthetic_session_policy
    engine.session_policy = synthetic_session_policy(tick.vt_symbol)
    engine.process_tick_event(Event(EVENT_TICK, tick))
    return engine


def test_weekend_new_timestamp_still_must_block_hedge(monkeypatch):
    result = _hedge_ready()
    fixed_sunday = datetime(2026, 11, 22, 14, 22, 0)
    monkeypatch.setattr(
        engine_module, "quote_is_fresh",
        lambda tick: safety.quote_is_fresh(tick, now=fixed_sunday)
    )
    result.tick.datetime = fixed_sunday
    # Even a forged/simulated Sunday quote cannot open a missing session.
    from vnpy_optionmaster.trading_sessions import SessionPolicy
    result.hedge.option_engine.session_policy = SessionPolicy((), {})
    result.hedge.run()
    assert result.calls == [], "fresh Sunday tick must never generate an automated order"


def test_missing_ask_volume_should_block_buy_order():
    result = _hedge_ready()
    result.tick.ask_price_1 = 10.0
    result.tick.ask_volume_1 = 0
    result.hedge.run()
    assert result.calls == [], "buy order requires a valid executable Ask"


def test_zero_ask_plus_payup_should_not_make_positive_fake_buy_price():
    result = _hedge_ready()
    result.tick.ask_price_1 = 0.0
    result.tick.ask_volume_1 = 0
    result.hedge.run()
    assert result.calls == [], "payup must not turn 0 Ask into a valid buy limit"


@pytest.mark.parametrize(("bid", "ask"), [
    (11.0, 10.0),  # Crossed BBO
    (10.0, 10.0),  # Locked BBO
])
def test_crossed_or_locked_book_should_block_hedge(bid, ask):
    result = _hedge_ready()
    result.tick.bid_price_1 = bid
    result.tick.ask_price_1 = ask
    result.hedge.run()
    assert result.calls == [], "crossed/locked BBO is not an executable normal book"


def test_crossed_tick_should_not_enter_portfolio_greeks_pipeline():
    tick = _tick(_contract(1))
    tick.bid_price_1 = tick.ask_price_1 + 1.0
    received = []
    _inject_tick(tick, received)
    assert received == [], "bad BBO must be checked before portfolio.update_tick()"


def test_out_of_order_same_contract_tick_should_not_replace_latest_snapshot():
    tick = _tick(_contract(1))
    tick.datetime = datetime.now()
    received = []
    engine = _inject_tick(tick, received)
    older = _tick(_contract(1))
    older.datetime = tick.datetime - timedelta(seconds=4)
    engine.process_tick_event(Event(EVENT_TICK, older))
    assert len(received) == 1, "older event must not regress risk snapshot"


def test_valid_one_sided_bid_does_not_need_ask_for_sell_only():
    result = _run_hedge(
        pos_delta=12.0, long_pos=0.0, long_pos_frozen=0.0,
        short_pos=0.0, short_pos_frozen=0.0
    )
    result.calls.clear()
    result.hedge.active_orderids.clear()
    result.tick.ask_price_1 = 0.0
    result.tick.ask_volume_1 = 0
    result.hedge.run()
    assert len(result.calls) == 1, "valid Bid may support sells; missing Ask is not globally invalid"

def test_valid_bid_only_must_not_create_half_price_underlying_midpoint():
    from vnpy_optionmaster.base import UnderlyingData

    contract = _contract(1)
    underlying = UnderlyingData(contract)
    tick = _tick(contract)
    tick.ask_price_1 = 0.0
    tick.ask_volume_1 = 0
    underlying.update_tick(tick)
    assert underlying.mid_price == 0.0 or underlying.mid_price >= tick.bid_price_1, (
        "single-side Bid must not fabricate a midprice at half the bid"
    )


@pytest.mark.parametrize(("delta", "limit_name", "limit_value"), [
    (-6.0, "limit_up", 10.5),
    (12.0, "limit_down", 9.0),
])
def test_hedge_payup_price_must_stay_within_daily_exchange_limit(
    delta, limit_name, limit_value
):
    result = _run_hedge(
        pos_delta=delta, long_pos=0.0, long_pos_frozen=0.0,
        short_pos=0.0, short_pos_frozen=0.0
    )
    result.calls.clear()
    result.hedge.active_orderids.clear()
    setattr(result.tick, limit_name, limit_value)
    result.hedge.run()
    if result.calls:
        price = result.calls[0][0].price
        assert price <= limit_value if limit_name == "limit_up" else price >= limit_value


def safety_session_closed():
    from vnpy_optionmaster.trading_sessions import SessionPolicy
    return SessionPolicy((), {})


def test_electronic_eye_weekend_tick_must_not_submit_order(monkeypatch):
    from test_algo_orders import _make_algo, _tick as _option_tick, _ALGO_BID_PRICE

    fixed_sunday = datetime(2026, 11, 22, 14, 22, 0)
    original = safety.quote_is_fresh
    monkeypatch.setattr(
        safety, "quote_is_fresh",
        lambda tick, *, now=None, max_age_seconds=30: original(
            tick, now=fixed_sunday, max_age_seconds=max_age_seconds
        )
    )
    tick = _option_tick(10)
    tick.datetime = fixed_sunday
    algo, main_engine, _ = _make_algo(short_pos=0, net_pos=0, tick=tick)
    algo.algo_engine.option_engine.session_policy = safety_session_closed()
    algo.option.greeks_valid = True
    algo.long_allowed = True
    algo.short_allowed = False
    algo.target_pos = 0
    algo.max_pos = 5
    algo.max_order_size = 5
    algo.algo_bid_price = _ALGO_BID_PRICE
    algo.trading_active = True
    algo.do_trading()
    assert main_engine.calls == [], "electronic eye cannot trade in closed exchange session"


def test_electronic_eye_crossed_book_must_not_submit_order():
    from test_algo_orders import _make_algo, _tick as _option_tick, _ALGO_BID_PRICE

    tick = _option_tick(10)
    tick.bid_price_1 = 1.35
    tick.ask_price_1 = 1.25
    algo, main_engine, _ = _make_algo(short_pos=0, net_pos=0, tick=tick)
    algo.option.greeks_valid = True
    algo.long_allowed = True
    algo.short_allowed = False
    algo.target_pos = 0
    algo.max_pos = 5
    algo.max_order_size = 5
    algo.algo_bid_price = _ALGO_BID_PRICE
    algo.trading_active = True
    algo.do_trading()
    assert main_engine.calls == [], "crossed option quote must not trigger electronic eye"
