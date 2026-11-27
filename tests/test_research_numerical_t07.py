"""M0 T07 numerical acceptance: independent Golden Cases and fail-closed gates.

All expected option prices come from independently evaluated textbook
closed-form formulas or a separately implemented two-step CRR recursion.
Tolerance policy is frozen in docs/m0-t07-numerical-acceptance.md.
No input originates from a market feed or from OptionMaster's pricing kernels.
"""
from dataclasses import replace
from datetime import date, datetime, timedelta, timezone
from math import erf, exp, fsum, log, sqrt

import pytest

from vnpy_optionmaster.pricing.iv_solver import IVStatus
from vnpy_optionmaster.research_combination import ResearchAssetClass, ResearchCombinationLeg
from vnpy_optionmaster.research_grid import ResearchGridRequest, ResearchRiskGridEngine
from vnpy_optionmaster.research_risk import (
    ResearchPricingModel,
    ResearchRiskEngine,
    ResearchValuationRequest,
    ResearchValuationStatus,
)
from vnpy_optionmaster.research_scenario import ResearchScenarioLeg, ResearchScenarioRequest
from vnpy_optionmaster.research_time import ResearchTimeRequest


PRICE_ATOL = 1e-8
GREEK_RTOL = 2e-3
GREEK_ATOL = 1e-3
IV_ATOL = 1e-6
AGG_ATOL = 1e-8
CN = timezone(timedelta(hours=8))
BASE = datetime(2025, 1, 1, 15, tzinfo=CN)


def _normal_cdf(z: float) -> float:
    return (1.0 + erf(z / sqrt(2.0))) / 2.0


def _closed_form(
    model: ResearchPricingModel,
    s: float,
    k: float,
    r: float,
    t: float,
    sigma: float,
    cp: int,
) -> float:
    """Independent reference: spot BS or discounted-futures Black-76."""
    drift = r if model is ResearchPricingModel.BLACK_SCHOLES else 0.0
    d1 = (log(s / k) + (drift + .5 * sigma * sigma) * t) / (sigma * sqrt(t))
    d2 = d1 - sigma * sqrt(t)
    if model is ResearchPricingModel.BLACK_SCHOLES:
        return cp * (
            s * _normal_cdf(cp * d1)
            - k * exp(-r*t) * _normal_cdf(cp * d2)
        )
    return cp * exp(-r*t) * (
        s * _normal_cdf(cp * d1)
        - k * _normal_cdf(cp * d2)
    )


def _one_leg(
    model: ResearchPricingModel = ResearchPricingModel.BLACK_76,
    *,
    s: float = 100.0,
    k: float = 100.0,
    rate: float = .05,
    years: float = 1.0,
    sigma: float | None = .2,
    cp: int = 1,
    q: int = 1,
    multiplier: float = 1.0,
    observed_price: float | None = None,
    steps: int = 15,
) -> ResearchValuationRequest:
    return ResearchValuationRequest(
        contract_id="TEST-OPTION",
        underlying_contract_id="TEST-UNDERLYING",
        underlying_price=s,
        strike=k,
        risk_free_rate=rate,
        time_to_expiry=years,
        implied_volatility=sigma,
        option_type=cp,
        signed_quantity=q,
        contract_multiplier=multiplier,
        model_name=model,
        reference_option_price=observed_price,
        tree_steps=steps,
        underlying_shock=0.0,
    )


@pytest.mark.parametrize("model,s,k,r,t,sigma,cp,expected", [
    (ResearchPricingModel.BLACK_SCHOLES, 42., 40., .1, .5, .2, 1, 4.759422392871532),
    (ResearchPricingModel.BLACK_SCHOLES, 42., 40., .1, .5, .2, -1, .8085993729000904),
    (ResearchPricingModel.BLACK_76, 100., 100., .05, 1., .2, 1, 7.57708214642728),
    (ResearchPricingModel.BLACK_76, 100., 100., .05, 1., .2, -1, 7.57708214642728),
])
def test_frozen_textbook_price_goldens(
    model: ResearchPricingModel, s: float, k: float, r: float, t: float,
    sigma: float, cp: int, expected: float,
) -> None:
    result = ResearchRiskEngine.evaluate(
        _one_leg(model, s=s, k=k, rate=r, years=t, sigma=sigma, cp=cp, q=-3, multiplier=5.0),
    )
    assert result.status is ResearchValuationStatus.OK
    assert result.baseline_value == pytest.approx(-15 * expected, rel=0.0, abs=PRICE_ATOL)
    assert result.scenario_value == pytest.approx(-15 * expected, rel=0.0, abs=PRICE_ATOL)
    assert result.value_change == pytest.approx(0.0, rel=0.0, abs=1e-12)


@pytest.mark.parametrize("model", [
    ResearchPricingModel.BLACK_SCHOLES,
    ResearchPricingModel.BLACK_76,
])
def test_call_put_parity_is_independently_enforced(model: ResearchPricingModel) -> None:
    s, k, r, t, v = 112.0, 105.0, .017, .7, .31
    call = ResearchRiskEngine.evaluate(_one_leg(model, s=s, k=k, rate=r, years=t, sigma=v))
    put = ResearchRiskEngine.evaluate(_one_leg(model, s=s, k=k, rate=r, years=t, sigma=v, cp=-1))
    assert call.status is put.status is ResearchValuationStatus.OK
    expected_difference = (
        s - k * exp(-r*t) if model is ResearchPricingModel.BLACK_SCHOLES
        else (s-k)*exp(-r*t)
    )
    assert call.baseline_value - put.baseline_value == pytest.approx(
        expected_difference, rel=0.0, abs=PRICE_ATOL,
    )


@pytest.mark.parametrize("model,cp", [
    (ResearchPricingModel.BLACK_SCHOLES, 1),
    (ResearchPricingModel.BLACK_SCHOLES, -1),
    (ResearchPricingModel.BLACK_76, 1),
    (ResearchPricingModel.BLACK_76, -1),
])
def test_greeks_match_independent_price_derivatives_and_units(
    model: ResearchPricingModel, cp: int,
) -> None:
    s, k, r, t, sigma = 107., 103., .03, .75, .27
    signed_scale = -3 * 10
    result = ResearchRiskEngine.evaluate(
        _one_leg(model, s=s, k=k, rate=r, years=t, sigma=sigma, cp=cp, q=-3, multiplier=10),
    )
    assert result.status is ResearchValuationStatus.OK
    assert result.baseline_greeks is not None

    def p(x: float, v: float, remaining: float) -> float:
        return _closed_form(model, x, k, r, remaining, v, cp)

    h_price, h_sigma, h_t = .02, .0002, .00001
    d = (p(s+h_price,sigma,t)-p(s-h_price,sigma,t))/(2*h_price)
    gamma = (p(s+h_price,sigma,t)-2*p(s,sigma,t)+p(s-h_price,sigma,t))/(h_price*h_price)
    vega = (p(s,sigma+h_sigma,t)-p(s,sigma-h_sigma,t))/(2*h_sigma)
    theta = -(p(s,sigma,t+h_t)-p(s,sigma,t-h_t))/(2*h_t)

    greeks = result.baseline_greeks
    assert greeks.delta == pytest.approx(signed_scale*d, rel=GREEK_RTOL, abs=GREEK_ATOL)
    assert greeks.gamma == pytest.approx(signed_scale*gamma, rel=GREEK_RTOL, abs=GREEK_ATOL)
    assert greeks.vega == pytest.approx(signed_scale*vega, rel=GREEK_RTOL, abs=GREEK_ATOL)
    assert greeks.theta == pytest.approx(signed_scale*theta, rel=GREEK_RTOL, abs=GREEK_ATOL)


def _two_step_american_put(
    f: float, k: float, r: float, t: float, sigma: float,
) -> float:
    """Reference 2-step exercise recursion independent of project tree code."""
    dt = t/2
    up = exp(sigma*sqrt(dt))
    down = 1/up
    prob_up = (1-down)/(up-down)
    disc = exp(-r*dt)
    values = [
        max(k-f*up*up, 0),
        max(k-f, 0),
        max(k-f*down*down, 0),
    ]
    values = [
        max(k-f*up, disc*(prob_up*values[0]+(1-prob_up)*values[1])),
        max(k-f*down, disc*(prob_up*values[1]+(1-prob_up)*values[2])),
    ]
    return max(k-f, disc*(prob_up*values[0]+(1-prob_up)*values[1]))


def test_american_put_tree_independent_golden_and_early_exercise_lower_bound() -> None:
    f, k, r, t, sigma = 80., 100., .05, .5, .24
    expected = _two_step_american_put(f, k, r, t, sigma)
    result = ResearchRiskEngine.evaluate(
        _one_leg(ResearchPricingModel.BINOMIAL_TREE, s=f, k=k, rate=r, years=t,
                 sigma=sigma, cp=-1, steps=2, q=2, multiplier=5),
    )
    assert result.status is ResearchValuationStatus.OK
    assert result.baseline_value == pytest.approx(10*expected, rel=0.0, abs=PRICE_ATOL)
    assert result.baseline_value >= 10*(k-f)


@pytest.mark.parametrize("model", [
    ResearchPricingModel.BLACK_SCHOLES,
    ResearchPricingModel.BLACK_76,
    ResearchPricingModel.BINOMIAL_TREE,
])
def test_price_iv_price_round_trip_offline(model: ResearchPricingModel) -> None:
    sigma = .312345
    f, k, r, t = 100., 105., .03, .5
    if model is ResearchPricingModel.BINOMIAL_TREE:
        reference = _two_step_american_put(f, k, r, t, sigma)
        cp, steps = -1, 2
    else:
        reference = _closed_form(model, f, k, r, t, sigma, 1)
        cp, steps = 1, 15
    result = ResearchRiskEngine.evaluate(
        _one_leg(model, s=f, k=k, rate=r, years=t, sigma=None, observed_price=reference, cp=cp, steps=steps),
    )
    assert result.status is ResearchValuationStatus.OK
    assert result.iv_result is not None and result.iv_result.status is IVStatus.SUCCESS
    assert result.implied_volatility == pytest.approx(sigma, rel=0.0, abs=IV_ATOL)
    assert result.baseline_value == pytest.approx(reference, rel=0.0, abs=PRICE_ATOL)


@pytest.mark.parametrize("model", [
    ResearchPricingModel.BLACK_SCHOLES,
    ResearchPricingModel.BLACK_76,
    ResearchPricingModel.BINOMIAL_TREE,
])
def test_impossible_iv_price_never_becomes_zero_volatility(model: ResearchPricingModel) -> None:
    result = ResearchRiskEngine.evaluate(
        _one_leg(model, sigma=None, observed_price=1000.0, steps=17),
    )
    assert result.status is ResearchValuationStatus.UNAVAILABLE
    assert result.reason_code == "IV_NO_SOLUTION"
    assert result.implied_volatility is None
    assert result.baseline_value is None
    assert result.baseline_greeks is None


@pytest.mark.parametrize("model,lower_bound", [
    (ResearchPricingModel.BLACK_SCHOLES, 100*exp(-.05*.01) - 80),
    (ResearchPricingModel.BLACK_76, 20*exp(-.05*.01)),
    (ResearchPricingModel.BINOMIAL_TREE, 20.0),
])
def test_low_vega_intrinsic_floor_does_not_claim_unique_iv(
    model: ResearchPricingModel, lower_bound: float,
) -> None:
    """A no-volatility price boundary cannot identify a unique implied IV."""
    result = ResearchRiskEngine.evaluate(
        _one_leg(
            model, s=80., k=100., rate=.05, years=.01,
            sigma=None, observed_price=lower_bound, cp=-1, steps=21,
        ),
    )
    assert result.status is ResearchValuationStatus.UNAVAILABLE
    assert result.reason_code == "IV_NO_SOLUTION"
    assert result.iv_result is not None and result.iv_result.status is IVStatus.NO_SOLUTION
    assert result.baseline_value is None
    assert result.baseline_greeks is None


@pytest.mark.parametrize("field", [
    "underlying_price", "strike", "risk_free_rate",
    "time_to_expiry", "implied_volatility", "contract_multiplier",
    "underlying_shock",
])
def test_arbitrarily_large_integer_inputs_fail_closed_not_throw(field: str) -> None:
    original = _one_leg()
    corrupted = replace(original, **{field: 10**1000})
    result = ResearchRiskEngine.evaluate(corrupted)
    assert result.status is ResearchValuationStatus.UNAVAILABLE
    assert result.reason_code == "INVALID_INPUT"
    assert result.baseline_value is None
    assert result.scenario_value is None


def test_arbitrarily_large_observed_option_price_fails_closed_not_throw() -> None:
    original = _one_leg(sigma=None, observed_price=10**1000)
    result = ResearchRiskEngine.evaluate(original)
    assert result.status is ResearchValuationStatus.UNAVAILABLE
    assert result.reason_code == "INVALID_INPUT"
    assert result.iv_result is None


def test_overflowed_signed_quantity_never_returns_partially_valid_greeks() -> None:
    original = _one_leg(q=10**1000)
    result = ResearchRiskEngine.evaluate(original)
    assert result.status is ResearchValuationStatus.UNAVAILABLE
    assert result.reason_code == "NUMERICAL_FAILURE"
    assert result.baseline_value is None
    assert result.baseline_greeks is None


def _research_grid() -> ResearchGridRequest:
    near = ResearchScenarioLeg(
        selected_leg=ResearchCombinationLeg(
            valuation=_one_leg(s=100., k=100., rate=.03, years=30/365,
                               sigma=.25, cp=1, q=2, multiplier=10),
            exchange="DCE", product_family_id="Y",
            asset_class=ResearchAssetClass.FUTURES_OPTION,
        ),
        expiry_id="near",
    )
    far = ResearchScenarioLeg(
        selected_leg=ResearchCombinationLeg(
            valuation=replace(
                _one_leg(s=120., k=115., rate=.03, years=60/365,
                         sigma=.30, cp=-1, q=-3, multiplier=5),
                contract_id="SECOND-PUT",
                underlying_contract_id="FUTURES-FAR",
            ),
            exchange="DCE", product_family_id="Y",
            asset_class=ResearchAssetClass.FUTURES_OPTION,
        ),
        expiry_id="far",
    )
    scenario = ResearchScenarioRequest(
        legs=(near, far),
        parallel_price_shock=.02,
        price_shocks_by_underlying=(("TEST-UNDERLYING", .05),),
        additional_price_shock=.01,
        parallel_iv_shock=-.01,
        iv_shocks_by_expiry=(("near", -.025),),
        additional_iv_shock=.005,
    )
    request = ResearchTimeRequest(
        scenario=scenario,
        reference_at=BASE,
        scenario_at=BASE+timedelta(days=7),
        expiry_boundaries=(
            ("near", date(2025,1,31)),
            ("far", date(2025,3,2)),
        ),
    )
    return ResearchGridRequest(
        time_request=request,
        curve_price_shocks=(-.01, 0., .01),
        heatmap_price_shocks=(-.01, 0., .01),
        heatmap_iv_shocks=(-.015, 0., .015),
    )


def test_independent_joint_price_iv_time_ratio_grid_golden() -> None:
    result = ResearchRiskGridEngine.evaluate(_research_grid())
    assert result.status is ResearchValuationStatus.OK
    base = (
        20*_closed_form(ResearchPricingModel.BLACK_76, 100, 100, .03, 30/365, .25, 1)
        - 15*_closed_form(ResearchPricingModel.BLACK_76, 120, 115, .03, 60/365, .30, -1)
    )
    assert result.baseline_value == pytest.approx(base, rel=0.0, abs=AGG_ATOL)
    origin = result.heatmap_rows[1][1]
    assert origin.status is ResearchValuationStatus.OK
    expected_origin = (
        20*_closed_form(ResearchPricingModel.BLACK_76, 106, 100, .03, 23/365, .230, 1)
        - 15*_closed_form(ResearchPricingModel.BLACK_76, 123.6, 115, .03, 53/365, .295, -1)
    )
    assert origin.scenario_value == pytest.approx(expected_origin, rel=0.0, abs=AGG_ATOL)
    assert origin.value_change == pytest.approx(expected_origin-base, rel=0.0, abs=AGG_ATOL)
    target = result.heatmap_rows[2][2]
    assert target.status is ResearchValuationStatus.OK
    expected_target = (
        20*_closed_form(ResearchPricingModel.BLACK_76, 107, 100, .03, 23/365, .245, 1)
        - 15*_closed_form(ResearchPricingModel.BLACK_76, 124.8, 115, .03, 53/365, .310, -1)
    )
    assert target.scenario_value == pytest.approx(expected_target, rel=0.0, abs=AGG_ATOL)
    for row in result.heatmap_rows:
        for point in row:
            assert point.status is ResearchValuationStatus.OK
            assert point.baseline_value == pytest.approx(base, rel=0.0, abs=AGG_ATOL)
            assert fsum(c.value_change for c in point.contributions) == pytest.approx(
                point.value_change, rel=0.0, abs=AGG_ATOL,
            )
    assert result.price_curve[1] == origin


def test_invalid_grid_cells_have_no_numerical_risk_or_contributions() -> None:
    request = _research_grid()
    invalid = replace(
        request,
        heatmap_price_shocks=(-1.5, 0., .01),
        heatmap_iv_shocks=(-.5, 0., .015),
    )
    result = ResearchRiskGridEngine.evaluate(invalid)
    assert result.status is ResearchValuationStatus.OK
    cell = result.heatmap_rows[0][0]
    assert cell.status is ResearchValuationStatus.UNAVAILABLE
    assert cell.value_change is None
    assert cell.scenario_value is None
    assert cell.contributions == ()
    assert result.heatmap_rows[1][1].status is ResearchValuationStatus.OK
