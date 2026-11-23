"""OptionData-to-worker integration, entirely offline with fake option ticks."""
from datetime import datetime, timedelta
from time import monotonic, sleep
from vnpy.trader.object import TickData

from vnpy_optionmaster.pricing.iv_runtime import OptionIVRuntime
from vnpy_optionmaster.risk_safety import risk_snapshot_is_fresh
from test_mark_quality import _real_option_pair


def test_runtime_invalidates_new_tick_immediately_then_commits_current_risk():
    underlying, option, prior = _real_option_pair()
    assert risk_snapshot_is_fresh(option)
    runtime = OptionIVRuntime(cache_entries=16, queue_capacity=2, max_pending=8, max_priority_streak=2)
    try:
        assert runtime.start()
        assert runtime.attach(option)
        tick = TickData(
            gateway_name=prior.gateway_name, symbol=prior.symbol, exchange=prior.exchange,
            datetime=datetime.now(),
            bid_price_1=2.5, ask_price_1=3.0, bid_volume_1=10, ask_volume_1=10,
        )
        option.update_tick(tick)
        assert not option.greeks_valid
        assert not risk_snapshot_is_fresh(option)
        deadline = monotonic() + 5
        while monotonic() < deadline and not option.greeks_valid:
            runtime.pump()
            sleep(0.005)
        assert option.greeks_valid
        assert option.greeks_quote_version == (tick, underlying.tick)
        assert risk_snapshot_is_fresh(option)
    finally:
        runtime.close()


def test_runtime_blocked_worker_keeps_old_risk_revoked():
    _, option, old = _real_option_pair()
    runtime = OptionIVRuntime(cache_entries=16, queue_capacity=1, max_pending=1, max_priority_streak=1)
    try:
        assert runtime.start()
        assert runtime.attach(option)
        runtime.worker.close()
        tick = TickData(
            gateway_name=old.gateway_name, symbol=old.symbol, exchange=old.exchange,
            datetime=old.datetime + timedelta(milliseconds=10),
            bid_price_1=3.0, ask_price_1=3.5, bid_volume_1=10, ask_volume_1=10,
        )
        option.update_tick(tick)
        runtime.pump()
        assert not option.greeks_valid
        assert not risk_snapshot_is_fresh(option)
    finally:
        runtime.close()
def test_worker_crash_rejects_even_previously_valid_greek_snapshot():
    _, option, _ = _real_option_pair()
    runtime = OptionIVRuntime(cache_entries=8, queue_capacity=1, max_pending=2, max_priority_streak=1)
    try:
        assert runtime.start()
        assert runtime.attach(option)
        assert not risk_snapshot_is_fresh(option)
        # Commit one Worker-owned snapshot before simulating process death.
        latest = TickData(
            gateway_name=option.tick.gateway_name,
            symbol=option.tick.symbol, exchange=option.tick.exchange,
            datetime=datetime.now(), bid_price_1=2.5, ask_price_1=3.0,
            bid_volume_1=10, ask_volume_1=10,
        )
        option.update_tick(latest)
        deadline = monotonic() + 5
        while monotonic() < deadline and not risk_snapshot_is_fresh(option):
            runtime.pump()
            sleep(0.005)
        assert risk_snapshot_is_fresh(option)
        runtime.worker.close()
        # No timer or new Tick is required for order-time revalidation.
        assert not risk_snapshot_is_fresh(option)
    finally:
        runtime.close()


def test_worker_close_cannot_silently_hot_switch_to_sync_pricing():
    _, option, last = _real_option_pair()
    runtime = OptionIVRuntime(cache_entries=8, queue_capacity=1, max_pending=2, max_priority_streak=1)
    assert runtime.start()
    assert runtime.attach(option)
    runtime.close()
    assert option.iv_runtime is runtime
    assert not option.greeks_valid
    later = TickData(
        gateway_name=last.gateway_name, symbol=last.symbol,
        exchange=last.exchange, datetime=datetime.now(),
        bid_price_1=2.5, ask_price_1=3.0,
        bid_volume_1=10, ask_volume_1=10,
    )
    option.update_tick(later)
    assert not option.greeks_valid
    assert not risk_snapshot_is_fresh(option)


def test_attaching_worker_revokes_previous_synchronous_snapshot_immediately():
    _, option, _ = _real_option_pair()
    assert risk_snapshot_is_fresh(option)
    runtime = OptionIVRuntime(cache_entries=8, queue_capacity=2, max_pending=4, max_priority_streak=1)
    try:
        assert runtime.start()
        assert runtime.attach(option)
        assert not option.greeks_valid
        assert not risk_snapshot_is_fresh(option)
        assert option.greeks_quote_version is None
    finally:
        runtime.close()


def test_runtime_must_not_restart_a_stopped_worker_with_old_dispatch_state():
    _, option, _ = _real_option_pair()
    runtime = OptionIVRuntime(
        cache_entries=8, queue_capacity=2, max_pending=4,
        max_priority_streak=1,
    )
    assert runtime.start()
    assert runtime.attach(option)
    runtime.worker.close()  # Abnormal process exit, not a clean new epoch.
    assert not risk_snapshot_is_fresh(option)
    assert not runtime.start()
    assert not runtime.worker.alive
    runtime.close()
    assert not runtime.start()
    assert not runtime.attach(option)
    assert not risk_snapshot_is_fresh(option)
