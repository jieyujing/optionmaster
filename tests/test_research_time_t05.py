"""T05: explicit shared scenario timestamp, multi-expiry and strict expiry boundaries.

Deterministic time fixtures use an explicit +08:00 exchange-local zone;
ACT/365F elapsed-time adjustment is calculated in UTC seconds.
"""
from dataclasses import replace
from datetime import date, datetime, timedelta, timezone
from math import fsum

import pytest

from vnpy_optionmaster.pricing.black_76 import calculate_price
from vnpy_optionmaster.research_combination import ResearchAssetClass, ResearchCombinationLeg
from vnpy_optionmaster.research_risk import ResearchValuationRequest, ResearchValuationStatus
from vnpy_optionmaster.research_scenario import (
    ResearchScenarioEngine,
    ResearchScenarioLeg,
    ResearchScenarioRequest,
)
from vnpy_optionmaster.research_time import (
    ResearchTimeEngine,
    ResearchTimeRequest,
)

CN = timezone(timedelta(hours=8))
OTHER = timezone.utc
BASE = datetime(2025, 1, 1, 15, tzinfo=CN)
NEAR_EXACT = datetime(2025, 1, 31, 15, tzinfo=CN)
FAR_EXACT = datetime(2025, 3, 2, 15, tzinfo=CN)
DAY = timedelta(days=1)
NEAR_ID = "near-expiry"
FAR_ID = "far-expiry"


def _leg(
    contract: str,
    underlying: str,
    expiry: str,
    *,
    maturity_days: float,
    strike: float = 100,
    spot: float = 100,
    sigma: float | None = 0.25,
    reference_price: float | None = None,
    quantity: int = 1,
) -> ResearchScenarioLeg:
    return ResearchScenarioLeg(
        selected_leg=ResearchCombinationLeg(
            valuation=ResearchValuationRequest(
                contract_id=contract,
                underlying_contract_id=underlying,
                underlying_price=spot,
                strike=strike,
                risk_free_rate=0.03,
                time_to_expiry=maturity_days / 365.0,
                implied_volatility=sigma,
                reference_option_price=reference_price,
                option_type=1,
                signed_quantity=quantity,
                contract_multiplier=10,
                underlying_shock=0.0,
            ),
            exchange="DCE", product_family_id="Y",
            asset_class=ResearchAssetClass.FUTURES_OPTION,
        ),
        expiry_id=expiry,
    )


def _legs() -> tuple[ResearchScenarioLeg, ResearchScenarioLeg]:
    return (
        _leg("Y2505-C-100", "Y2505", NEAR_ID, maturity_days=30),
        _leg("Y2509-C-110", "Y2509", FAR_ID, maturity_days=60, strike=110, spot=110, sigma=0.30, quantity=-2),
    )


def _req(
    *,
    scenario_at: datetime | object = BASE + 10 * DAY,
    reference_at: datetime | object = BASE,
    boundaries: object = ((NEAR_ID, date(2025, 1, 31)), (FAR_ID, date(2025, 3, 2))),
    scenario: ResearchScenarioRequest | object | None = None,
    day_count_basis: object = "ACT/365F",
) -> ResearchTimeRequest:
    if scenario is None:
        scenario = ResearchScenarioRequest(legs=_legs())
    return ResearchTimeRequest(
        scenario=scenario,
        reference_at=reference_at,
        scenario_at=scenario_at,
        expiry_boundaries=boundaries,
        day_count_basis=day_count_basis,
    )


def test_time_passage_reprices_each_leg_on_one_shared_instant() -> None:
    result = ResearchTimeEngine.evaluate(_req())
    assert result.status is ResearchValuationStatus.OK
    assert result.reason_code is None
    assert result.reference_at == BASE
    assert result.scenario_at == BASE + 10 * DAY
    assert result.underlying_contract_ids == ("Y2505", "Y2509")
    assert len(result.contributions) == 2
    assert [x.baseline_time_to_expiry for x in result.contributions] == pytest.approx([30 / 365, 60 / 365])
    assert [x.scenario_time_to_expiry for x in result.contributions] == pytest.approx([20 / 365, 50 / 365])
    baseline = 10 * calculate_price(100, 100, .03, 30/365, .25, 1) - 20 * calculate_price(110, 110, .03, 60/365, .30, 1)
    scenario = 10 * calculate_price(100, 100, .03, 20/365, .25, 1) - 20 * calculate_price(110, 110, .03, 50/365, .30, 1)
    assert result.baseline_value == pytest.approx(baseline)
    assert result.scenario_value == pytest.approx(scenario)
    assert result.value_change == pytest.approx(scenario - baseline)
    assert fsum(x.value_change for x in result.contributions) == pytest.approx(result.value_change)


def test_zero_elapsed_time_is_same_as_t04_price_iv_configuration() -> None:
    original = ResearchScenarioRequest(legs=_legs(), parallel_price_shock=.1, parallel_iv_shock=-.02)
    expected = ResearchScenarioEngine.evaluate(original)
    result = ResearchTimeEngine.evaluate(_req(scenario_at=BASE, scenario=original))
    assert result.status is ResearchValuationStatus.OK
    assert expected.status is ResearchValuationStatus.OK
    assert result.baseline_value == pytest.approx(expected.baseline_value)
    assert result.scenario_value == pytest.approx(expected.scenario_value)
    assert result.value_change == pytest.approx(expected.value_change)


def test_joint_time_price_iv_and_month_overrides_reprice_all_legs() -> None:
    original = ResearchScenarioRequest(
        legs=_legs(),
        parallel_price_shock=0.03,
        price_shocks_by_underlying=(("Y2505", 0.04),),
        additional_price_shock=0.02,
        parallel_iv_shock=-0.02,
        iv_shocks_by_expiry=((NEAR_ID, -0.05),),
        additional_iv_shock=0.01,
    )
    result = ResearchTimeEngine.evaluate(_req(scenario=original))
    assert result.status is ResearchValuationStatus.OK
    assert [x.scenario_time_to_expiry for x in result.contributions] == pytest.approx([20/365, 50/365])
    expected = (
        10 * calculate_price(106, 100, .03, 20/365, .21, 1)
        - 20 * calculate_price(115.5, 110, .03, 50/365, .29, 1)
    )
    assert result.scenario_value == pytest.approx(expected, abs=1e-8)
    baseline = (
        10 * calculate_price(100, 100, .03, 30/365, .25, 1)
        - 20 * calculate_price(110, 110, .03, 60/365, .30, 1)
    )
    assert result.baseline_value == pytest.approx(baseline)


def test_reference_market_price_iv_not_reinverted_after_time_moves() -> None:
    sigma = .312345
    reference_price = calculate_price(100, 100, .03, 30/365, sigma, 1)
    near, far = _legs()
    near = replace(
        near,
        selected_leg=replace(
            near.selected_leg,
            valuation=replace(near.selected_leg.valuation, implied_volatility=None, reference_option_price=reference_price),
        ),
    )
    src = ResearchScenarioRequest(legs=(near, far), parallel_price_shock=.05, parallel_iv_shock=-.03)
    result = ResearchTimeEngine.evaluate(_req(scenario=src))
    assert result.status is ResearchValuationStatus.OK
    assert result.contributions[0].baseline_iv == pytest.approx(sigma, abs=1e-6)
    assert result.contributions[0].scenario_iv == pytest.approx(sigma-.03, abs=1e-6)
    assert result.contributions[0].baseline_value == pytest.approx(
        10 * calculate_price(100, 100, .03, 30/365, sigma, 1),
    )
    assert result.contributions[0].scenario_value == pytest.approx(
        10 * calculate_price(105, 100, .03, 20/365, sigma-.03, 1),
    )


@pytest.mark.parametrize("scenario_at", [
    datetime(2025, 1, 31, 0, 0, tzinfo=CN),
    datetime(2025, 1, 31, 9, 0, tzinfo=CN),
    datetime(2025, 1, 31, 14, 59, tzinfo=CN),
    datetime(2025, 1, 31, 23, 59, tzinfo=CN),
    FAR_EXACT,
])
def test_date_only_boundary_rejects_every_in_date_scenario(scenario_at: datetime) -> None:
    result = ResearchTimeEngine.evaluate(_req(scenario_at=scenario_at))
    assert result.status is ResearchValuationStatus.UNAVAILABLE
    assert result.reason_code == "EXPIRED_SCENARIO"
    assert result.baseline_value is None
    assert result.contributions == ()


def test_date_only_boundary_allows_strictly_previous_day() -> None:
    result = ResearchTimeEngine.evaluate(
        _req(scenario_at=datetime(2025, 1, 30, 23, 59, 59, tzinfo=CN)),
    )
    assert result.status is ResearchValuationStatus.OK
    assert result.contributions[0].scenario_time_to_expiry > 0


def test_exact_cutoff_allows_same_calendar_day_only_strictly_earlier() -> None:
    boundaries = ((NEAR_ID, NEAR_EXACT), (FAR_ID, FAR_EXACT))
    early = ResearchTimeEngine.evaluate(
        _req(scenario_at=NEAR_EXACT - timedelta(hours=1), boundaries=boundaries),
    )
    assert early.status is ResearchValuationStatus.OK
    assert early.contributions[0].scenario_time_to_expiry == pytest.approx(1 / 24 / 365)
    for when in (NEAR_EXACT, NEAR_EXACT + timedelta(microseconds=1), FAR_EXACT):
        result = ResearchTimeEngine.evaluate(_req(scenario_at=when, boundaries=boundaries))
        assert result.status is ResearchValuationStatus.UNAVAILABLE
        assert result.reason_code == "EXPIRED_SCENARIO"


def test_earliest_leg_rejects_even_if_later_expiration_still_valid() -> None:
    result = ResearchTimeEngine.evaluate(_req(scenario_at=datetime(2025, 2, 1, 8, tzinfo=CN)))
    assert result.status is ResearchValuationStatus.UNAVAILABLE
    assert result.reason_code == "EXPIRED_SCENARIO"


@pytest.mark.parametrize("boundary_input,reason", [
    (((NEAR_ID, date(2025,1,31)),), "MISSING_EXPIRY_BOUNDARY"),
    (((NEAR_ID, date(2025,1,31)), (FAR_ID, date(2025,3,2)), ("extra", date(2025,3,30))), "UNKNOWN_EXPIRY_BOUNDARY"),
    (((NEAR_ID, date(2025,1,31)), (NEAR_ID, date(2025,1,31)), (FAR_ID, date(2025,3,2))), "INVALID_INPUT"),
    (((NEAR_ID, "2025-01-31"), (FAR_ID, date(2025,3,2))), "INVALID_INPUT"),
    (((NEAR_ID, datetime(2025,1,31,15)), (FAR_ID, FAR_EXACT)), "INVALID_INPUT"),
    (((NEAR_ID, datetime(2025,1,31,15,tzinfo=OTHER)), (FAR_ID, FAR_EXACT)), "INVALID_INPUT"),
    (((" near-expiry", date(2025,1,31)), (FAR_ID, date(2025,3,2))), "INVALID_INPUT"),
    ({"near-expiry": date(2025,1,31), "far-expiry": date(2025,3,2)}, "INVALID_INPUT"),
])
def test_malformed_expiry_boundaries_fail_closed(boundary_input: object, reason: str) -> None:
    result = ResearchTimeEngine.evaluate(_req(boundaries=boundary_input))
    assert result.status is ResearchValuationStatus.UNAVAILABLE
    assert result.reason_code == reason
    assert result.contributions == ()


@pytest.mark.parametrize("reference,scenario", [
    (datetime(2025,1,1,15), BASE + DAY),
    (BASE, datetime(2025,1,11,15)),
    (BASE, datetime(2025,1,11,15,tzinfo=OTHER)),
    (BASE + DAY, BASE),
    (BASE, BASE - timedelta(microseconds=1)),
    ("2025-01-01", BASE + DAY),
])
def test_invalid_time_zone_or_backward_time_rejected(reference: object, scenario: object) -> None:
    result = ResearchTimeEngine.evaluate(_req(reference_at=reference, scenario_at=scenario))
    assert result.status is ResearchValuationStatus.UNAVAILABLE
    assert result.reason_code == "INVALID_SCENARIO_TIME"


def test_baseline_at_or_after_expiry_is_invalid_even_if_scenario_equal_reference() -> None:
    near, far = _legs()
    altered = (
        replace(
            near,
            selected_leg=replace(
                near.selected_leg,
                valuation=replace(near.selected_leg.valuation, time_to_expiry=.05),
            ),
        ),
        far,
    )
    original = ResearchScenarioRequest(legs=altered)
    result = ResearchTimeEngine.evaluate(_req(reference_at=NEAR_EXACT, scenario_at=NEAR_EXACT, scenario=original))
    assert result.status is ResearchValuationStatus.UNAVAILABLE
    assert result.reason_code == "EXPIRED_BASELINE"


def test_reference_maturity_must_match_expiry_metadata() -> None:
    near, far = _legs()
    altered = (
        replace(near, selected_leg=replace(near.selected_leg, valuation=replace(near.selected_leg.valuation, time_to_expiry=1.0))),
        far,
    )
    result = ResearchTimeEngine.evaluate(_req(scenario=ResearchScenarioRequest(legs=altered)))
    assert result.status is ResearchValuationStatus.UNAVAILABLE
    assert result.reason_code == "EXPIRY_METADATA_CONFLICT"


def test_maturity_does_not_become_zero_near_expiry_if_inputs_are_valid() -> None:
    result = ResearchTimeEngine.evaluate(_req(
        scenario_at=datetime(2025,1,30,23,59,59,tzinfo=CN),
        boundaries=((NEAR_ID, date(2025,1,31)), (FAR_ID, date(2025,3,2))),
    ))
    assert result.status is ResearchValuationStatus.OK
    assert result.contributions[0].scenario_time_to_expiry > 0


def test_unsupported_day_count_basis_fails_closed() -> None:
    result = ResearchTimeEngine.evaluate(_req(day_count_basis="ACT/360"))
    assert result.status is ResearchValuationStatus.UNAVAILABLE
    assert result.reason_code == "UNSUPPORTED_DAY_COUNT_BASIS"


def test_underlying_scenario_invalid_leg_propagates_without_partial_totals() -> None:
    original = ResearchScenarioRequest(legs=_legs(), parallel_iv_shock=-.30)
    result = ResearchTimeEngine.evaluate(_req(scenario=original))
    assert result.status is ResearchValuationStatus.UNAVAILABLE
    assert result.reason_code == "INVALID_SCENARIO"
    assert result.contributions == ()
    assert result.scenario_value is None


def test_bad_request_is_unavailable_not_exception() -> None:
    result = ResearchTimeEngine.evaluate(object())
    assert result.status is ResearchValuationStatus.UNAVAILABLE
    assert result.reason_code == "INVALID_INPUT"


def test_invalid_nested_selected_leg_fails_closed() -> None:
    near, far = _legs()
    bad = replace(near, selected_leg=object())
    result = ResearchTimeEngine.evaluate(_req(scenario=ResearchScenarioRequest(legs=(bad, far))))
    assert result.status is ResearchValuationStatus.UNAVAILABLE
    assert result.reason_code == "INVALID_INPUT"
    assert result.contributions == ()


def test_date_max_boundary_fails_closed_without_datetime_overflow() -> None:
    result = ResearchTimeEngine.evaluate(_req(
        boundaries=((NEAR_ID, date.max), (FAR_ID, date(2025,3,2))),
    ))
    assert result.status is ResearchValuationStatus.UNAVAILABLE
    assert result.reason_code == "INVALID_INPUT"
    assert result.contributions == ()
