"""T03 end-to-end multi-leg research combination acceptance tests.

No Qt, CTP, history database, market feed or actual position objects are used.
The product family and exchange are explicit authoritative inputs, never inferred
from option symbols or the prefix of the underlying delivery month.
"""
from dataclasses import replace
from math import erf, exp, fsum, isfinite, log, sqrt

import pytest

from vnpy_optionmaster.research_combination import (
    ResearchAssetClass,
    ResearchCombinationEngine,
    ResearchCombinationLeg,
    ResearchCombinationRequest,
)
from vnpy_optionmaster.research_risk import (
    ResearchPricingModel,
    ResearchRiskEngine,
    ResearchValuationRequest,
    ResearchValuationStatus,
)


def _leg(
    contract: str = "Y2505-C-8000",
    underlying: str = "Y2505",
    *,
    exchange: str = "DCE",
    family: str = "Y",
    asset: ResearchAssetClass | str = ResearchAssetClass.FUTURES_OPTION,
    quantity: int = 1,
    multiplier: float = 10.0,
    spot: float = 100.0,
    strike: float = 100.0,
    shock: float = 0.05,
    sigma: float | None = 0.2,
    reference_price: float | None = None,
    model: ResearchPricingModel | str = ResearchPricingModel.BLACK_76,
    expiry: float = 0.5,
) -> ResearchCombinationLeg:
    valuation = ResearchValuationRequest(
        contract_id=contract,
        underlying_contract_id=underlying,
        underlying_price=spot,
        strike=strike,
        risk_free_rate=0.03,
        time_to_expiry=expiry,
        implied_volatility=sigma,
        reference_option_price=reference_price,
        model_name=model,
        option_type=1,
        signed_quantity=quantity,
        contract_multiplier=multiplier,
        underlying_shock=shock,
    )
    return ResearchCombinationLeg(
        valuation=valuation,
        exchange=exchange,
        product_family_id=family,
        asset_class=asset,
    )


def _combo(*legs: ResearchCombinationLeg) -> ResearchCombinationRequest:
    return ResearchCombinationRequest(legs=tuple(legs))


def _call(f: float, k: float, vol: float, t: float, r: float = 0.03) -> float:
    """Independent Black-76 formula for combo price-reference checking."""
    d1 = (log(f / k) + 0.5 * vol * vol * t) / (vol * sqrt(t))
    d2 = d1 - vol * sqrt(t)

    def cdf(x: float) -> float:
        return (1 + erf(x / sqrt(2))) / 2

    return exp(-r * t) * (f * cdf(d1) - k * cdf(d2))


def test_vertical_spread_aggregates_signed_theoretical_values_and_contributions() -> None:
    long_call = _leg()
    short_call = _leg("Y2505-C-11000", quantity=-1, strike=110.0)
    result = ResearchCombinationEngine().evaluate(_combo(long_call, short_call))

    assert result.status is ResearchValuationStatus.OK
    assert result.reason_code is None
    expected_base = 10 * (_call(100, 100, 0.2, 0.5) - _call(100, 110, 0.2, 0.5))
    expected_scenario = 10 * (_call(105, 100, 0.2, 0.5) - _call(105, 110, 0.2, 0.5))
    assert result.baseline_value == pytest.approx(expected_base, abs=1e-9)
    assert result.scenario_value == pytest.approx(expected_scenario, abs=1e-9)
    assert result.value_change == pytest.approx(expected_scenario - expected_base, abs=1e-9)
    assert len(result.contributions) == 2
    assert [item.contract_id for item in result.contributions] == [
        "Y2505-C-8000", "Y2505-C-11000",
    ]
    assert [item.signed_quantity for item in result.contributions] == [1, -1]
    assert result.contributions[0].baseline_value > 0
    assert result.contributions[1].baseline_value < 0
    assert fsum(item.baseline_value for item in result.contributions) == pytest.approx(result.baseline_value)
    assert fsum(item.scenario_value for item in result.contributions) == pytest.approx(result.scenario_value)
    assert fsum(item.value_change for item in result.contributions) == pytest.approx(result.value_change)
    assert all(item.valuation_result.baseline_greeks is not None for item in result.contributions)


def test_ratio_spread_uses_independent_signed_quantities_and_multipliers() -> None:
    first = _leg(quantity=2, multiplier=10)
    second = _leg("Y2505-C-11000", quantity=-3, multiplier=5, strike=110)
    result = ResearchCombinationEngine().evaluate(_combo(first, second))
    assert result.status is ResearchValuationStatus.OK
    assert result.baseline_value == pytest.approx(20 * _call(100, 100, 0.2, 0.5) - 15 * _call(100, 110, 0.2, 0.5))
    assert result.contributions[1].contract_multiplier == 5.0


def test_same_family_different_futures_months_have_distinct_underlying_prices() -> None:
    near = _leg("Y2505-C-8000", "Y2505", spot=8000, strike=8000, shock=0.04, expiry=0.25)
    far = _leg(
        "Y2509-C-8200", "Y2509", spot=8200, strike=8200, shock=-0.02,
        expiry=0.7, quantity=-2,
    )
    result = ResearchCombinationEngine().evaluate(_combo(near, far))
    assert result.status is ResearchValuationStatus.OK
    assert result.underlying_contract_ids == ("Y2505", "Y2509")
    first_single = ResearchRiskEngine.evaluate(near.valuation)
    second_single = ResearchRiskEngine.evaluate(far.valuation)
    assert result.value_change == pytest.approx(first_single.value_change + second_single.value_change)
    assert result.contributions[0].underlying_contract_id == "Y2505"
    assert result.contributions[1].underlying_contract_id == "Y2509"


def test_unchanged_zero_shock_gives_zero_combination_value_change() -> None:
    legs = (
        _leg(shock=0.0),
        _leg("Y2505-C-11000", quantity=-2, strike=110, shock=0.0),
    )
    result = ResearchCombinationEngine().evaluate(_combo(*legs))
    assert result.status is ResearchValuationStatus.OK
    assert result.value_change == pytest.approx(0, abs=1e-12)


@pytest.mark.parametrize(
    ("bad_leg", "reason"),
    [
        (_leg("P2509-C-8200", "P2509", family="P"), "UNSUPPORTED_COMBINATION"),
        (_leg("Y2509-C-8200", "Y2509", exchange="CZCE"), "UNSUPPORTED_COMBINATION"),
        (_leg("Y2509-C-8200", "Y2509", asset=ResearchAssetClass.INDEX_OPTION, model=ResearchPricingModel.BLACK_SCHOLES), "UNSUPPORTED_COMBINATION"),
        (_leg("Y2509-C-8200", "Y2509", family="Y", exchange="DCE", model=ResearchPricingModel.BLACK_SCHOLES), "UNSUPPORTED_COMBINATION"),
        (_leg("Y2509-C-8200", "Y2509", family="Y ", exchange="DCE"), "INVALID_INPUT"),
        (_leg("Y2509-C-8200", "Y2509", exchange=""), "INVALID_INPUT"),
        (_leg("Y2509-C-8200", "Y2509", asset="unrecognized"), "INVALID_INPUT"),
    ],
)
def test_incompatible_or_uncanonical_product_identity_fails_closed(
    bad_leg: ResearchCombinationLeg, reason: str,
) -> None:
    result = ResearchCombinationEngine().evaluate(_combo(_leg(), bad_leg))
    assert result.status is ResearchValuationStatus.UNAVAILABLE
    assert result.reason_code == reason
    assert result.baseline_value is None
    assert result.scenario_value is None
    assert result.value_change is None
    assert result.contributions == ()


def test_canonical_family_does_not_use_contract_prefix_to_guess_product() -> None:
    # Identical-looking option prefixes are NOT authority for product family.
    invalid = _leg("Y2509-C-8100", "Y2509", family="P")
    result = ResearchCombinationEngine().evaluate(_combo(_leg(), invalid))
    assert result.reason_code == "UNSUPPORTED_COMBINATION"


@pytest.mark.parametrize("asset", [ResearchAssetClass.INDEX_OPTION, ResearchAssetClass.ETF_OPTION])
def test_same_spot_underlying_index_etf_option_combination_allowed(asset: ResearchAssetClass) -> None:
    first = _leg(
        "IO-C-100", "000300.SH", exchange="CFFEX", family="CSI300", asset=asset,
        model=ResearchPricingModel.BLACK_SCHOLES,
    )
    second = _leg(
        "IO-C-110", "000300.SH", exchange="CFFEX", family="CSI300", asset=asset,
        model=ResearchPricingModel.BLACK_SCHOLES, strike=110, quantity=-1,
    )
    result = ResearchCombinationEngine().evaluate(_combo(first, second))
    assert result.status is ResearchValuationStatus.OK
    assert result.underlying_contract_ids == ("000300.SH",)


def test_different_etf_underlyings_rejected_even_if_same_family() -> None:
    first = _leg("ETF-C-100", "510300.SH", exchange="SSE", family="CSI300", asset=ResearchAssetClass.ETF_OPTION, model=ResearchPricingModel.BLACK_SCHOLES)
    second = _leg("ETF-C-105", "159919.SZ", exchange="SSE", family="CSI300", asset=ResearchAssetClass.ETF_OPTION, model=ResearchPricingModel.BLACK_SCHOLES)
    result = ResearchCombinationEngine().evaluate(_combo(first, second))
    assert result.reason_code == "UNSUPPORTED_COMBINATION"


def test_index_and_etf_cannot_mix_even_with_same_underlying() -> None:
    one = _leg("A-C-100", "000300.SH", exchange="CFFEX", family="CSI300", asset=ResearchAssetClass.INDEX_OPTION, model=ResearchPricingModel.BLACK_SCHOLES)
    two = _leg("B-C-105", "000300.SH", exchange="CFFEX", family="CSI300", asset=ResearchAssetClass.ETF_OPTION, model=ResearchPricingModel.BLACK_SCHOLES)
    assert ResearchCombinationEngine().evaluate(_combo(one, two)).reason_code == "UNSUPPORTED_COMBINATION"


def test_repeated_option_id_rejected_even_with_different_position_or_strike() -> None:
    one = _leg()
    two = _leg(quantity=-1, strike=110)
    result = ResearchCombinationEngine().evaluate(_combo(one, two))
    assert result.status is ResearchValuationStatus.UNAVAILABLE
    assert result.reason_code == "DUPLICATE_LEG"


@pytest.mark.parametrize("field,value", [
    ("underlying_price", 103.0),
    ("underlying_shock", -0.01),
])
def test_shared_underlying_input_must_be_consistent(field: str, value: float) -> None:
    one = _leg()
    two = _leg("Y2505-C-10500", strike=105)
    two = replace(two, valuation=replace(two.valuation, **{field: value}))
    result = ResearchCombinationEngine().evaluate(_combo(one, two))
    assert result.reason_code == "UNDERLYING_INPUT_CONFLICT"
    assert result.contributions == ()
    assert result.baseline_value is None


def test_unavailable_leg_is_never_hidden_by_valid_other_legs() -> None:
    one = _leg()
    invalid = _leg("Y2505-C-11000", strike=110, sigma=None, reference_price=1000.0)
    result = ResearchCombinationEngine().evaluate(_combo(one, invalid))
    assert result.status is ResearchValuationStatus.UNAVAILABLE
    assert result.reason_code == "IV_NO_SOLUTION"
    assert result.failed_leg_index == 1
    assert result.failed_contract_id == "Y2505-C-11000"
    assert result.baseline_value is None
    assert result.scenario_value is None
    assert result.value_change is None
    assert result.contributions == ()


@pytest.mark.parametrize("legs", [
    (),
    (_leg(),),
    (_leg(), object()),
    [_leg(), _leg("Y2505-C-11000", strike=110)],
])
def test_invalid_combination_shape_is_unavailable(legs: object) -> None:
    result = ResearchCombinationEngine().evaluate(ResearchCombinationRequest(legs=legs))
    assert result.status is ResearchValuationStatus.UNAVAILABLE
    assert result.reason_code == "INVALID_INPUT"


def test_non_combination_input_is_unavailable_not_exception() -> None:
    result = ResearchCombinationEngine().evaluate(object())
    assert result.status is ResearchValuationStatus.UNAVAILABLE
    assert result.reason_code == "INVALID_INPUT"


def test_invalid_quantity_propagates_with_exact_leg_index() -> None:
    invalid = _leg("Y2505-C-11000", strike=110, quantity=0)
    result = ResearchCombinationEngine().evaluate(_combo(_leg(), invalid))
    assert result.status is ResearchValuationStatus.UNAVAILABLE
    assert result.reason_code == "INVALID_INPUT"
    assert result.failed_leg_index == 1
    assert result.failed_contract_id == "Y2505-C-11000"


def test_aggregate_overflow_fails_closed_without_partial_value() -> None:
    huge1 = _leg(multiplier=1e308, quantity=1)
    huge2 = _leg("Y2505-C-11000", strike=110, multiplier=1e308, quantity=1)
    result = ResearchCombinationEngine().evaluate(_combo(huge1, huge2))
    assert result.status is ResearchValuationStatus.UNAVAILABLE
    assert result.baseline_value is None
    assert result.contributions == ()


def test_output_is_immutable_and_no_combined_delta_is_fabricated() -> None:
    result = ResearchCombinationEngine().evaluate(_combo(_leg(), _leg("Y2505-C-11000", strike=110, quantity=-1)))
    assert result.status is ResearchValuationStatus.OK
    assert isinstance(result.contributions, tuple)
    assert all(isfinite(c.value_change) for c in result.contributions)
    with pytest.raises((AttributeError, TypeError)):
        result.baseline_value = 1.0
