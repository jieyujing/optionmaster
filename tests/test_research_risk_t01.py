"""T01: public, offline one-leg research valuation behavior."""
from dataclasses import replace
from math import isfinite

import pytest

from vnpy_optionmaster.research_risk import (
    ResearchRiskEngine,
    ResearchValuationRequest,
    ResearchValuationStatus,
)


@pytest.fixture
def case() -> ResearchValuationRequest:
    return ResearchValuationRequest(
        contract_id="Y2505-C-8000",
        underlying_contract_id="Y2505",
        underlying_price=100.0,
        strike=100.0,
        risk_free_rate=0.05,
        time_to_expiry=1.0,
        implied_volatility=0.2,
        option_type=1,
        signed_quantity=1,
        contract_multiplier=10.0,
        underlying_shock=0.1,
    )


def test_one_leg_black76_matches_independent_reference(case: ResearchValuationRequest) -> None:
    from vnpy_optionmaster.pricing.black_76 import calculate_price

    result = ResearchRiskEngine().evaluate(case)
    assert result.status is ResearchValuationStatus.OK
    assert result.baseline_value == pytest.approx(10 * calculate_price(100, 100, 0.05, 1.0, 0.2, 1))
    assert result.scenario_value == pytest.approx(10 * calculate_price(110, 100, 0.05, 1.0, 0.2, 1))
    assert result.value_change == pytest.approx(result.scenario_value - result.baseline_value)


def test_long_short_and_multiplier_symmetry(case: ResearchValuationRequest) -> None:
    engine = ResearchRiskEngine()
    long = engine.evaluate(case)
    short = engine.evaluate(replace(case, signed_quantity=-2))
    assert long.status is ResearchValuationStatus.OK
    assert short.status is ResearchValuationStatus.OK
    assert short.baseline_value == pytest.approx(-2 * long.baseline_value)
    assert short.scenario_value == pytest.approx(-2 * long.scenario_value)
    assert short.value_change == pytest.approx(-2 * long.value_change)


def test_zero_shock_is_exactly_zero(case: ResearchValuationRequest) -> None:
    result = ResearchRiskEngine().evaluate(replace(case, underlying_shock=0.0))
    assert result.status is ResearchValuationStatus.OK
    assert result.value_change == pytest.approx(0.0, abs=1e-12)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("underlying_price", 0.0),
        ("underlying_price", float("nan")),
        ("underlying_price", float("inf")),
        ("strike", -1.0),
        ("risk_free_rate", float("nan")),
        ("time_to_expiry", 0.0),
        ("implied_volatility", 0.0),
        ("contract_multiplier", 0.0),
        ("contract_multiplier", float("nan")),
        ("signed_quantity", 0),
        ("signed_quantity", 1.5),
        ("option_type", 0),
        ("underlying_shock", -1.0),
        ("underlying_shock", float("inf")),
        ("contract_id", ""),
        ("underlying_contract_id", ""),
    ],
)
def test_bad_inputs_fail_closed(case: ResearchValuationRequest, field: str, value: object) -> None:
    result = ResearchRiskEngine().evaluate(replace(case, **{field: value}))
    assert result.status is ResearchValuationStatus.UNAVAILABLE
    assert result.reason_code == "INVALID_INPUT"
    assert result.baseline_value is None
    assert result.scenario_value is None
    assert result.value_change is None


def test_no_market_or_trading_objects_required(case: ResearchValuationRequest) -> None:
    result = ResearchRiskEngine().evaluate(case)
    assert result.status is ResearchValuationStatus.OK
    assert result.value_change is not None and isfinite(result.value_change)
