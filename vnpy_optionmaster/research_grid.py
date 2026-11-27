"""T06: offline risk-response curves and price x absolute-IV scenario surfaces.

All cells are complete model repricings through T05. No historical market feed,
Qt, CTP, real positions, live Greeks aggregation or actual trading P&L.

Axes are additional shocks, not replacement shocks:
  price (%)       S_j = S0_j * (1 + configured_r_j + extra_x)
  IV (vol points)  sigma_i = sigma0_i + configured_d_expiry + extra_y

For every point the baseline is the SAME historical (unshocked) theoretical
combination net value, even when the heatmap (0,0) reflects nonzero configured
shocks and time decay. Heatmap rows correspond to IV-axis order and columns to
price-axis order. Invalid independent points have no fabricated zero values.
"""
from dataclasses import dataclass, replace
from math import isfinite

from vnpy_optionmaster.research_pricing import research_pricing_scope
from vnpy_optionmaster.research_risk import ResearchValuationStatus
from vnpy_optionmaster.research_time import (
    ResearchTimeContribution,
    ResearchTimeEngine,
    ResearchTimeRequest,
    ResearchTimeResult,
)

_MAX_CURVE_POINTS = 121
_MAX_HEATMAP_PRICE_POINTS = 31
_MAX_HEATMAP_IV_POINTS = 31
_MAX_HEATMAP_CELLS = 1024


@dataclass(frozen=True)
class ResearchGridRequest:
    """Prevalidated T05 scenario + immutable explicit shock-axis coordinates."""

    time_request: ResearchTimeRequest
    curve_price_shocks: tuple[float, ...]
    heatmap_price_shocks: tuple[float, ...]
    heatmap_iv_shocks: tuple[float, ...]


@dataclass(frozen=True)
class ResearchGridPoint:
    """One price/IV coordinate; invalid points carry no monetary risk values."""

    price_shock: float
    iv_shock: float
    status: ResearchValuationStatus
    reason_code: str | None = None
    baseline_value: float | None = None
    scenario_value: float | None = None
    value_change: float | None = None
    contributions: tuple[ResearchTimeContribution, ...] = ()


@dataclass(frozen=True)
class ResearchRiskGridResult:
    status: ResearchValuationStatus
    reason_code: str | None = None
    baseline_value: float | None = None
    configured_scenario_value: float | None = None
    configured_value_change: float | None = None
    configured_contributions: tuple[ResearchTimeContribution, ...] = ()
    price_curve_axis: tuple[float, ...] = ()
    heatmap_price_axis: tuple[float, ...] = ()
    heatmap_iv_axis: tuple[float, ...] = ()
    price_curve: tuple[ResearchGridPoint, ...] = ()
    heatmap_rows: tuple[tuple[ResearchGridPoint, ...], ...] = ()


def _unavailable(reason_code: str) -> ResearchRiskGridResult:
    """Whole response is invalid; never expose partial rows or fake a zero."""
    return ResearchRiskGridResult(
        status=ResearchValuationStatus.UNAVAILABLE,
        reason_code=reason_code,
    )


def _numeric_axis(raw: object, max_points: int) -> tuple[tuple[float, ...] | None, str | None]:
    """Check shape and finite/nonduplicate values before dispatching any model."""
    if not isinstance(raw, tuple) or not raw:
        return None, "INVALID_AXES"
    if len(raw) > max_points:
        return None, "GRID_LIMIT_EXCEEDED"
    values: list[float] = []
    seen: set[float] = set()
    for original in raw:
        if isinstance(original, bool) or not isinstance(original, (int, float)):
            return None, "INVALID_AXES"
        try:
            value = float(original)
        except (ValueError, OverflowError):
            return None, "INVALID_AXES"
        if not isfinite(value) or value in seen:
            return None, "INVALID_AXES"
        values.append(value)
        seen.add(value)
    if 0.0 not in seen:
        return None, "INVALID_AXES"
    return tuple(values), None


def _point(
    result: ResearchTimeResult,
    price: float,
    iv: float,
    historical_baseline: float,
) -> ResearchGridPoint:
    if result.status is not ResearchValuationStatus.OK:
        return ResearchGridPoint(
            price_shock=price,
            iv_shock=iv,
            status=ResearchValuationStatus.UNAVAILABLE,
            reason_code=result.reason_code or "NUMERICAL_FAILURE",
        )
    if (
        result.baseline_value is None
        or result.scenario_value is None
        or result.value_change is None
        or result.baseline_value != historical_baseline
    ):
        # Baseline must not differ between cells (in particular under IV moves).
        return ResearchGridPoint(
            price_shock=price,
            iv_shock=iv,
            status=ResearchValuationStatus.UNAVAILABLE,
            reason_code="BASELINE_DRIFT",
        )
    return ResearchGridPoint(
        price_shock=price,
        iv_shock=iv,
        status=ResearchValuationStatus.OK,
        baseline_value=historical_baseline,
        scenario_value=result.scenario_value,
        value_change=result.value_change,
        contributions=result.contributions,
    )


class ResearchRiskGridEngine:
    """Request-scoped consistent baseline + fully repriced one/two-dimensional risk."""

    @staticmethod
    def evaluate(request: ResearchGridRequest) -> ResearchRiskGridResult:
        with research_pricing_scope():
            return ResearchRiskGridEngine._evaluate(request)

    @staticmethod
    def _evaluate(request: ResearchGridRequest) -> ResearchRiskGridResult:
        if (
            not isinstance(request, ResearchGridRequest)
            or not isinstance(request.time_request, ResearchTimeRequest)
        ):
            return _unavailable("INVALID_INPUT")

        curve_axis, curve_error = _numeric_axis(request.curve_price_shocks, _MAX_CURVE_POINTS)
        if curve_error is not None:
            return _unavailable(curve_error)
        price_axis, price_error = _numeric_axis(
            request.heatmap_price_shocks, _MAX_HEATMAP_PRICE_POINTS,
        )
        if price_error is not None:
            return _unavailable(price_error)
        iv_axis, iv_error = _numeric_axis(
            request.heatmap_iv_shocks, _MAX_HEATMAP_IV_POINTS,
        )
        if iv_error is not None:
            return _unavailable(iv_error)
        if curve_axis is None or price_axis is None or iv_axis is None:
            return _unavailable("INVALID_AXES")
        if len(price_axis) * len(iv_axis) > _MAX_HEATMAP_CELLS:
            return _unavailable("GRID_LIMIT_EXCEEDED")

        configured = ResearchTimeEngine.evaluate(request.time_request)
        if configured.status is not ResearchValuationStatus.OK:
            return _unavailable(configured.reason_code or "NUMERICAL_FAILURE")
        if (
            configured.baseline_value is None
            or configured.scenario_value is None
            or configured.value_change is None
        ):
            return _unavailable("NUMERICAL_FAILURE")

        historical_baseline = configured.baseline_value
        scenario = request.time_request.scenario

        def calculate(price_shock: float, iv_shock: float) -> ResearchGridPoint:
            if price_shock == 0.0 and iv_shock == 0.0:
                # The origin is the configured scenario, not zero-risk P&L.
                result = configured
            else:
                changed_scenario = replace(
                    scenario,
                    additional_price_shock=scenario.additional_price_shock + price_shock,
                    additional_iv_shock=scenario.additional_iv_shock + iv_shock,
                )
                result = ResearchTimeEngine.evaluate(
                    replace(request.time_request, scenario=changed_scenario),
                )
            return _point(result, price_shock, iv_shock, historical_baseline)

        curve = tuple(calculate(x, 0.0) for x in curve_axis)
        # Reuse the curve for the y=0 row. It is still a full T05 result.
        curve_lookup = dict(zip(curve_axis, curve, strict=True))
        rows = tuple(
            tuple(
                curve_lookup[x] if y == 0.0 and x in curve_lookup else calculate(x, y)
                for x in price_axis
            )
            for y in iv_axis
        )
        return ResearchRiskGridResult(
            status=ResearchValuationStatus.OK,
            baseline_value=historical_baseline,
            configured_scenario_value=configured.scenario_value,
            configured_value_change=configured.value_change,
            configured_contributions=configured.contributions,
            price_curve_axis=curve_axis,
            heatmap_price_axis=price_axis,
            heatmap_iv_axis=iv_axis,
            price_curve=curve,
            heatmap_rows=rows,
        )
