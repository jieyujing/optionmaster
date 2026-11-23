"""Full-portfolio risk publication must never expose a partial Worker epoch.

Every case uses synthetic market data and the real portfolio/option path.
"""
from datetime import datetime
from time import monotonic, sleep

from vnpy.event import Event
from vnpy.trader.constant import Product
from vnpy.trader.event import EVENT_TICK
from vnpy.trader.object import ContractData

from test_iv_chain_burst import _book, _real_chain_fixture
from test_hedge_orders import _run_hedge
from vnpy_optionmaster.pricing.iv_runtime import OptionIVRuntime
from vnpy_optionmaster.risk_safety import risk_snapshot_is_fresh
from vnpy_optionmaster.trading_sessions import CHINA_TZ


def test_actionable_portfolio_epoch_rechecks_held_option_dependencies() -> None:
    _, portfolio, _, options = _real_chain_fixture(option_count=6)
    snapshot = portfolio.actionable_risk_snapshot
    assert snapshot is not None
    assert snapshot.pos_delta == portfolio.pos_delta

    held = next(option for option in options if option.net_pos)
    held.greeks_valid = False
    # The last-known display numbers remain visible but are no longer tradable.
    assert portfolio.actionable_risk_snapshot is None
    portfolio.calculate_pos_greeks()
    assert portfolio.actionable_risk_snapshot is None


def test_actionable_portfolio_epoch_rejects_stale_chain_aggregation() -> None:
    _, portfolio, _, options = _real_chain_fixture(option_count=6)
    held = next(option for option in options if option.net_pos)
    assert portfolio.actionable_risk_snapshot is not None

    # Force a partially updated position flow: OptionData has the newest
    # position Greek, but ChainData still holds the old aggregate.
    held.net_pos += 1
    held.calculate_pos_greeks()
    portfolio.calculate_pos_greeks()
    assert portfolio.actionable_risk_snapshot is None

    held.chain.calculate_pos_greeks()
    portfolio.calculate_pos_greeks()
    assert portfolio.actionable_risk_snapshot is not None


def test_worker_partial_results_never_publish_actionable_portfolio_epoch() -> None:
    engine, portfolio, underlying, options = _real_chain_fixture(option_count=6)
    runtime = OptionIVRuntime(
        cache_entries=64, queue_capacity=2, max_pending=12,
        max_priority_streak=2, inflight_timeout_seconds=5.0,
    )
    try:
        assert runtime.start()
        assert all(runtime.attach(option) for option in options)
        engine.iv_runtime = runtime
        assert portfolio.actionable_risk_snapshot is None

        prior = underlying.tick
        assert prior is not None
        contract = ContractData(
            gateway_name=prior.gateway_name, symbol=prior.symbol,
            exchange=prior.exchange, name="synthetic",
            product=Product.FUTURES, size=10, pricetick=0.1,
        )
        engine.process_tick_event(Event(
            EVENT_TICK, _book(
                contract, bid=100, ask=100.2, at=datetime.now(CHINA_TZ),
            ),
        ))

        held = [option for option in options if option.net_pos]
        observed_partial = False
        deadline = monotonic() + 6.0
        while monotonic() < deadline:
            runtime.pump()
            ready = sum(risk_snapshot_is_fresh(option) for option in held)
            if 0 < ready < len(held):
                observed_partial = True
                assert portfolio.actionable_risk_snapshot is None
            if ready == len(held):
                break
            sleep(0.001)
        assert observed_partial
        assert all(risk_snapshot_is_fresh(option) for option in held)
        snapshot = portfolio.actionable_risk_snapshot
        assert snapshot is not None
        assert snapshot.pos_delta == portfolio.pos_delta
        assert snapshot.pos_gamma == portfolio.pos_gamma

        # A position change without recalculation still invalidates publication.
        held[0].net_pos += 1
        assert portfolio.actionable_risk_snapshot is None
    finally:
        runtime.close()


def test_hedge_sender_requires_a_current_portfolio_aggregate_epoch() -> None:
    _, portfolio, _, _ = _real_chain_fixture(option_count=6)
    result = _run_hedge(
        pos_delta=-6.0, long_pos=0, long_pos_frozen=0,
        short_pos=0, short_pos_frozen=0,
    )
    result.calls.clear()
    result.hedge.active_orderids.clear()
    result.hedge.option_engine._portfolio = portfolio
    result.hedge.delta_target = 40
    result.hedge.delta_range = 1

    assert portfolio.actionable_risk_snapshot is not None
    result.hedge.run()
    assert len(result.calls) == 1

    # A corrupted/mixed aggregate must not bypass the real send boundary,
    # even while every held option's individual Greek remains fresh.
    result.calls.clear()
    result.hedge.active_orderids.clear()
    portfolio.pos_delta += 100.0
    assert portfolio.actionable_risk_snapshot is None
    result.hedge.run()
    assert result.calls == []
