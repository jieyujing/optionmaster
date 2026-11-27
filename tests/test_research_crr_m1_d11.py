"""D11: research-only CRR dispatch, exact scalar parity and IV contracts."""
from dataclasses import replace
from itertools import product

import pytest

from vnpy_optionmaster import research_pricing
from vnpy_optionmaster.pricing import binomial_tree
from vnpy_optionmaster.pricing.iv_solver import IVStatus
from vnpy_optionmaster.research_pricing import ResearchPricingModel
from vnpy_optionmaster.research_risk import ResearchRiskEngine, ResearchValuationRequest, ResearchValuationStatus

MODEL = ResearchPricingModel.BINOMIAL_TREE


def test_research_dispatch_does_not_call_live_scalar_kernel(monkeypatch):
    args = (60580., 61000., .0144, 48/365, .27, 1, 128)
    expected = binomial_tree.calculate_greeks(*args)
    mark = float(expected[0])
    expected_iv = research_pricing.research_iv(MODEL, mark, *args[:4], *args[5:])
    assert expected_iv.valid

    def forbidden(*_args, **_kwargs):
        raise AssertionError("research must not call scalar live CRR")

    monkeypatch.setattr(binomial_tree, "calculate_greeks", forbidden)
    monkeypatch.setattr(binomial_tree, "calculate_price", forbidden)
    monkeypatch.setattr(binomial_tree, "calculate_vega", forbidden)
    assert research_pricing.research_greeks(MODEL, *args) == expected
    assert research_pricing.research_iv(MODEL, mark, *args[:4], *args[5:]) == expected_iv


CASES = (
    (80., 100., .05, .5, .24),
    (100., 100., 0., 1., .2),
    (120., 100., -.03, 2., .45),
    (100., 100., .03, 1/365, .01),
    (100., 100., -.01, .25, 5.),
    (60580., 61000., .0144, 48/365, 1e-6),
    (100., 100., .03, 0., .2),
    (120., 100., .03, .5, 0.),
)


@pytest.mark.parametrize("case,right,steps", tuple(product(CASES, (-1, 1), (2, 15, 64, 128, 512))))
def test_price_and_all_greeks_exactly_equal_scalar(case, right, steps):
    from vnpy_optionmaster import research_crr

    args = (*case, right, steps)
    assert research_crr.calculate_price(*args) == binomial_tree.calculate_price(*args)
    assert research_crr.calculate_vega(*args) == binomial_tree.calculate_vega(*args)
    expected = binomial_tree.calculate_greeks(*args)
    actual = research_crr.calculate_greeks(*args)
    assert actual == expected
    assert tuple(float(x).hex() for x in actual) == tuple(float(x).hex() for x in expected)
    assert research_pricing.research_greeks(MODEL, *args) == expected


@pytest.mark.parametrize("right,rate,sigma", [(-1,.04,.24),(1,0.,.2),(1,-.02,.8),(-1,.01,2.)])
def test_iv_result_exact_and_round_trip(monkeypatch, right, rate, sigma):
    from vnpy_optionmaster import research_crr

    args = (100., 100., rate, .5, sigma, right, 128)
    mark = float(binomial_tree.calculate_price(*args))
    iv_args = (MODEL, mark, *args[:4], *args[5:])
    actual = research_pricing.research_iv(*iv_args)
    monkeypatch.setattr(research_pricing, "_binomial_kernel", binomial_tree)
    expected = research_pricing.research_iv(*iv_args)
    assert actual == expected
    assert actual.status is IVStatus.SUCCESS
    assert actual.volatility == pytest.approx(sigma, abs=1e-6, rel=0)
    assert actual.volatility is not None
    assert research_crr.calculate_price(*args[:4], actual.volatility, *args[5:]) == pytest.approx(mark, abs=1e-8, rel=0)


@pytest.mark.parametrize("mark,f,k,t,right,steps", [(0.,100.,100.,.5,1,128),(1000.,100.,100.,.5,1,128),(20.,80.,100.,.5,-1,128),(5.,100.,100.,0.,1,128),(5.,100.,100.,.5,1,1)])
def test_iv_failure_contract_matches_scalar(monkeypatch, mark, f, k, t, right, steps):
    args = (MODEL, mark, f, k, .03, t, right, steps)
    actual = research_pricing.research_iv(*args)
    monkeypatch.setattr(research_pricing, "_binomial_kernel", binomial_tree)
    assert actual == research_pricing.research_iv(*args)
    assert not actual.valid


@pytest.mark.parametrize("updates,reason", [
    ({"implied_volatility": 1e-300}, "NUMERICAL_FAILURE"),
    ({"risk_free_rate": -1e308}, "NUMERICAL_FAILURE"),
    ({"underlying_price": float("inf")}, "INVALID_INPUT"),
    ({"tree_steps": 513}, "INVALID_INPUT"),
    ({"implied_volatility": None, "reference_option_price": 1000.}, "IV_NO_SOLUTION"),
])
def test_public_risk_unavailable_matches_scalar(monkeypatch, updates, reason):
    request = ResearchValuationRequest(
        "test-C", "test", 100., 100., .03, .5, .2, 1, 2, 5.,
        model_name=MODEL, tree_steps=128,
    )
    request = replace(request, **updates)
    actual = ResearchRiskEngine.evaluate(request)
    monkeypatch.setattr(research_pricing, "_binomial_kernel", binomial_tree)
    expected = ResearchRiskEngine.evaluate(request)
    assert actual == expected
    assert actual.status is ResearchValuationStatus.UNAVAILABLE
    assert actual.reason_code == reason
    assert actual.baseline_value is actual.scenario_value is actual.value_change is None
    assert actual.baseline_greeks is actual.scenario_greeks is None
