"""D10: exact request-local pricing reuse, tested at the public grid seam."""
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from datetime import date, datetime, timedelta, timezone

import pytest

from vnpy_optionmaster import research_crr, research_pricing
from vnpy_optionmaster.pricing import binomial_tree
from vnpy_optionmaster.research_combination import ResearchAssetClass, ResearchCombinationLeg
from vnpy_optionmaster.research_grid import ResearchGridRequest, ResearchRiskGridEngine
from vnpy_optionmaster.research_pricing import ResearchPricingModel
from vnpy_optionmaster.research_risk import ResearchValuationRequest, ResearchValuationStatus
from vnpy_optionmaster.research_scenario import ResearchScenarioLeg, ResearchScenarioRequest
from vnpy_optionmaster.research_time import ResearchTimeEngine, ResearchTimeRequest

BASE = datetime(2022, 9, 7, 15, tzinfo=timezone(timedelta(hours=8)))


def request(*, from_marks=False, elapsed=1):
    legs = []
    for right, symbol, quantity in ((1, "C61000", 2), (-1, "P61000", -1)):
        mark = binomial_tree.calculate_price(60580, 61000, 0.0144, 48/365, 0.27, right, 64) if from_marks else None
        risk = ResearchValuationRequest(
            symbol, "SHFE.cu2211", 60580, 61000, .0144, 48/365,
            None if from_marks else .27, right, quantity, 5,
            model_name=ResearchPricingModel.BINOMIAL_TREE, reference_option_price=mark, tree_steps=64,
        )
        legs.append(ResearchScenarioLeg(
            ResearchCombinationLeg(risk, "SHFE", "CU", ResearchAssetClass.FUTURES_OPTION), "cu2211-expiry",
        ))
    time = ResearchTimeRequest(
        ResearchScenarioRequest(tuple(legs), parallel_price_shock=.02, parallel_iv_shock=.01),
        BASE, BASE + timedelta(days=elapsed), (("cu2211-expiry", date(2022, 10, 25)),),
    )
    return ResearchGridRequest(time, (-.05, 0, .05), (-.05, 0, .05), (-.5, 0, .03))


def assert_exact_point(point, timed, x, y):
    source = timed.scenario
    scenario = replace(source, additional_price_shock=source.additional_price_shock+x,
                       additional_iv_shock=source.additional_iv_shock+y)
    # This is the old direct T05 evaluation seam, outside a grid scope.
    old = ResearchTimeEngine.evaluate(replace(timed, scenario=scenario))
    assert point.status == old.status and point.reason_code == old.reason_code
    if old.status is ResearchValuationStatus.OK:
        assert point.baseline_value == old.baseline_value
        assert point.scenario_value == old.scenario_value
        assert point.value_change == old.value_change
        assert point.contributions == old.contributions
    else:
        assert point.baseline_value is point.scenario_value is point.value_change is None
        assert not point.contributions


def test_grid_calls_each_identical_model_input_once_and_releases_scope(monkeypatch):
    req = request()
    calls = Counter()
    original = research_crr.calculate_greeks

    def counted(*args, **kwargs):
        calls[args, tuple(sorted(kwargs.items()))] += 1
        return original(*args, **kwargs)

    monkeypatch.setattr(research_crr, "calculate_greeks", counted)
    first = ResearchRiskGridEngine.evaluate(req)
    assert first.status is ResearchValuationStatus.OK
    assert calls and max(calls.values()) == 1
    unique = len(calls)
    second = ResearchRiskGridEngine.evaluate(req)
    assert second == first
    assert len(calls) == unique and set(calls.values()) == {2}


@pytest.mark.parametrize("from_marks,elapsed", [(False, 0), (False, 1), (True, 1)])
def test_full_grid_exactly_matches_independent_unscoped_t05_evaluations(from_marks, elapsed):
    req = request(from_marks=from_marks, elapsed=elapsed)
    grid = ResearchRiskGridEngine.evaluate(req)
    assert grid.status is ResearchValuationStatus.OK
    for point in grid.price_curve:
        assert_exact_point(point, req.time_request, point.price_shock, 0.)
    for row in grid.heatmap_rows:
        for point in row:
            assert_exact_point(point, req.time_request, point.price_shock, point.iv_shock)
    assert all(x.status is ResearchValuationStatus.UNAVAILABLE for x in grid.heatmap_rows[0])
    assert grid.heatmap_rows[1][1].value_change == grid.configured_value_change


def test_repeated_iv_inversion_is_scoped_to_one_grid(monkeypatch):
    req = request(from_marks=True)
    calls = Counter()
    original = research_pricing._binomial_research_iv

    def counted(*args):
        calls[args] += 1
        return original(*args)

    monkeypatch.setattr(research_pricing, "_binomial_research_iv", counted)
    first = ResearchRiskGridEngine.evaluate(req)
    assert first.status is ResearchValuationStatus.OK
    assert len(calls) == 2 and set(calls.values()) == {1}
    assert ResearchRiskGridEngine.evaluate(req) == first
    assert set(calls.values()) == {2}


def test_two_parallel_grid_requests_do_not_share_memo(monkeypatch):
    req = request()
    calls = Counter()
    original = research_crr.calculate_greeks
    from threading import Lock
    lock = Lock()

    def counted(*args, **kwargs):
        with lock:
            calls[args, tuple(sorted(kwargs.items()))] += 1
        return original(*args, **kwargs)

    monkeypatch.setattr(research_crr, "calculate_greeks", counted)
    with ThreadPoolExecutor(max_workers=2) as pool:
        a = pool.submit(ResearchRiskGridEngine.evaluate, req)
        b = pool.submit(ResearchRiskGridEngine.evaluate, req)
        assert a.result(timeout=15) == b.result(timeout=15)
    assert calls and set(calls.values()) == {2}


def test_failed_request_does_not_pollute_next_request(monkeypatch):
    req = request()
    original = research_crr.calculate_greeks

    def failed(*args, **kwargs):
        raise ArithmeticError("test model failure")

    monkeypatch.setattr(research_crr, "calculate_greeks", failed)
    bad = ResearchRiskGridEngine.evaluate(req)
    assert bad.status is ResearchValuationStatus.UNAVAILABLE
    assert bad.reason_code == "NUMERICAL_FAILURE" and not bad.heatmap_rows
    monkeypatch.setattr(research_crr, "calculate_greeks", original)
    assert ResearchRiskGridEngine.evaluate(req).status is ResearchValuationStatus.OK
