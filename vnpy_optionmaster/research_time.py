"""T05: safe unified observation time for historical multi-expiry option risk.

Callers supply timezone-aware reference/scenario datetimes and explicit expiry
cutoff metadata. Missing cutoff time is represented by a date; the entire
calendar expiry date is conservatively unavailable. No system clock, trading,
market feeds, exercise, delivery, or settlement is used.

The time decay basis is explicitly ACT/365F: elapsed *UTC seconds* / 86400 / 365.
Each leg's input reference maturity must be consistent with its expiry date or
exact cutoff (date-only uncertainty allows any expiry instant in that day).

T04 repricing is intentionally reused with baseline IV locked at the ORIGINAL
time so the historical price and reference-vol surface cannot drift as the
scenario clock moves. This is a two-pass offline implementation; T06 can reuse
cached inputs as a separately authorized optimization.
"""
from dataclasses import dataclass, replace
from datetime import date, datetime, time, timedelta, timezone
from math import fsum, isfinite

from vnpy_optionmaster.research_combination import ResearchCombinationLeg
from vnpy_optionmaster.research_risk import (
    ResearchValuationRequest,
    ResearchValuationResult,
    ResearchValuationStatus,
)
from vnpy_optionmaster.research_scenario import (
    ResearchScenarioEngine,
    ResearchScenarioLeg,
    ResearchScenarioRequest,
)

_SECONDS_PER_YEAR = 365.0 * 86400.0
_YEAR_FRACTION_TOLERANCE = 1e-8


@dataclass(frozen=True)
class ResearchTimeRequest:
    """Historical calculation with one shared observed scenario time.

    expiry_boundaries maps explicit opaque expiry IDs to date or aware datetime
    from authoritative contract metadata. date implies no reliable intraday
    cutoff, so scenario_at.date() MUST be strictly earlier.
    """

    scenario: ResearchScenarioRequest
    reference_at: datetime
    scenario_at: datetime
    expiry_boundaries: tuple[tuple[str, date | datetime], ...]
    day_count_basis: str = "ACT/365F"


@dataclass(frozen=True)
class ResearchTimeContribution:
    contract_id: str
    underlying_contract_id: str
    expiry_id: str
    signed_quantity: int
    contract_multiplier: float
    baseline_time_to_expiry: float
    scenario_time_to_expiry: float
    baseline_iv: float
    scenario_iv: float
    baseline_value: float
    scenario_value: float
    value_change: float
    baseline_result: ResearchValuationResult
    scenario_result: ResearchValuationResult


@dataclass(frozen=True)
class ResearchTimeResult:
    status: ResearchValuationStatus
    reason_code: str | None = None
    reference_at: datetime | None = None
    scenario_at: datetime | None = None
    baseline_value: float | None = None
    scenario_value: float | None = None
    value_change: float | None = None
    contributions: tuple[ResearchTimeContribution, ...] = ()
    underlying_contract_ids: tuple[str, ...] = ()
    failed_leg_index: int | None = None
    failed_contract_id: str | None = None


def _unavailable(
    reason: str,
    *,
    index: int | None = None,
    contract_id: str | None = None,
) -> ResearchTimeResult:
    """An invalid leg or time invalidates the entire research result."""
    return ResearchTimeResult(
        status=ResearchValuationStatus.UNAVAILABLE,
        reason_code=reason,
        failed_leg_index=index,
        failed_contract_id=contract_id,
    )


def _aware_in_zone(value: object, reference: datetime | None = None) -> bool:
    if not isinstance(value, datetime):
        return False
    if value.tzinfo is None or value.utcoffset() is None:
        return False
    return reference is None or value.tzinfo == reference.tzinfo


def _year_fraction_between(start: datetime, end: datetime) -> float:
    """Use UTC durations across DST, never local wall-clock subtraction."""
    start_utc = start.astimezone(timezone.utc)
    end_utc = end.astimezone(timezone.utc)
    return (end_utc - start_utc).total_seconds() / _SECONDS_PER_YEAR


def _canonical_id(value: object) -> bool:
    return (
        isinstance(value, str)
        and bool(value)
        and value == value.strip()
        and not any(character.isspace() for character in value)
    )


def _parse_boundaries(
    source: object,
    known: set[str],
    reference: datetime,
) -> tuple[dict[str, date | datetime] | None, str | None]:
    if not isinstance(source, tuple):
        return None, "INVALID_INPUT"
    result: dict[str, date | datetime] = {}
    for entry in source:
        if not isinstance(entry, tuple) or len(entry) != 2:
            return None, "INVALID_INPUT"
        key, bound = entry
        if not _canonical_id(key) or key in result:
            return None, "INVALID_INPUT"
        if isinstance(bound, datetime):
            if not _aware_in_zone(bound, reference):
                return None, "INVALID_INPUT"
        elif not isinstance(bound, date) or bound == date.max:
            # date.max has no following day to establish a conservative cutoff.
            return None, "INVALID_INPUT"
        result[key] = bound
    if set(result) - known:
        return None, "UNKNOWN_EXPIRY_BOUNDARY"
    if known - set(result):
        return None, "MISSING_EXPIRY_BOUNDARY"
    return result, None


def _validate_expiry(
    *,
    reference: datetime,
    scenario: datetime,
    bound: date | datetime,
    reference_maturity: float,
) -> str | None:
    if isinstance(bound, datetime):
        # Same trusted market timezone was checked at boundary parsing.
        if reference.astimezone(timezone.utc) >= bound.astimezone(timezone.utc):
            return "EXPIRED_BASELINE"
        if scenario.astimezone(timezone.utc) >= bound.astimezone(timezone.utc):
            return "EXPIRED_SCENARIO"
        expected_maturity = _year_fraction_between(reference, bound)
        if abs(reference_maturity - expected_maturity) > _YEAR_FRACTION_TOLERANCE:
            return "EXPIRY_METADATA_CONFLICT"
    else:
        # A date-only expiry does not prove a safe intraday exercise cutoff.
        if reference.date() >= bound:
            return "EXPIRED_BASELINE"
        if scenario.date() >= bound:
            return "EXPIRED_SCENARIO"
        start_of_date = datetime.combine(bound, time.min, tzinfo=reference.tzinfo)
        start_of_next_date = datetime.combine(bound + timedelta(days=1), time.min, tzinfo=reference.tzinfo)
        earliest_maturity = _year_fraction_between(reference, start_of_date)
        latest_maturity = _year_fraction_between(reference, start_of_next_date)
        if not (
            earliest_maturity - _YEAR_FRACTION_TOLERANCE
            <= reference_maturity
            <= latest_maturity + _YEAR_FRACTION_TOLERANCE
        ):
            return "EXPIRY_METADATA_CONFLICT"
    return None


class ResearchTimeEngine:
    """Time-adjust T04 combination scenario with strict earliest-expiry gates."""

    @staticmethod
    def evaluate(request: ResearchTimeRequest) -> ResearchTimeResult:
        if not isinstance(request, ResearchTimeRequest):
            return _unavailable("INVALID_INPUT")
        source = request.scenario
        if (
            not isinstance(source, ResearchScenarioRequest)
            or not isinstance(source.legs, tuple)
            or len(source.legs) < 2
        ):
            return _unavailable("INVALID_INPUT")
        if request.day_count_basis != "ACT/365F":
            return _unavailable("UNSUPPORTED_DAY_COUNT_BASIS")
        reference = request.reference_at
        scenario = request.scenario_at
        if not (
            _aware_in_zone(reference)
            and _aware_in_zone(scenario, reference)
        ):
            return _unavailable("INVALID_SCENARIO_TIME")
        # A user's requested shift is forward-only, measured by UTC elapsed time.
        elapsed_years = _year_fraction_between(reference, scenario)
        if elapsed_years < 0 or not isfinite(elapsed_years):
            return _unavailable("INVALID_SCENARIO_TIME")

        known: set[str] = set()
        for item in source.legs:
            if not isinstance(item, ResearchScenarioLeg):
                return _unavailable("INVALID_INPUT")
            if not _canonical_id(item.expiry_id):
                return _unavailable("INVALID_INPUT")
            known.add(item.expiry_id)
        boundaries, error = _parse_boundaries(request.expiry_boundaries, known, reference)
        if error is not None or boundaries is None:
            return _unavailable(error or "INVALID_INPUT")

        shifted_maturities: list[float] = []
        for index, item in enumerate(source.legs):
            if (
                not isinstance(item.selected_leg, ResearchCombinationLeg)
                or not isinstance(item.selected_leg.valuation, ResearchValuationRequest)
            ):
                return _unavailable("INVALID_INPUT", index=index)
            baseline_option = item.selected_leg.valuation
            contract_id = baseline_option.contract_id if isinstance(baseline_option.contract_id, str) else None
            t0 = baseline_option.time_to_expiry
            if (
                isinstance(t0, bool)
                or not isinstance(t0, (float, int))
            ):
                return _unavailable("INVALID_INPUT", index=index, contract_id=contract_id)
            try:
                valid_t0 = isfinite(t0) and t0 > 0
            except (OverflowError, ValueError):
                valid_t0 = False
            if not valid_t0:
                return _unavailable("INVALID_INPUT", index=index, contract_id=contract_id)
            boundary_error = _validate_expiry(
                reference=reference,
                scenario=scenario,
                bound=boundaries[item.expiry_id],
                reference_maturity=float(t0),
            )
            if boundary_error is not None:
                return _unavailable(boundary_error, index=index, contract_id=contract_id)
            remaining = float(t0) - elapsed_years
            if not isfinite(remaining) or remaining <= 0:
                return _unavailable("EXPIRED_SCENARIO", index=index, contract_id=contract_id)
            shifted_maturities.append(remaining)

        original = ResearchScenarioEngine.evaluate(source)
        if original.status is not ResearchValuationStatus.OK:
            return _unavailable(
                original.reason_code or "NUMERICAL_FAILURE",
                index=original.failed_leg_index,
                contract_id=original.failed_contract_id,
            )
        if original.baseline_value is None:
            return _unavailable("NUMERICAL_FAILURE")

        shifted_legs: list[ResearchScenarioLeg] = []
        for item, base_result, remaining in zip(
            source.legs, original.contributions, shifted_maturities, strict=True,
        ):
            # The historical sigma may have come from an observed reference
            # price. Lock it before changing T, rather than re-solving at T'.
            old_valuation = item.selected_leg.valuation
            shifted_valuation = replace(
                old_valuation,
                time_to_expiry=remaining,
                implied_volatility=base_result.baseline_iv,
                reference_option_price=None,
            )
            shifted_legs.append(replace(
                item,
                selected_leg=replace(item.selected_leg, valuation=shifted_valuation),
            ))
        updated_request = replace(source, legs=tuple(shifted_legs))
        shifted = ResearchScenarioEngine.evaluate(updated_request)
        if shifted.status is not ResearchValuationStatus.OK:
            return _unavailable(
                shifted.reason_code or "NUMERICAL_FAILURE",
                index=shifted.failed_leg_index,
                contract_id=shifted.failed_contract_id,
            )
        if shifted.scenario_value is None:
            return _unavailable("NUMERICAL_FAILURE")

        contributions: list[ResearchTimeContribution] = []
        for index, (leg, start, end, remaining) in enumerate(zip(
            source.legs, original.contributions, shifted.contributions,
            shifted_maturities, strict=True,
        )):
            delta = end.scenario_value - start.baseline_value
            if not isfinite(delta):
                return _unavailable(
                    "NUMERICAL_FAILURE", index=index, contract_id=start.contract_id,
                )
            contributions.append(
                ResearchTimeContribution(
                    contract_id=start.contract_id,
                    underlying_contract_id=start.underlying_contract_id,
                    expiry_id=leg.expiry_id,
                    signed_quantity=start.signed_quantity,
                    contract_multiplier=start.contract_multiplier,
                    baseline_time_to_expiry=leg.selected_leg.valuation.time_to_expiry,
                    scenario_time_to_expiry=remaining,
                    baseline_iv=start.baseline_iv,
                    scenario_iv=end.scenario_iv,
                    baseline_value=start.baseline_value,
                    scenario_value=end.scenario_value,
                    value_change=delta,
                    baseline_result=start.baseline_result,
                    scenario_result=end.scenario_result,
                )
            )

        try:
            result_change = shifted.scenario_value - original.baseline_value
            contribution_change = fsum(value.value_change for value in contributions)
        except (OverflowError, ValueError):
            return _unavailable("NUMERICAL_FAILURE")
        if not all(isfinite(x) for x in (result_change, contribution_change)):
            return _unavailable("NUMERICAL_FAILURE")
        # The aggregated values and per-leg sum differ only by floating
        # cancellation. The full-risk change is always scenario minus baseline.
        return ResearchTimeResult(
            status=ResearchValuationStatus.OK,
            reference_at=reference,
            scenario_at=scenario,
            baseline_value=original.baseline_value,
            scenario_value=shifted.scenario_value,
            value_change=result_change,
            contributions=tuple(contributions),
            underlying_contract_ids=original.underlying_contract_ids,
        )
