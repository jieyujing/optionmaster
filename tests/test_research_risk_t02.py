"""T02 research adapter: multiple pricing models, IV and Greeks.

Golden cases exercise the public one-leg seam without trading or historical DB.
"""
from dataclasses import replace
from math import erf, exp, isfinite, log, sqrt

import pytest

from vnpy_optionmaster.pricing import binomial_tree
from vnpy_optionmaster.pricing.iv_solver import IVStatus
from vnpy_optionmaster.research_risk import (
    ResearchPricingModel,
    ResearchRiskEngine,
    ResearchValuationRequest,
    ResearchValuationStatus,
)


def _call_price(s: float, k: float, r: float, t: float, vol: float, model: ResearchPricingModel) -> float:
    d1 = (log(s / k) + (0.5 * vol * vol + (r if model is ResearchPricingModel.BLACK_SCHOLES else 0)) * t) / (vol * sqrt(t))
    d2 = d1 - vol * sqrt(t)
    def cdf(z: float) -> float:
        return (1 + erf(z / sqrt(2))) / 2
    if model is ResearchPricingModel.BLACK_SCHOLES:
        return s * cdf(d1) - k * exp(-r * t) * cdf(d2)
    return exp(-r * t) * (s * cdf(d1) - k * cdf(d2))


def _case(**updates: object) -> ResearchValuationRequest:
    source = ResearchValuationRequest(
        contract_id="TEST-C-100",
        underlying_contract_id="TEST",
        underlying_price=100.0,
        strike=100.0,
        risk_free_rate=0.03,
        time_to_expiry=0.5,
        implied_volatility=0.234567,
        option_type=1,
        signed_quantity=2,
        contract_multiplier=10.0,
        underlying_shock=0.05,
    )
    return replace(source, **updates)


@pytest.mark.parametrize("model", [ResearchPricingModel.BLACK_76, ResearchPricingModel.BLACK_SCHOLES])
def test_european_analytical_prices_and_greeks(model: ResearchPricingModel) -> None:
    case = _case(model_name=model)
    result = ResearchRiskEngine().evaluate(case)
    assert result.status is ResearchValuationStatus.OK
    scale = case.signed_quantity * case.contract_multiplier
    assert result.baseline_value == pytest.approx(
        scale * _call_price(100.0, 100.0, 0.03, 0.5, 0.234567, model), abs=1e-8
    )
    assert result.scenario_value == pytest.approx(
        scale * _call_price(105.0, 100.0, 0.03, 0.5, 0.234567, model), abs=1e-8
    )
    assert result.implied_volatility == pytest.approx(0.234567)
    assert result.baseline_greeks is not None
    assert result.scenario_greeks is not None
    assert all(isfinite(x) for x in (
        result.baseline_greeks.delta, result.baseline_greeks.gamma,
        result.baseline_greeks.theta, result.baseline_greeks.vega,
    ))
    # Delta, Gamma, Vega: independent finite differences of analytical price.
    h = 0.01
    vol = 0.234567
    def p(s: float, v: float, t: float) -> float:
        return _call_price(s, 100.0, 0.03, t, v, model)
    delta = (p(100 + h, vol, 0.5) - p(100 - h, vol, 0.5)) / (2 * h)
    gamma = (p(100 + h, vol, 0.5) - 2 * p(100, vol, 0.5) + p(100 - h, vol, 0.5)) / (h * h)
    vega = (p(100, vol + 1e-4, 0.5) - p(100, vol - 1e-4, 0.5)) / 2e-4
    theta = -(p(100, vol, 0.50001) - p(100, vol, 0.49999)) / 2e-5
    greeks = result.baseline_greeks
    assert greeks.delta == pytest.approx(delta * scale, rel=1e-4, abs=1e-4)
    assert greeks.gamma == pytest.approx(gamma * scale, rel=1e-3, abs=1e-3)
    assert greeks.vega == pytest.approx(vega * scale, rel=1e-4, abs=1e-4)
    assert greeks.theta == pytest.approx(theta * scale, rel=1e-4, abs=1e-4)


def test_model_labels_match_existing_ui_contract() -> None:
    assert ResearchPricingModel.BLACK_76.value == "Black-76 欧式期货期权"
    assert ResearchPricingModel.BLACK_SCHOLES.value == "Black-Scholes 欧式股票期权"
    assert ResearchPricingModel.BINOMIAL_TREE.value == "二叉树 美式期货期权"


def test_american_binomial_price_greeks_and_early_exercise() -> None:
    case = _case(
        model_name=ResearchPricingModel.BINOMIAL_TREE,
        underlying_price=80.0, strike=100.0, option_type=-1, tree_steps=51, underlying_shock=0.1,
    )
    result = ResearchRiskEngine().evaluate(case)
    assert result.status is ResearchValuationStatus.OK
    assert result.baseline_value == pytest.approx(
        20 * binomial_tree.calculate_price(80.0, 100.0, 0.03, 0.5, 0.234567, -1, 51)
    )
    assert result.baseline_value >= 20 * 20
    assert result.baseline_greeks is not None
    assert all(isfinite(x) for x in (
        result.baseline_greeks.delta, result.baseline_greeks.gamma,
        result.baseline_greeks.theta, result.baseline_greeks.vega,
    ))


@pytest.mark.parametrize("model", [
    ResearchPricingModel.BLACK_76,
    ResearchPricingModel.BLACK_SCHOLES,
    ResearchPricingModel.BINOMIAL_TREE,
])
def test_reference_price_inverts_iv_without_rounded_float_fallback(model: ResearchPricingModel) -> None:
    sigma = 0.312345
    case = _case(model_name=model, implied_volatility=None, tree_steps=51)
    if model is ResearchPricingModel.BINOMIAL_TREE:
        price = binomial_tree.calculate_price(100, 100, 0.03, 0.5, sigma, 1, 51)
    else:
        price = _call_price(100, 100, 0.03, 0.5, sigma, model)
    result = ResearchRiskEngine().evaluate(replace(case, reference_option_price=price))
    assert result.status is ResearchValuationStatus.OK
    assert result.iv_result is not None and result.iv_result.status is IVStatus.SUCCESS
    assert result.iv_result.valid
    assert result.implied_volatility == pytest.approx(sigma, abs=1e-5)
    assert result.baseline_value == pytest.approx(price * 20, abs=1e-6)


@pytest.mark.parametrize("model", [
    ResearchPricingModel.BLACK_76,
    ResearchPricingModel.BLACK_SCHOLES,
    ResearchPricingModel.BINOMIAL_TREE,
])
def test_impossible_market_price_fails_closed(model: ResearchPricingModel) -> None:
    case = _case(model_name=model, implied_volatility=None, reference_option_price=1000.0)
    result = ResearchRiskEngine().evaluate(case)
    assert result.status is ResearchValuationStatus.UNAVAILABLE
    assert result.reason_code == "IV_NO_SOLUTION"
    assert result.iv_result is not None and result.iv_result.status is IVStatus.NO_SOLUTION
    assert result.baseline_value is None
    assert result.baseline_greeks is None


@pytest.mark.parametrize("update", [
    {"implied_volatility": None, "reference_option_price": None},
    {"implied_volatility": 0.2, "reference_option_price": 10.0},
    {"implied_volatility": None, "reference_option_price": -1.0},
    {"model_name": "unrecognized-model"},
    {"tree_steps": 1, "model_name": ResearchPricingModel.BINOMIAL_TREE},
    {"tree_steps": True, "model_name": ResearchPricingModel.BINOMIAL_TREE},
])
def test_bad_model_or_vol_source_is_explicitly_rejected(update: dict[str, object]) -> None:
    result = ResearchRiskEngine().evaluate(_case(**update))
    assert result.status is ResearchValuationStatus.UNAVAILABLE
    assert result.reason_code == (
        "UNSUPPORTED_MODEL" if update.get("model_name") == "unrecognized-model" else "INVALID_INPUT"
    )
    assert result.value_change is None


def test_short_greeks_sign_and_quantity_scale() -> None:
    engine = ResearchRiskEngine()
    long = engine.evaluate(_case(model_name=ResearchPricingModel.BLACK_SCHOLES, signed_quantity=1))
    short = engine.evaluate(_case(model_name=ResearchPricingModel.BLACK_SCHOLES, signed_quantity=-3))
    assert long.status is ResearchValuationStatus.OK
    assert short.status is ResearchValuationStatus.OK
    assert long.baseline_greeks is not None and short.baseline_greeks is not None
    for name in ("delta", "gamma", "theta", "vega"):
        assert getattr(short.baseline_greeks, name) == pytest.approx(-3 * getattr(long.baseline_greeks, name))

def _independent_two_step_american_put(
    futures: float, strike: float, rate: float, maturity: float, sigma: float,
) -> tuple[float, float]:
    """Golden 2-step early-exercise reference, independent of pricing module."""
    dt = maturity / 2
    up = exp(sigma * sqrt(dt))
    down = 1 / up
    probability = (1 - down) / (up - down)
    discount = exp(-rate * dt)
    terminal = (
        max(strike - futures * up * up, 0),
        max(strike - futures, 0),
        max(strike - futures * down * down, 0),
    )
    up_value = max(
        strike - futures * up,
        discount * (probability * terminal[0] + (1 - probability) * terminal[1]),
    )
    down_value = max(
        strike - futures * down,
        discount * (probability * terminal[1] + (1 - probability) * terminal[2]),
    )
    price = max(
        strike - futures,
        discount * (probability * up_value + (1 - probability) * down_value),
    )
    delta = (up_value - down_value) / (futures * (up - down))
    return price, delta


def test_american_two_step_independent_price_and_delta_golden_case() -> None:
    case = _case(
        model_name=ResearchPricingModel.BINOMIAL_TREE,
        underlying_price=95.0, strike=100.0, risk_free_rate=0.04,
        time_to_expiry=0.7, implied_volatility=0.25, option_type=-1,
        signed_quantity=-2, contract_multiplier=5.0, tree_steps=2,
        underlying_shock=0.07,
    )
    result = ResearchRiskEngine().evaluate(case)
    price, delta = _independent_two_step_american_put(95.0, 100.0, 0.04, 0.7, 0.25)
    assert result.status is ResearchValuationStatus.OK
    assert result.baseline_value == pytest.approx(-10 * price, abs=1e-9)
    assert result.baseline_greeks is not None
    assert result.baseline_greeks.delta == pytest.approx(-10 * delta, abs=1e-9)
