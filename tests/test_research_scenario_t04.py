"""T04: independent price and IV shock semantics on multi-leg research combinations.

All fixtures are offline and deliberately carry canonical underlying and exact
expiration identifiers. Test the public scenario interface, not Qt or trading.
"""
from dataclasses import replace
from math import erf, exp, fsum, log, sqrt

import pytest

from vnpy_optionmaster.research_combination import (
    ResearchAssetClass,
    ResearchCombinationLeg,
)
from vnpy_optionmaster.research_pricing import ResearchPricingModel
from vnpy_optionmaster.research_risk import (
    ResearchRiskEngine,
    ResearchValuationRequest,
    ResearchValuationStatus,
)
from vnpy_optionmaster.research_scenario import (
    ResearchScenarioEngine,
    ResearchScenarioLeg,
    ResearchScenarioRequest,
)


def _leg(
    contract: str,
    underlying: str,
    expiry: str,
    *,
    spot: float = 100.0,
    strike: float = 100.0,
    vol: float | None = 0.25,
    reference_option_price: float | None = None,
    direction: int = 1,
    multiplier: float = 10.0,
    maturity: float = 0.5,
    preconfigured_shock: float = 0.0,
    exchange: str = "DCE",
    family: str = "Y",
    asset: ResearchAssetClass = ResearchAssetClass.FUTURES_OPTION,
    model: ResearchPricingModel = ResearchPricingModel.BLACK_76,
) -> ResearchScenarioLeg:
    return ResearchScenarioLeg(
        selected_leg=ResearchCombinationLeg(
            valuation=ResearchValuationRequest(
                contract_id=contract,
                underlying_contract_id=underlying,
                underlying_price=spot,
                strike=strike,
                risk_free_rate=0.03,
                time_to_expiry=maturity,
                implied_volatility=vol,
                reference_option_price=reference_option_price,
                option_type=1,
                signed_quantity=direction,
                contract_multiplier=multiplier,
                underlying_shock=preconfigured_shock,
                model_name=model,
            ),
            exchange=exchange,
            product_family_id=family,
            asset_class=asset,
        ),
        expiry_id=expiry,
    )


def _near_far() -> tuple[ResearchScenarioLeg, ResearchScenarioLeg]:
    return (
        _leg("Y2505-C-100", "Y2505", "2025-04-02", spot=100, maturity=0.3),
        _leg("Y2509-C-110", "Y2509", "2025-08-04", spot=110, strike=110, vol=0.30, maturity=0.7, direction=-2),
    )


def _request(
    legs: tuple[ResearchScenarioLeg, ...] | None = None,
    **overrides: object,
) -> ResearchScenarioRequest:
    return ResearchScenarioRequest(legs=legs if legs is not None else _near_far(), **overrides)


def _black76_call(spot: float, strike: float, vol: float, maturity: float) -> float:
    d1 = (log(spot / strike) + 0.5 * vol * vol * maturity) / (vol * sqrt(maturity))
    d2 = d1 - vol * sqrt(maturity)

    def normal_cdf(x: float) -> float:
        return 0.5 * (1 + erf(x / sqrt(2)))

    return exp(-0.03 * maturity) * (
        spot * normal_cdf(d1) - strike * normal_cdf(d2)
    )


def test_zero_shock_preserves_historical_baseline_and_zero_delta() -> None:
    result = ResearchScenarioEngine.evaluate(_request())
    assert result.status is ResearchValuationStatus.OK
    assert result.value_change == pytest.approx(0.0, abs=1e-12)
    assert result.baseline_value == pytest.approx(result.scenario_value, abs=1e-12)
    assert result.underlying_contract_ids == ("Y2505", "Y2509")
    assert len(result.contributions) == 2
    assert all(item.value_change == pytest.approx(0.0, abs=1e-12) for item in result.contributions)


def test_parallel_price_and_absolute_iv_points_completely_reprice_every_leg() -> None:
    result = ResearchScenarioEngine.evaluate(
        _request(parallel_price_shock=0.10, parallel_iv_shock=-0.05),
    )
    assert result.status is ResearchValuationStatus.OK
    expected_base = 10 * _black76_call(100, 100, 0.25, 0.3) - 20 * _black76_call(110, 110, 0.3, 0.7)
    expected_scenario = 10 * _black76_call(110, 100, 0.20, 0.3) - 20 * _black76_call(121, 110, 0.25, 0.7)
    assert result.baseline_value == pytest.approx(expected_base, abs=1e-8)
    assert result.scenario_value == pytest.approx(expected_scenario, abs=1e-8)
    assert result.value_change == pytest.approx(expected_scenario - expected_base, abs=1e-8)
    assert [c.scenario_iv for c in result.contributions] == pytest.approx([0.2, 0.25])
    assert [c.effective_price_shock for c in result.contributions] == pytest.approx([0.1, 0.1])
    assert [c.effective_iv_shock for c in result.contributions] == pytest.approx([-0.05, -0.05])


def test_independent_underlying_month_price_shocks_override_parallel_default() -> None:
    result = ResearchScenarioEngine.evaluate(
        _request(
            parallel_price_shock=0.03,
            price_shocks_by_underlying=(("Y2505", 0.04), ("Y2509", -0.02)),
        ),
    )
    assert result.status is ResearchValuationStatus.OK
    assert [c.effective_price_shock for c in result.contributions] == pytest.approx([0.04, -0.02])
    expected = 10 * _black76_call(104, 100, 0.25, 0.3) - 20 * _black76_call(107.8, 110, 0.30, 0.7)
    assert result.scenario_value == pytest.approx(expected, abs=1e-8)


def test_iv_expiry_month_overrides_parallel_default_without_altering_baseline() -> None:
    result = ResearchScenarioEngine.evaluate(
        _request(
            parallel_iv_shock=-0.03,
            iv_shocks_by_expiry=(("2025-04-02", 0.04),),
        ),
    )
    assert result.status is ResearchValuationStatus.OK
    assert [c.effective_iv_shock for c in result.contributions] == pytest.approx([0.04, -0.03])
    assert [c.scenario_iv for c in result.contributions] == pytest.approx([0.29, 0.27])
    assert [c.baseline_iv for c in result.contributions] == pytest.approx([0.25, 0.3])


def test_heatmap_extra_shocks_add_to_effective_month_config_not_compound() -> None:
    result = ResearchScenarioEngine.evaluate(
        _request(
            parallel_price_shock=0.03,
            price_shocks_by_underlying=(("Y2505", 0.04),),
            parallel_iv_shock=-0.02,
            iv_shocks_by_expiry=(("2025-04-02", -0.05),),
            additional_price_shock=0.02,
            additional_iv_shock=0.01,
        ),
    )
    assert result.status is ResearchValuationStatus.OK
    assert [c.effective_price_shock for c in result.contributions] == pytest.approx([0.06, 0.05])
    assert [c.effective_iv_shock for c in result.contributions] == pytest.approx([-0.04, -0.01])
    assert [c.scenario_iv for c in result.contributions] == pytest.approx([0.21, 0.29])
    assert result.scenario_value == pytest.approx(
        10 * _black76_call(106, 100, 0.21, 0.3) -
        20 * _black76_call(115.5, 110, 0.29, 0.7),
        abs=1e-8,
    )


def test_same_expiry_id_shares_iv_override_across_different_strikes() -> None:
    near = _leg("Y2505-C-100", "Y2505", "2025-04-02", vol=0.2)
    another = _leg("Y2505-C-110", "Y2505", "2025-04-02", strike=110, vol=0.27, direction=-1)
    result = ResearchScenarioEngine.evaluate(
        _request((near, another), iv_shocks_by_expiry=(("2025-04-02", 0.03),)),
    )
    assert result.status is ResearchValuationStatus.OK
    assert [c.scenario_iv for c in result.contributions] == pytest.approx([0.23, 0.30])


def test_one_leg_original_option_reference_price_is_inverted_once_for_baseline() -> None:
    sigma = 0.312345
    market_price = _black76_call(100, 100, sigma, 0.5)
    with_price = _leg(
        "Y2505-C-100", "Y2505", "2025-04-02",
        vol=None, reference_option_price=market_price,
    )
    other = _leg("Y2505-C-110", "Y2505", "2025-04-02", strike=110)
    original_iv = ResearchRiskEngine.evaluate(with_price.selected_leg.valuation)
    assert original_iv.status is ResearchValuationStatus.OK
    result = ResearchScenarioEngine.evaluate(
        _request(
            (with_price, other),
            parallel_price_shock=0.1,
            parallel_iv_shock=-0.03,
        ),
    )
    assert result.status is ResearchValuationStatus.OK
    assert result.contributions[0].baseline_iv == pytest.approx(sigma, abs=1e-6)
    assert result.contributions[0].scenario_iv == pytest.approx(sigma - 0.03, abs=1e-6)
    assert result.contributions[0].baseline_value == pytest.approx(original_iv.baseline_value)
    assert result.contributions[0].scenario_value == pytest.approx(
        10 * _black76_call(110, 100, sigma - 0.03, 0.5),
        abs=1e-6,
    )


def test_baseline_does_not_move_when_scenario_changes() -> None:
    untouched = ResearchScenarioEngine.evaluate(_request())
    shocked = ResearchScenarioEngine.evaluate(
        _request(parallel_price_shock=0.2, parallel_iv_shock=0.09),
    )
    assert untouched.status is ResearchValuationStatus.OK
    assert shocked.status is ResearchValuationStatus.OK
    assert shocked.baseline_value == pytest.approx(untouched.baseline_value, abs=1e-12)
    assert shocked.scenario_value != pytest.approx(untouched.scenario_value)
    assert fsum(c.value_change for c in shocked.contributions) == pytest.approx(
        shocked.value_change, abs=1e-8,
    )


@pytest.mark.parametrize("invalid_overrides", [
    {"parallel_price_shock": float("nan")},
    {"parallel_price_shock": 10**1000},
    {"iv_shocks_by_expiry": (("2025-04-02", 10**1000),)},
    {"parallel_price_shock": float("inf")},
    {"parallel_iv_shock": float("-inf")},
    {"additional_price_shock": float("nan")},
    {"additional_iv_shock": float("nan")},
    {"price_shocks_by_underlying": (("Y2505", float("nan")),)},
    {"iv_shocks_by_expiry": (("2025-04-02", float("inf")),)},
    {"price_shocks_by_underlying": (("Y2505", 0.1), ("Y2505", 0.2))},
    {"iv_shocks_by_expiry": (("2025-04-02", 0.1), ("2025-04-02", 0.2))},
    {"iv_shocks_by_expiry": ((" 2025-04-02 ", 0.02),)},
    {"price_shocks_by_underlying": {"Y2505": 0.03}},
])
def test_invalid_shock_inputs_fail_closed(invalid_overrides: dict[str, object]) -> None:
    result = ResearchScenarioEngine.evaluate(_request(**invalid_overrides))
    assert result.status is ResearchValuationStatus.UNAVAILABLE
    assert result.reason_code == "INVALID_INPUT"
    assert result.baseline_value is None
    assert result.contributions == ()


@pytest.mark.parametrize("invalid_overrides", [
    {"parallel_price_shock": -1.0},
    {"price_shocks_by_underlying": (("Y2505", -1.1),)},
    {"parallel_iv_shock": -0.25},
    {"iv_shocks_by_expiry": (("2025-08-04", -0.30),)},
    {"parallel_price_shock": 0.15, "additional_price_shock": -1.2},
    {"parallel_iv_shock": 0.1, "additional_iv_shock": -0.4},
])
def test_invalid_effective_price_or_iv_fails_without_fabricated_zero(
    invalid_overrides: dict[str, object],
) -> None:
    result = ResearchScenarioEngine.evaluate(_request(**invalid_overrides))
    assert result.status is ResearchValuationStatus.UNAVAILABLE
    assert result.reason_code == "INVALID_SCENARIO"
    assert result.value_change is None
    assert result.contributions == ()


@pytest.mark.parametrize("unknown", [
    {"price_shocks_by_underlying": (("Y9999", 0.03),)},
    {"iv_shocks_by_expiry": (("2025-12-31", 0.01),)},
])
def test_unknown_shock_target_rejected_instead_of_ignored(unknown: dict[str, object]) -> None:
    result = ResearchScenarioEngine.evaluate(_request(**unknown))
    assert result.status is ResearchValuationStatus.UNAVAILABLE
    assert result.reason_code == "UNKNOWN_SHOCK_TARGET"
    assert result.baseline_value is None


@pytest.mark.parametrize("bad_id", ["", " 2025-04-02", "2025 04 02"])
def test_missing_or_noncanonical_expiry_metadata_rejected(bad_id: str) -> None:
    first, second = _near_far()
    result = ResearchScenarioEngine.evaluate(
        _request((replace(first, expiry_id=bad_id), second)),
    )
    assert result.status is ResearchValuationStatus.UNAVAILABLE
    assert result.reason_code == "INVALID_INPUT"


def test_identical_expiry_id_cannot_have_conflicting_time_to_expiry() -> None:
    a = _leg("Y2505-C-100", "Y2505", "2025-04-02", maturity=0.4)
    b = _leg("Y2505-C-110", "Y2505", "2025-04-02", maturity=0.5)
    result = ResearchScenarioEngine.evaluate(_request((a, b)))
    assert result.status is ResearchValuationStatus.UNAVAILABLE
    assert result.reason_code == "EXPIRY_METADATA_CONFLICT"


def test_existing_one_leg_underlying_shock_must_not_become_hidden_scenario_input() -> None:
    first, second = _near_far()
    altered = replace(
        first,
        selected_leg=replace(
            first.selected_leg,
            valuation=replace(first.selected_leg.valuation, underlying_shock=0.05),
        ),
    )
    result = ResearchScenarioEngine.evaluate(_request((altered, second)))
    assert result.status is ResearchValuationStatus.UNAVAILABLE
    assert result.reason_code == "BASELINE_SHOCK_CONFLICT"


def test_existing_invalid_combination_rejection_is_preserved() -> None:
    near, far = _near_far()
    bad = replace(far, selected_leg=replace(far.selected_leg, product_family_id="P"))
    result = ResearchScenarioEngine.evaluate(_request((near, bad)))
    assert result.status is ResearchValuationStatus.UNAVAILABLE
    assert result.reason_code == "UNSUPPORTED_COMBINATION"


def test_one_leg_no_iv_solution_propagates_atomically() -> None:
    first, far = _near_far()
    invalid = replace(
        far,
        selected_leg=replace(
            far.selected_leg,
            valuation=replace(
                far.selected_leg.valuation, implied_volatility=None, reference_option_price=1000.0,
            ),
        ),
    )
    result = ResearchScenarioEngine.evaluate(_request((first, invalid)))
    assert result.status is ResearchValuationStatus.UNAVAILABLE
    assert result.reason_code == "IV_NO_SOLUTION"
    assert result.failed_leg_index == 1
    assert result.baseline_value is None
    assert result.scenario_value is None
    assert result.contributions == ()


def test_scenario_result_keeps_separate_baseline_and_scenario_greeks_per_leg() -> None:
    result = ResearchScenarioEngine.evaluate(
        _request(parallel_price_shock=0.2, parallel_iv_shock=0.1),
    )
    assert result.status is ResearchValuationStatus.OK
    assert result.contributions[0].baseline_result.baseline_greeks is not None
    assert result.contributions[0].scenario_result.baseline_greeks is not None
    assert result.contributions[0].baseline_result.baseline_greeks.delta != pytest.approx(
        result.contributions[0].scenario_result.baseline_greeks.delta,
    )


def test_malformed_request_does_not_raise() -> None:
    invalid = ResearchScenarioEngine.evaluate(object())
    assert invalid.status is ResearchValuationStatus.UNAVAILABLE
    assert invalid.reason_code == "INVALID_INPUT"
    assert ResearchScenarioEngine.evaluate(_request(legs=())).status is ResearchValuationStatus.UNAVAILABLE
