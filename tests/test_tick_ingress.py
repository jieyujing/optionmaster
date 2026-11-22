"""True OptionEngine EVENT_TICK path; invalid ticks must never reach risk state."""
from datetime import datetime, timedelta
from types import SimpleNamespace
from vnpy.event import Event
from vnpy.trader.event import EVENT_TICK
from vnpy_optionmaster.engine import OptionEngine
from vnpy_optionmaster.trading_sessions import CHINA_TZ, SessionPolicy

from _session_test_support import synthetic_session_policy
from test_hedge_orders import _tick, _contract


def _engine():
    tick = _tick(_contract(1))
    tick.datetime = datetime.now(CHINA_TZ)
    changes: list = []
    engine = object.__new__(OptionEngine)
    portfolio = SimpleNamespace(update_tick=lambda value: changes.append(value))
    engine.instruments = {tick.vt_symbol: SimpleNamespace(portfolio=portfolio, pricetick=0.5)}
    engine.session_policy = synthetic_session_policy(tick.vt_symbol)
    engine.tick_rejections = {}
    engine.last_accepted_ticks = {}
    return engine, tick, changes


def _send(engine, tick):
    engine.process_tick_event(Event(EVENT_TICK, tick))


def test_valid_tick_only_once_and_later_tick_is_accepted() -> None:
    engine, tick, changes = _engine()
    _send(engine, tick)
    _send(engine, tick)
    assert len(changes) == 1
    assert engine.tick_rejections["duplicate_tick"] == 1

    newer = _tick(_contract(1))
    newer.datetime = tick.datetime + timedelta(milliseconds=1)
    newer.last_price = 9.7
    _send(engine, newer)
    assert changes[-1] is newer
    assert len(changes) == 2


def test_same_exchange_source_older_tick_never_rolls_back() -> None:
    engine, tick, changes = _engine()
    _send(engine, tick)
    old = _tick(_contract(1))
    old.datetime = tick.datetime - timedelta(seconds=2)
    _send(engine, old)
    assert changes == [tick]
    assert engine.tick_rejections["out_of_order_tick"] == 1


def test_equal_timestamp_conflicting_quote_is_ambiguous_not_ordered() -> None:
    engine, tick, changes = _engine()
    _send(engine, tick)
    conflict = _tick(_contract(1))
    conflict.datetime = tick.datetime
    conflict.last_price = 10.0
    _send(engine, conflict)
    assert changes == [tick]
    assert engine.tick_rejections["ambiguous_equal_timestamp"] == 1


def test_crossed_tick_is_dropped_before_portfolio_update() -> None:
    engine, tick, changes = _engine()
    tick.bid_price_1 = tick.ask_price_1 + 0.5
    _send(engine, tick)
    assert not changes
    assert engine.tick_rejections["locked_or_crossed_book"] == 1


def test_invalid_price_size_or_stale_tick_is_not_trusted() -> None:
    engine, tick, changes = _engine()
    tick.bid_volume_1 = 0
    _send(engine, tick)
    assert not changes
    assert engine.tick_rejections["invalid_bid"] == 1

    tick.bid_volume_1 = 10
    tick.datetime = datetime.now(CHINA_TZ) - timedelta(seconds=90)
    _send(engine, tick)
    assert not changes
    assert engine.tick_rejections["stale_or_invalid_timestamp"] == 1


def test_missing_calendar_forbids_risk_mutation() -> None:
    engine, tick, changes = _engine()
    engine.session_policy = SessionPolicy((), {})
    _send(engine, tick)
    assert not changes
    assert engine.tick_rejections["session_closed_or_unverified"] == 1


def test_different_gateway_on_same_symbol_cannot_overwrite_unverified() -> None:
    engine, tick, changes = _engine()
    _send(engine, tick)
    newer = _tick(_contract(1))
    newer.datetime = tick.datetime + timedelta(seconds=1)
    newer.gateway_name = "UNVERIFIED-SECOND-GATEWAY"
    _send(engine, newer)
    assert changes == [tick]
    assert engine.tick_rejections["source_switch_requires_reconcile"] == 1
