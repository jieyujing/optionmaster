"""T06: research risk-response curve and price x IV surface acceptance.

No live account, database, historical feed, Qt or CTP is used.
Coordinates are ADDITIONAL decimal price % and absolute IV volatility points,
relative to the already configured T04/T05 research scenario.
"""
from dataclasses import replace
from datetime import date, datetime, timedelta, timezone
from math import erf, exp, fsum, log, sqrt

import pytest

from vnpy_optionmaster.research_combination import ResearchAssetClass, ResearchCombinationLeg
from vnpy_optionmaster.research_grid import (
    ResearchGridRequest,
    ResearchRiskGridEngine,
)
from vnpy_optionmaster.research_risk import ResearchValuationRequest, ResearchValuationStatus
from vnpy_optionmaster.research_scenario import ResearchScenarioLeg, ResearchScenarioRequest
from vnpy_optionmaster.research_time import ResearchTimeEngine, ResearchTimeRequest

CN = timezone(timedelta(hours=8))
BASE = datetime(2025, 1, 1, 15, tzinfo=CN)
SCENARIO_AT = BASE + timedelta(days=10)


def _leg(contract: str, underlying: str, expiry: str, *,
         spot: float, strike: float, days: int, iv: float | None,
         amount: int = 1, reference_price: float | None = None) -> ResearchScenarioLeg:
    return ResearchScenarioLeg(
        selected_leg=ResearchCombinationLeg(
            valuation=ResearchValuationRequest(
                contract_id=contract, underlying_contract_id=underlying,
                underlying_price=spot, strike=strike, risk_free_rate=.03,
                time_to_expiry=days / 365, implied_volatility=iv,
                reference_option_price=reference_price, option_type=1,
                signed_quantity=amount, contract_multiplier=10,
            ),
            exchange="DCE", product_family_id="Y", asset_class=ResearchAssetClass.FUTURES_OPTION,
        ),
        expiry_id=expiry,
    )


def _time_request(*, overrides: bool = True,
                  scenario_at: datetime = SCENARIO_AT,
                  reference_price_near: bool = False) -> ResearchTimeRequest:
    sigma = .25
    observed = _black76(100, 100, sigma, 30/365) if reference_price_near else None
    near = _leg("Y2505-C-100", "Y2505", "2025-01-31",
                spot=100, strike=100, days=30,
                iv=None if reference_price_near else sigma, reference_price=observed)
    far = _leg("Y2509-C-110", "Y2509", "2025-03-02",
               spot=110, strike=110, days=60, iv=.30, amount=-2)
    configuration = ResearchScenarioRequest(
        legs=(near, far),
        parallel_price_shock=.03 if overrides else 0,
        price_shocks_by_underlying=(("Y2505", .04),) if overrides else (),
        parallel_iv_shock=-.02 if overrides else 0,
        iv_shocks_by_expiry=(("2025-01-31", -.05),) if overrides else (),
        additional_price_shock=.01 if overrides else 0,
        additional_iv_shock=.005 if overrides else 0,
    )
    return ResearchTimeRequest(
        scenario=configuration,
        reference_at=BASE,
        scenario_at=scenario_at,
        expiry_boundaries=(("2025-01-31", date(2025, 1, 31)),
                           ("2025-03-02", date(2025, 3, 2))),
    )


def _request(*, time_request: ResearchTimeRequest | None = None,
             curve: object = (-.02, 0.0, .02),
             price: object = (-.02, 0.0, .02),
             iv: object = (-.01, 0.0, .01)) -> ResearchGridRequest:
    return ResearchGridRequest(
        time_request=time_request if time_request is not None else _time_request(),
        curve_price_shocks=curve,
        heatmap_price_shocks=price,
        heatmap_iv_shocks=iv,
    )


def _black76(futures: float, strike: float, sigma: float, years: float) -> float:
    d1 = (log(futures/strike)+.5*sigma*sigma*years)/(sigma*sqrt(years))
    d2 = d1 - sigma*sqrt(years)

    def n(x: float) -> float:
        return .5 * (1 + erf(x/sqrt(2)))

    return exp(-.03 * years) * (futures*n(d1) - strike*n(d2))


def test_grid_shape_axes_origin_and_current_scenario_equality() -> None:
    request = _request()
    expected = ResearchTimeEngine.evaluate(request.time_request)
    result = ResearchRiskGridEngine.evaluate(request)
    assert result.status is ResearchValuationStatus.OK
    assert result.reason_code is None
    assert result.price_curve_axis == (-.02, 0., .02)
    assert result.heatmap_price_axis == (-.02, 0., .02)
    assert result.heatmap_iv_axis == (-.01, 0., .01)
    assert len(result.price_curve) == 3
    assert len(result.heatmap_rows) == 3
    assert all(len(row) == 3 for row in result.heatmap_rows)
    assert expected.status is ResearchValuationStatus.OK
    assert result.baseline_value == expected.baseline_value
    assert result.configured_scenario_value == expected.scenario_value
    assert result.configured_value_change == expected.value_change
    assert result.configured_contributions == expected.contributions
    origin = result.heatmap_rows[1][1]
    assert origin.price_shock == 0.0 and origin.iv_shock == 0.0
    assert origin.status is ResearchValuationStatus.OK
    assert origin.baseline_value == expected.baseline_value
    assert origin.scenario_value == expected.scenario_value
    assert origin.value_change == expected.value_change
    assert origin.contributions == expected.contributions
    assert result.price_curve[1] == origin


def test_configured_origin_may_have_nonzero_risk_while_historical_zero_does_not() -> None:
    configured = ResearchRiskGridEngine.evaluate(_request())
    historical = ResearchRiskGridEngine.evaluate(_request(
        time_request=_time_request(overrides=False, scenario_at=BASE),
    ))
    assert configured.status is ResearchValuationStatus.OK
    assert historical.status is ResearchValuationStatus.OK
    assert configured.baseline_value == pytest.approx(historical.baseline_value, abs=1e-10)
    assert configured.heatmap_rows[1][1].value_change != pytest.approx(0., abs=1e-8)
    # No market/IV shock AND no elapsed time is required for zero ΔV.
    assert historical.heatmap_rows[1][1].value_change == pytest.approx(0., abs=1e-12)


def test_additive_heatmap_axes_reprice_multi_months_and_tenors_independently() -> None:
    result = ResearchRiskGridEngine.evaluate(_request())
    target = result.heatmap_rows[2][2]  # price +.02; IV +.01
    assert result.status is ResearchValuationStatus.OK
    assert target.status is ResearchValuationStatus.OK
    # Near price +.04 (override) +.01 (configured extra) +.02 (grid) = +.07
    # Far price +.03 (default) +.01 +.02 = +.06
    # Near IV .25-.05+.005+.01=.215, far .30-.02+.005+.01=.295
    expected_scenario = (
        10 * _black76(107, 100, .215, 20/365)
        - 20 * _black76(116.6, 110, .295, 50/365)
    )
    assert target.scenario_value == pytest.approx(expected_scenario, abs=1e-8)
    expected_baseline = 10 * _black76(100, 100, .25, 30/365) - 20 * _black76(110, 110, .30, 60/365)
    assert target.baseline_value == pytest.approx(expected_baseline, abs=1e-8)
    assert target.value_change == pytest.approx(expected_scenario - expected_baseline, abs=1e-8)
    assert [c.signed_quantity for c in target.contributions] == [1, -2]
    assert [c.scenario_iv for c in target.contributions] == pytest.approx([.215, .295])
    assert [c.scenario_time_to_expiry for c in target.contributions] == pytest.approx([20/365, 50/365])


def test_every_usable_grid_cell_has_same_base_and_reconciling_leg_contributions() -> None:
    result = ResearchRiskGridEngine.evaluate(_request())
    assert result.status is ResearchValuationStatus.OK
    all_cells = (*result.price_curve, *(cell for row in result.heatmap_rows for cell in row))
    assert len(all_cells) == 12
    assert len({cell.baseline_value for cell in all_cells}) == 1
    for cell in all_cells:
        assert cell.status is ResearchValuationStatus.OK
        assert len(cell.contributions) == 2
        assert fsum(c.value_change for c in cell.contributions) == pytest.approx(cell.value_change, abs=1e-8)
        assert fsum(c.baseline_value for c in cell.contributions) == pytest.approx(cell.baseline_value, abs=1e-8)
        assert fsum(c.scenario_value for c in cell.contributions) == pytest.approx(cell.scenario_value, abs=1e-8)


def test_price_response_curve_keeps_iv_and_observation_time_fixed() -> None:
    request = _request(curve=(-.02, 0.0, .02), price=(0.0,), iv=(0.0,))
    result = ResearchRiskGridEngine.evaluate(request)
    assert result.status is ResearchValuationStatus.OK
    for coordinate, point in zip(request.curve_price_shocks, result.price_curve, strict=True):
        direct = ResearchTimeEngine.evaluate(replace(
            request.time_request,
            scenario=replace(
                request.time_request.scenario,
                additional_price_shock=request.time_request.scenario.additional_price_shock + coordinate,
            ),
        ))
        assert point.status is direct.status
        assert point.scenario_value == direct.scenario_value
        assert point.value_change == direct.value_change
        assert {c.scenario_time_to_expiry for c in point.contributions} == {20/365, 50/365}


def test_grid_cells_equal_direct_time_engine_for_each_coordinate() -> None:
    request = _request()
    result = ResearchRiskGridEngine.evaluate(request)
    assert result.status is ResearchValuationStatus.OK
    for row in result.heatmap_rows:
        for point in row:
            direct = ResearchTimeEngine.evaluate(replace(
                request.time_request,
                scenario=replace(
                    request.time_request.scenario,
                    additional_price_shock=request.time_request.scenario.additional_price_shock + point.price_shock,
                    additional_iv_shock=request.time_request.scenario.additional_iv_shock + point.iv_shock,
                ),
            ))
            assert point.status is direct.status
            assert point.baseline_value == direct.baseline_value
            assert point.scenario_value == direct.scenario_value
            assert point.value_change == direct.value_change


def test_invalid_cells_remain_unavailable_without_fictitious_zero_risk() -> None:
    result = ResearchRiskGridEngine.evaluate(_request(
        curve=(-1.5, 0.0, .02),
        price=(-1.5, 0.0, .02),
        iv=(-.50, 0.0, .01),
    ))
    assert result.status is ResearchValuationStatus.OK
    assert result.heatmap_rows[0][0].status is ResearchValuationStatus.UNAVAILABLE
    assert result.heatmap_rows[0][0].reason_code == "INVALID_SCENARIO"
    for cell in (result.heatmap_rows[0][0], result.heatmap_rows[1][0], result.heatmap_rows[0][1], result.price_curve[0]):
        assert cell.status is ResearchValuationStatus.UNAVAILABLE
        assert cell.value_change is None
        assert cell.scenario_value is None
        assert cell.baseline_value is None
        assert cell.contributions == ()
    assert result.heatmap_rows[1][1].status is ResearchValuationStatus.OK
    assert result.heatmap_rows[1][1].value_change is not None


def test_historical_reference_iv_inversion_stays_fixed_across_all_cells() -> None:
    result = ResearchRiskGridEngine.evaluate(_request(time_request=_time_request(reference_price_near=True)))
    assert result.status is ResearchValuationStatus.OK
    values = (
        *result.configured_contributions,
        *(c for cell in result.price_curve for c in cell.contributions),
        *(c for row in result.heatmap_rows for cell in row for c in cell.contributions),
    )
    near_ivs = [c.baseline_iv for c in values if c.contract_id == "Y2505-C-100"]
    assert near_ivs and all(v == pytest.approx(.25, abs=1e-6) for v in near_ivs)
    assert all(c.baseline_value == pytest.approx(result.baseline_value) for c in (result.heatmap_rows[1][1],))


@pytest.mark.parametrize("invalid_axes", [
    {"curve": ()},
    {"curve": (0.01, 0.02)},
    {"price": (0.01, 0.02)},
    {"iv": (0.01, 0.02)},
    {"price": (0.0, 0.0)},
    {"iv": (-0.0, 0.0)},
    {"curve": (0.0, True)},
    {"iv": (0.0, None)},
    {"curve": (0.0, float("nan"))},
    {"price": (0.0, float("inf"))},
    {"iv": (0.0, float("-inf"))},
    {"iv": (0.0, 10**1000)},
    {"curve": [0.0, 0.02]},
    {"iv": {"zero": 0.0}},
])
def test_malformed_or_missing_origin_axes_rejected_before_calculation(invalid_axes: dict[str, object]) -> None:
    result = ResearchRiskGridEngine.evaluate(_request(**invalid_axes))
    assert result.status is ResearchValuationStatus.UNAVAILABLE
    assert result.reason_code == "INVALID_AXES"
    assert result.baseline_value is None
    assert result.price_curve == ()
    assert result.heatmap_rows == ()


def test_grid_size_bound_and_large_curve_are_rejected() -> None:
    for bad in (
        _request(curve=tuple([0.] + [i/100000 for i in range(1, 122)])),
        _request(price=tuple([0.] + [i/100000 for i in range(1, 32)])),
    ):
        result = ResearchRiskGridEngine.evaluate(bad)
        assert result.status is ResearchValuationStatus.UNAVAILABLE
        assert result.reason_code == "GRID_LIMIT_EXCEEDED"
        assert result.heatmap_rows == ()


def test_invalid_configured_scenario_fails_whole_request() -> None:
    original = _time_request()
    bad = replace(original, scenario=replace(original.scenario, parallel_iv_shock=-1.))
    result = ResearchRiskGridEngine.evaluate(_request(time_request=bad))
    assert result.status is ResearchValuationStatus.UNAVAILABLE
    assert result.reason_code == "INVALID_SCENARIO"
    assert result.baseline_value is None
    assert result.configured_contributions == ()
    assert result.heatmap_rows == ()


def test_expired_shared_scenario_time_fails_whole_grid() -> None:
    bad = _time_request(scenario_at=datetime(2025, 1, 31, 1, tzinfo=CN))
    result = ResearchRiskGridEngine.evaluate(_request(time_request=bad))
    assert result.status is ResearchValuationStatus.UNAVAILABLE
    assert result.reason_code == "EXPIRED_SCENARIO"
    assert result.heatmap_rows == ()


def test_grid_contract_is_immutable_and_point_order_is_row_major_iv_price() -> None:
    result = ResearchRiskGridEngine.evaluate(_request(curve=(0.,), price=(.02, 0., -.02), iv=(.01, 0., -.01)))
    assert result.status is ResearchValuationStatus.OK
    assert [row[0].iv_shock for row in result.heatmap_rows] == [.01, 0, -.01]
    assert [[c.price_shock for c in row] for row in result.heatmap_rows] == [[.02, 0., -.02]] * 3
    with pytest.raises((AttributeError, TypeError)):
        result.baseline_value = 123.0


def test_invalid_top_level_request_is_unavailable_not_exception() -> None:
    result = ResearchRiskGridEngine.evaluate(object())
    assert result.status is ResearchValuationStatus.UNAVAILABLE
    assert result.reason_code == "INVALID_INPUT"
