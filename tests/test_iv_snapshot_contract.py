"""Main-owner risk snapshot commit invariants (offline, real OptionData)."""
from dataclasses import replace
from types import SimpleNamespace

from vnpy_optionmaster.pricing.iv_worker import WorkerCache, calculate_batch
from vnpy_optionmaster.pricing.iv_snapshot import capture_option_iv, commit_option_iv
from vnpy_optionmaster.risk_safety import risk_snapshot_is_fresh
from test_mark_quality import _real_option_pair
from vnpy.trader.object import TickData


def test_current_worker_result_commits_full_precision_and_current_position():
    underlying, option, _ = _real_option_pair()
    touched = []
    option.chain = SimpleNamespace(calculate_pos_greeks=lambda: touched.append("chain"))
    option.portfolio = SimpleNamespace(calculate_pos_greeks=lambda: touched.append("portfolio"))
    snapshot = capture_option_iv(option, model="black_76", generation=1, sequence=1)
    assert snapshot is not None
    result = calculate_batch(snapshot.request, WorkerCache(32))
    option.greeks_valid = False
    option.net_pos = 5
    assert commit_option_iv(option, snapshot, result)
    assert risk_snapshot_is_fresh(option)
    assert option.pos_delta == option.theo_delta * 5
    assert option.mid_impv == result.mid.volatility
    assert touched == ["chain", "portfolio"]


def test_new_underlying_tick_invalidates_old_worker_result():
    underlying, option, _ = _real_option_pair()
    snapshot = capture_option_iv(option, model="black_76", generation=1, sequence=1)
    assert snapshot is not None
    reply = calculate_batch(snapshot.request, WorkerCache(8))
    original = option.mid_impv
    prior = underlying.tick
    underlying.tick = TickData(
        gateway_name=prior.gateway_name, symbol=prior.symbol,
        exchange=prior.exchange, datetime=prior.datetime,
        bid_price_1=99.5, ask_price_1=100.5,
        bid_volume_1=10, ask_volume_1=10,
    )
    assert not commit_option_iv(option, snapshot, reply)
    assert option.mid_impv == original


def test_model_configuration_mutation_invalidates_old_worker_result():
    _, option, _ = _real_option_pair()
    snapshot = capture_option_iv(option, model="black_76", generation=1, sequence=1)
    assert snapshot is not None
    result = calculate_batch(snapshot.request, WorkerCache(8))
    option.interest_rate += 0.01
    assert not commit_option_iv(option, snapshot, result)
    option.interest_rate -= 0.01
    assert not commit_option_iv(option, snapshot, replace(result, generation=2))


def test_worker_partial_iv_failure_does_not_restore_greek_readiness():
    _, option, _ = _real_option_pair()
    option.mark_valid = False
    option.tick.ask_price_1 = 0.0
    option.tick.ask_volume_1 = 0
    snapshot = capture_option_iv(option, model="black_76", generation=1, sequence=1)
    assert snapshot is not None
    result = calculate_batch(snapshot.request, WorkerCache(8))
    option.greeks_valid = False
    assert commit_option_iv(option, snapshot, result)
    assert option.bid_impv_valid is True
    assert option.ask_impv_valid is False
    assert option.mid_impv_valid is False
    assert option.greeks_valid is False
    assert not risk_snapshot_is_fresh(option)


def test_async_committed_risk_revalidates_worker_generation_and_pricing_inputs():
    _, option, _ = _real_option_pair()
    snapshot = capture_option_iv(option, model="black_76", generation=1, sequence=1)
    assert snapshot is not None
    result = calculate_batch(snapshot.request, WorkerCache(8))
    runtime = SimpleNamespace(
        worker=SimpleNamespace(alive=True, generation=1), pending={},
    )
    option.iv_runtime = runtime
    option.greeks_valid = False
    assert commit_option_iv(option, snapshot, result)
    assert risk_snapshot_is_fresh(option)

    option.interest_rate += 0.01
    assert not risk_snapshot_is_fresh(option)
    option.interest_rate -= 0.01
    option.underlying_adjustment += 0.5
    assert not risk_snapshot_is_fresh(option)
    option.underlying_adjustment -= 0.5
    runtime.worker.generation = 2
    assert not risk_snapshot_is_fresh(option)
