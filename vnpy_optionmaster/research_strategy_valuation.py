"""D12 model-valued research curves; D10 memo and D11 kernel remain authoritative."""
from dataclasses import dataclass, replace
from datetime import date, datetime, timezone
from fractions import Fraction
from math import fsum, isfinite
from time import monotonic
from typing import Literal

from vnpy_optionmaster.research_pricing import (
    ResearchPricingModel, research_greeks, research_iv, research_pricing_scope,
)
from vnpy_optionmaster.research_strategy_api_dto import (
    CapabilityDTO, LegContributionDTO, OptionQuoteDTO, Reason, RiskMetricDTO, RootIntervalDTO, UnderlyingQuoteDTO,
    SelectedLegDTO, StrategyCurveDTO, StrategyGreekGroupDTO, StrategyPointDTO,
    StrategyRateEvidenceDTO, StrategyRiskDTO, StrategyValuationDTO, StrategyValuationRequestDTO,
)
from vnpy_optionmaster.research_strategy_payoff import (
    Interval, PriceMapping, ResolvedLeg, ResolvedStrategy, StrategyUnavailable,
    evaluate_payoff, expiry_boundary, price_mappings, unavailable_risk,
)

_TREE_STEPS = 128
_SECONDS_PER_YEAR = 365 * 86400
# Local computation safety only; real query/production budgets are separate gates.
_COMPUTATION_SECONDS = 25.0
_MODELS = {
    "BLACK_76": ResearchPricingModel.BLACK_76,
    "BLACK_SCHOLES": ResearchPricingModel.BLACK_SCHOLES,
    "AMERICAN_BINOMIAL": ResearchPricingModel.BINOMIAL_TREE,
}
CurveKind = Literal["CURRENT", "SCENARIO", "FIRST_EXPIRY_CONDITIONAL", "TERMINAL"]
Greeks = tuple[float, float, float, float]


@dataclass(frozen=True)
class Calibration:
    sigma: float | None
    rate: float | None
    expiry: datetime | None
    reason: Reason | None
    pricing_adjustment: float = 0.0


def _years(start: datetime, end: datetime) -> float:
    return (end.astimezone(timezone.utc) - start.astimezone(timezone.utc)).total_seconds() / _SECONDS_PER_YEAR


def _deadline(end: float) -> None:
    if monotonic() >= end:
        raise StrategyUnavailable("DEADLINE_EXCEEDED")


def _right(leg: ResolvedLeg) -> int:
    return 1 if leg.option is not None and leg.option.option_type == "CALL" else -1


def _finite(value: float) -> float:
    if not isfinite(value):
        raise StrategyUnavailable("NUMERICAL_FAILURE")
    return float(value)


def _rate_map(resolved: ResolvedStrategy, rates: tuple[StrategyRateEvidenceDTO, ...]) -> dict[str, StrategyRateEvidenceDTO]:
    selected = {leg.option.expiry_id for leg in resolved.legs if leg.option is not None}
    pins = {resolved.snapshot.source_pin, *resolved.snapshot.source_versions.values()}
    result: dict[str, StrategyRateEvidenceDTO] = {}
    duplicates: set[str] = set()
    for rate in rates:
        if rate.expiry_id not in selected:
            continue
        if rate.expiry_id in result:
            duplicates.add(rate.expiry_id)
        try:
            valid_date = date.fromisoformat(rate.quote_date).isoformat() == rate.quote_date
        except ValueError:
            valid_date = False
        if rate.source_pin not in pins or not valid_date or rate.quote_date > resolved.snapshot.trade_date:
            duplicates.add(rate.expiry_id)
        result[rate.expiry_id] = rate
    for key in duplicates:
        result.pop(key, None)
    return result


def quote_model_greeks(q: OptionQuoteDTO, u: UnderlyingQuoteDTO, price: float, rate: float,
                       t: float, sigma: float, right: int, *,
                       pricing_adjustment: float = 0.0) -> tuple[float, float, float, float, float]:
    # Same input convention as OptionData.calculate_theo_greeks. No
    # independent forward conversion, Greek transform or pricing kernel.
    return research_greeks(_MODELS[q.model], price + pricing_adjustment,
                           q.strike, rate, t, sigma, right, _TREE_STEPS)


def calibrate_quote(q: OptionQuoteDTO, underlying: UnderlyingQuoteDTO, reference: datetime,
                    rates: dict[str, StrategyRateEvidenceDTO], end: float, *,
                    pricing_adjustment: float | None = 0.0) -> Calibration:
    """Same admitted-mark calibration for chain display and strategy valuation."""
    try:
        _deadline(end)
        if q.expiry_at is None:
            raise StrategyUnavailable("HORIZON_UNAVAILABLE")
        expiry = datetime.fromisoformat(q.expiry_at)
        if expiry <= reference:
            raise StrategyUnavailable("EXPIRED_SCENARIO")
        local = q.terms_version.startswith("LOCAL_CACHE:")
        expected_model = ("AMERICAN_BINOMIAL" if q.exercise_style == "AMERICAN" and (underlying.kind == "FUTURE" or local) else
                          "BLACK_76" if q.exercise_style == "EUROPEAN" and (underlying.kind == "FUTURE" or (local and underlying.kind == "INDEX" and q.model == "BLACK_76")) else
                          "BLACK_SCHOLES" if q.exercise_style == "EUROPEAN" else None)
        if q.model != expected_model:
            raise StrategyUnavailable("MODEL_UNAVAILABLE")
        if q.model_eligibility.status == "UNAVAILABLE":
            raise StrategyUnavailable(q.model_eligibility.reason or "MODEL_UNAVAILABLE")
        rate = rates.get(q.expiry_id)
        if rate is None:
            raise StrategyUnavailable("RATE_UNAVAILABLE")
        price = underlying.market_price
        if price is None or price <= 0 or q.strike <= 0 or underlying.domain.status != "VERIFIED":
            raise StrategyUnavailable("DOMAIN_UNAVAILABLE")
        if q.market_price is None:
            raise StrategyUnavailable("MARK_UNAVAILABLE")
        t = _years(reference, expiry)
        right = 1 if q.option_type == "CALL" else -1
        if pricing_adjustment is None or not isfinite(pricing_adjustment):
            raise StrategyUnavailable("MODEL_UNAVAILABLE")
        model_price = price + pricing_adjustment
        if not isfinite(model_price) or model_price <= 0:
            raise StrategyUnavailable("DOMAIN_UNAVAILABLE")
        result = research_iv(_MODELS[q.model], q.market_price, model_price, q.strike,
                             rate.continuous_rate, t, right, _TREE_STEPS)
        if not result.valid or result.volatility is None:
            raise StrategyUnavailable("IV_UNAVAILABLE")
        sigma = result.volatility
        model = quote_model_greeks(q, underlying, price, rate.continuous_rate, t, sigma, right,
                                   pricing_adjustment=pricing_adjustment)
        if not all(isfinite(v) for v in model) or abs(model[0] - q.market_price) > max(1e-8, abs(q.market_price) * 1e-7):
            raise StrategyUnavailable("IV_UNAVAILABLE")
        _deadline(end)
        return Calibration(sigma, rate.continuous_rate, expiry, None, pricing_adjustment)
    except StrategyUnavailable as exc:
        return Calibration(None, None, None, exc.reason)
    except (ArithmeticError, ValueError, RuntimeError):
        return Calibration(None, None, None, "NUMERICAL_FAILURE")


def _calibrate(leg: ResolvedLeg, reference: datetime, rates: dict[str, StrategyRateEvidenceDTO], end: float) -> Calibration:
    q = leg.option
    assert q is not None
    return calibrate_quote(q, leg.underlying, reference, rates, end, pricing_adjustment=leg.pricing_adjustment)


def _value(leg: ResolvedLeg, price: float, at: datetime, calibrations: dict[str, Calibration],
           request: StrategyValuationRequestDTO, *, shocked: bool, terminal: bool,
           end: float) -> tuple[float, Greeks | None]:
    _deadline(end)
    scale = _finite(leg.entry.quantity * leg.multiplier)
    q = leg.option
    if q is None:
        return _finite(scale * (price - leg.entry.entry_price)), (scale, 0., 0., 0.)
    if terminal and q.expiry_at is not None and datetime.fromisoformat(q.expiry_at) == at:
        return _finite(scale * (max(_right(leg) * (price - q.strike), 0.) - leg.entry.entry_price)), None
    c = calibrations[leg.instrument_id]
    if c.reason is not None or c.sigma is None or c.rate is None or c.expiry is None:
        raise StrategyUnavailable(c.reason or "IV_UNAVAILABLE")
    t = _years(at, c.expiry)
    if t <= 0:
        raise StrategyUnavailable("EXPIRED_SCENARIO")
    shift = request.iv_shocks_by_expiry.get(q.expiry_id, request.global_iv_shock) if shocked else 0.
    sigma = c.sigma + shift
    if price <= 0 or sigma <= 0 or not isfinite(sigma):
        raise StrategyUnavailable("INVALID_SCENARIO")
    try:
        if price + c.pricing_adjustment <= 0:
            raise StrategyUnavailable("INVALID_SCENARIO")
        values = quote_model_greeks(q, leg.underlying, price, c.rate, t, sigma, _right(leg),
                                    pricing_adjustment=c.pricing_adjustment)
        if not all(isfinite(v) for v in values):
            raise StrategyUnavailable("NUMERICAL_FAILURE")
        return _finite(scale * (values[0] - leg.entry.entry_price)), (
            _finite(scale * values[1]), _finite(scale * values[2]), _finite(scale * values[3]), _finite(scale * values[4]),
        )
    except StrategyUnavailable:
        raise
    except (ValueError, ArithmeticError, RuntimeError) as exc:
        raise StrategyUnavailable("NUMERICAL_FAILURE") from exc


def _curve_unavailable(kind: CurveKind, day: str | None, at: str | None, reason: Reason) -> StrategyCurveDTO:
    return StrategyCurveDTO(kind=kind, status="UNAVAILABLE", reason=reason, evaluation_date=day, evaluation_at=at, points=[])


def _point(resolved: ResolvedStrategy, request: StrategyValuationRequestDTO, mappings: dict[str, PriceMapping],
           domain: Interval, x: float, at: datetime, calibrations: dict[str, Calibration],
           *, shocked: bool, terminal: bool, end: float) -> StrategyPointDTO:
    prices: dict[str, float] = {}
    try:
        _deadline(end)
        exact = Fraction(str(x))
        prices = {key: _finite(float(mapping.price(exact))) for key, mapping in mappings.items()}
        if not domain.contains(exact):
            raise StrategyUnavailable("INVALID_SCENARIO")
        contributions = [
            LegContributionDTO(instrument_id=leg.instrument_id, pnl=_value(
                leg, prices[leg.underlying.underlying_id], at, calibrations, request,
                shocked=shocked, terminal=terminal, end=end,
            )[0]) for leg in resolved.legs
        ]
        return StrategyPointDTO(price_return=x, underlying_prices=prices, status="OK", reason=None,
                                pnl=_finite(fsum(c.pnl for c in contributions)), contributions=contributions)
    except StrategyUnavailable as exc:
        return StrategyPointDTO(price_return=x, underlying_prices=prices, status="UNAVAILABLE", reason=exc.reason,
                                pnl=None, contributions=[])
    except (ArithmeticError, ValueError):
        return StrategyPointDTO(price_return=x, underlying_prices=prices, status="UNAVAILABLE", reason="NUMERICAL_FAILURE",
                                pnl=None, contributions=[])


def _curve(resolved: ResolvedStrategy, request: StrategyValuationRequestDTO, kind: CurveKind, at: datetime,
           calibrations: dict[str, Calibration], *, shocked: bool, terminal: bool, end: float) -> StrategyCurveDTO:
    clock = at.isoformat()
    try:
        mappings, domain = price_mappings(resolved)
    except StrategyUnavailable as exc:
        return _curve_unavailable(kind, at.date().isoformat(), clock, exc.reason)
    points = [_point(resolved, request, mappings, domain, x, at, calibrations, shocked=shocked,
                     terminal=terminal, end=end) for x in request.curve_price_returns]
    valid = sum(p.status == "OK" for p in points)
    reason = next((p.reason for p in points if p.status != "OK"), None)
    return StrategyCurveDTO(kind=kind, status="OK" if valid == len(points) else "PARTIAL" if valid else "UNAVAILABLE",
                            reason=reason, evaluation_date=at.date().isoformat(), evaluation_at=clock, points=points)


def _groups(resolved: ResolvedStrategy, request: StrategyValuationRequestDTO, at: datetime,
            calibrations: dict[str, Calibration], *, shocked: bool, end: float,
            forced_reason: Reason | None = None) -> list[StrategyGreekGroupDTO]:
    groups = []
    for u in resolved.underlyings:
        reason = forced_reason
        values: list[Greeks] = []
        if reason is None:
            try:
                mappings, domain = price_mappings(replace(resolved, underlyings=(u,)))
                # Groups are independent: another deliverable's unavailable mark
                # or domain cannot erase this exact underlying's valid Greeks.
                if not domain.contains(Fraction(0)):
                    raise StrategyUnavailable("INVALID_SCENARIO")
                price = _finite(float(mappings[u.underlying_id].price(Fraction(0))))
                for leg in resolved.legs:
                    if leg.underlying.underlying_id != u.underlying_id:
                        continue
                    _, greeks = _value(leg, price, at, calibrations, request, shocked=shocked, terminal=False, end=end)
                    if greeks is None:
                        raise StrategyUnavailable("EXPIRED_SCENARIO")
                    values.append(greeks)
                delta, gamma, theta, vega = (_finite(fsum(v[i] for v in values)) for i in range(4))
                groups.append(StrategyGreekGroupDTO(underlying_id=u.underlying_id, quote_unit=u.quote_unit, status="OK",
                    reason=None, delta=delta, gamma=gamma, theta_annual=theta, vega_per_unit=vega))
                continue
            except StrategyUnavailable as exc:
                reason = exc.reason
            except (ArithmeticError, ValueError):
                reason = "NUMERICAL_FAILURE"
        groups.append(StrategyGreekGroupDTO(underlying_id=u.underlying_id, quote_unit=u.quote_unit,
                      status="UNAVAILABLE", reason=reason, delta=None, gamma=None, theta_annual=None, vega_per_unit=None))
    return groups


def _conditional_risk(curve: StrategyCurveDTO, diagnostics: list[str]) -> StrategyRiskDTO:
    # Only the sampled extrema; sign-changing brackets are NOT exhaustive
    # roots. Expose bracket endpoints as diagnostics, not fabricated exact roots.
    if curve.status != "OK":
        return unavailable_risk(curve.reason or "HORIZON_UNAVAILABLE")
    points = [(p.price_return, p.pnl) for p in curve.points if p.pnl is not None]
    if not points:
        return unavailable_risk("HORIZON_UNAVAILABLE")
    profit = max(0., max(p for _, p in points))
    loss = max(0., -min(p for _, p in points))
    for (x, a), (y, b) in zip(points, points[1:], strict=False):
        if a != 0 and b != 0 and (a > 0) != (b > 0):
            diagnostics.append(f"HORIZON_ROOT_BRACKET:{x:.12g}:{y:.12g}")
    diagnostics.append("HORIZON_EXTREMA_SAMPLED_NOT_GLOBAL")
    def metric(v: float) -> RiskMetricDTO:
        return RiskMetricDTO(kind="CONDITIONAL_RANGE", value=v, reason=None)
    ratio = metric(profit/loss) if loss > 0 and isfinite(profit/loss) else RiskMetricDTO(
        kind="UNAVAILABLE", value=None, reason="CONDITIONAL_ONLY")
    return StrategyRiskDTO(scope="CONDITIONAL_RANGE",
        domain=RootIntervalDTO(start=points[0][0], end=points[-1][0], start_inclusive=True, end_inclusive=True),
        max_profit=metric(profit), max_loss=metric(loss), reward_risk_ratio=ratio,
        breakevens=[x for x, p in points if p == 0], zero_intervals=[], roots_exhaustive=False)


def evaluate_strategy_valuation(resolved: ResolvedStrategy,
                                rates: tuple[StrategyRateEvidenceDTO, ...]) -> StrategyValuationDTO:
    request = resolved.request
    if not isinstance(request, StrategyValuationRequestDTO):
        raise StrategyUnavailable("INVALID_REQUEST")
    if set(request.iv_shocks_by_expiry) - {leg.option.expiry_id for leg in resolved.legs if leg.option is not None}:
        raise StrategyUnavailable("INVALID_SCENARIO")
    ref = datetime.fromisoformat(resolved.snapshot.reference_at)
    scenario_at = datetime.fromisoformat(request.scenario_at)
    end = monotonic() + _COMPUTATION_SECONDS
    verified_rates = _rate_map(resolved, rates)
    diagnostics = ["ACT_365F", "STICKY_STRIKE", "RESEARCH_CRR_128"]
    same_expiry, first_day, horizon_clock = expiry_boundary(resolved)
    boundary = datetime.fromisoformat(horizon_clock) if horizon_clock is not None else None
    current_req = request.model_copy(update={"global_price_shock": 0., "price_shocks_by_underlying": {}})
    current_resolved = replace(resolved, request=current_req)
    with research_pricing_scope():
        calibrated = {leg.instrument_id: _calibrate(leg, ref, verified_rates, end) for leg in resolved.legs if leg.option is not None}
        legs: list[SelectedLegDTO] = []
        for leg in resolved.legs:
            dto = leg.dto()
            if leg.option is not None:
                c = calibrated[leg.instrument_id]
                dto = dto.model_copy(update={"implied_volatility": c.sigma, "model_eligibility": CapabilityDTO(
                    status="ELIGIBLE" if c.reason is None else "UNAVAILABLE", reason=c.reason)})
            legs.append(dto)
        current = _curve(current_resolved, request, "CURRENT", ref, calibrated, shocked=False, terminal=False, end=end)
        current_greeks = _groups(current_resolved, request, ref, calibrated, shocked=False, end=end)
        if same_expiry:
            exact = evaluate_payoff(resolved)
            horizon, risk = exact.terminal, exact.risk
        elif boundary is None:
            horizon = _curve_unavailable("FIRST_EXPIRY_CONDITIONAL", first_day, None, "HORIZON_UNAVAILABLE")
            risk = unavailable_risk("HORIZON_UNAVAILABLE")
        else:
            horizon = _curve(resolved, request, "FIRST_EXPIRY_CONDITIONAL", boundary, calibrated,
                             shocked=True, terminal=True, end=end)
            risk = _conditional_risk(horizon, diagnostics)
        forced: Reason | None = None
        if scenario_at < ref:
            forced = "INVALID_SCENARIO"
        elif boundary is not None and scenario_at == boundary:
            # Boundary is a hypothetical terminal/horizon calculation, NOT
            # positive-time Greeks. The scenario sampling cap stays unchanged.
            samples = set(request.curve_price_returns)
            scenario = horizon.model_copy(update={"kind": "SCENARIO", "points": [p for p in horizon.points if p.price_return in samples]})
            # Recompute point-level status after narrowing exact payoff knots.
            if scenario.points:
                n = sum(p.status == "OK" for p in scenario.points)
                scenario = StrategyCurveDTO(kind="SCENARIO", status="OK" if n==len(scenario.points) else "PARTIAL" if n else "UNAVAILABLE",
                    reason=next((p.reason for p in scenario.points if p.status!="OK"),None),
                    evaluation_date=first_day,evaluation_at=request.scenario_at,points=scenario.points)
            else:
                scenario = _curve_unavailable("SCENARIO", first_day, request.scenario_at, horizon.reason or "HORIZON_UNAVAILABLE")
            forced = "EXPIRED_SCENARIO"
        elif scenario_at.date().isoformat() >= first_day and (
            boundary is None or scenario_at >= boundary or any(
                leg.option is not None and leg.option.expiry_date == first_day and leg.option.expiry_clock != "VERIFIED_CUTOFF"
                for leg in resolved.legs
            )
        ):
            forced = "EXPIRED_SCENARIO"
        if forced is not None:
            if not (boundary is not None and scenario_at == boundary and forced == "EXPIRED_SCENARIO"):
                scenario = _curve_unavailable("SCENARIO", scenario_at.date().isoformat(), request.scenario_at, forced)
            scenario_greeks = _groups(resolved, request, scenario_at, calibrated, shocked=True, end=end, forced_reason=forced)
        else:
            scenario = _curve(resolved, request, "SCENARIO", scenario_at, calibrated, shocked=True, terminal=False, end=end)
            scenario_greeks = _groups(resolved, request, scenario_at, calibrated, shocked=True, end=end)
    return StrategyValuationDTO(snapshot=resolved.snapshot, reference_underlying_id=request.reference_underlying_id,
        global_price_shock=request.global_price_shock, price_shocks_by_underlying=request.price_shocks_by_underlying,
        global_iv_shock=request.global_iv_shock, iv_shocks_by_expiry=request.iv_shocks_by_expiry,
        rates=list(verified_rates.values()), model_diagnostics=diagnostics, legs=legs, current=current, scenario=scenario,
        horizon=horizon, horizon_risk=risk, current_greeks=current_greeks, scenario_greeks=scenario_greeks,
        tree_steps=128, iv_policy="STICKY_STRIKE", day_count="ACT/365F")
