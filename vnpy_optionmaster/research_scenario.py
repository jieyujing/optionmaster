"""T04: deterministic price/IV scenario repricing for research combinations.

The historical baseline is computed once from the original explicit inputs.
Scenario prices and volatilities are then repriced independently through the
same validated one-leg and combination engines (never interpreted as fills).
This module does not read market history, trade, or alter remaining expiry time.

Price shocks use decimal percentages of the ORIGINAL underlying price:
    S_s = S_0 * (1 + effective_price_shock)
IV shocks use absolute decimal volatility points:
    IV_s = IV_0 + effective_iv_shock
An underlying/expiry override REPLACES its matching parallel default. An
additional common shock is ADDITIVE to the effective value, not compounded.
"""
from dataclasses import dataclass, replace
from math import isfinite

from vnpy_optionmaster.research_combination import (
    ResearchCombinationEngine,
    ResearchCombinationLeg,
    ResearchCombinationRequest,
)
from vnpy_optionmaster.research_risk import (
    ResearchValuationRequest,
    ResearchValuationResult,
    ResearchValuationStatus,
)


@dataclass(frozen=True)
class ResearchScenarioLeg:
    """T03 combination leg with explicit historical expiry identity.

    expiry_id must come from historical contract metadata. It is not parsed
    from the option symbol or inferred from numeric time_to_expiry.
    """

    selected_leg: ResearchCombinationLeg
    expiry_id: str


@dataclass(frozen=True)
class ResearchScenarioRequest:
    """Complete scenario inputs, including optional contract/expiry overrides.

    Override pairs are immutable tuples to prevent mid-request changes and
    detect duplicate keys rather than silently replacing earlier values.
    Keys must match the actual leg underlying_contract_id or expiry_id.
    """

    legs: tuple[ResearchScenarioLeg, ...]
    parallel_price_shock: float = 0.0
    price_shocks_by_underlying: tuple[tuple[str, float], ...] = ()
    parallel_iv_shock: float = 0.0
    iv_shocks_by_expiry: tuple[tuple[str, float], ...] = ()
    additional_price_shock: float = 0.0
    additional_iv_shock: float = 0.0


@dataclass(frozen=True)
class ResearchScenarioContribution:
    """One fully repriced leg with separate baseline and scenario Greeks."""

    contract_id: str
    underlying_contract_id: str
    expiry_id: str
    signed_quantity: int
    contract_multiplier: float
    baseline_iv: float
    scenario_iv: float
    effective_price_shock: float
    effective_iv_shock: float
    baseline_value: float
    scenario_value: float
    value_change: float
    baseline_result: ResearchValuationResult
    scenario_result: ResearchValuationResult


@dataclass(frozen=True)
class ResearchScenarioResult:
    status: ResearchValuationStatus
    reason_code: str | None = None
    baseline_value: float | None = None
    scenario_value: float | None = None
    value_change: float | None = None
    contributions: tuple[ResearchScenarioContribution, ...] = ()
    underlying_contract_ids: tuple[str, ...] = ()
    failed_leg_index: int | None = None
    failed_contract_id: str | None = None


def _unavailable(
    reason: str,
    *,
    index: int | None = None,
    contract_id: str | None = None,
) -> ResearchScenarioResult:
    """No invalid scenario may return a partial portfolio valuation."""
    return ResearchScenarioResult(
        status=ResearchValuationStatus.UNAVAILABLE,
        reason_code=reason,
        failed_leg_index=index,
        failed_contract_id=contract_id,
    )


def _finite(value: object) -> bool:
    if isinstance(value, bool) or not isinstance(value, (float, int)):
        return False
    try:
        return isfinite(value)
    except (OverflowError, ValueError):
        # Extremely large Python integers cannot be converted to float.
        return False


def _canonical_id(value: object) -> bool:
    return (
        isinstance(value, str)
        and bool(value)
        and value == value.strip()
        and not any(character.isspace() for character in value)
    )


def _read_overrides(
    entries: object,
    known_ids: set[str],
) -> tuple[dict[str, float] | None, str | None]:
    """Strictly parse immutable pair tuples; unknown/duplicate IDs fail closed."""
    if not isinstance(entries, tuple):
        return None, "INVALID_INPUT"
    result: dict[str, float] = {}
    for item in entries:
        if not isinstance(item, tuple) or len(item) != 2:
            return None, "INVALID_INPUT"
        key, value = item
        if not _canonical_id(key) or not _finite(value):
            return None, "INVALID_INPUT"
        if key in result:
            return None, "INVALID_INPUT"
        if key not in known_ids:
            return None, "UNKNOWN_SHOCK_TARGET"
        result[key] = float(value)
    return result, None


class ResearchScenarioEngine:
    """Apply independent underlying-month / expiry IV shocks to T03 legs."""

    @staticmethod
    def evaluate(request: ResearchScenarioRequest) -> ResearchScenarioResult:
        if (
            not isinstance(request, ResearchScenarioRequest)
            or not isinstance(request.legs, tuple)
            or len(request.legs) < 2
        ):
            return _unavailable("INVALID_INPUT")

        shock_numbers = (
            request.parallel_price_shock,
            request.parallel_iv_shock,
            request.additional_price_shock,
            request.additional_iv_shock,
        )
        if not all(_finite(value) for value in shock_numbers):
            return _unavailable("INVALID_INPUT")

        known_underlyings: set[str] = set()
        known_expiries: dict[str, float] = {}
        source_legs: list[ResearchCombinationLeg] = []
        for index, item in enumerate(request.legs):
            if not isinstance(item, ResearchScenarioLeg):
                return _unavailable("INVALID_INPUT", index=index)
            leg = item.selected_leg
            if not isinstance(leg, ResearchCombinationLeg):
                return _unavailable("INVALID_INPUT", index=index)
            option = leg.valuation
            if not isinstance(option, ResearchValuationRequest):
                return _unavailable("INVALID_INPUT", index=index)
            contract_id = option.contract_id if isinstance(option.contract_id, str) else None
            if not _canonical_id(item.expiry_id):
                return _unavailable("INVALID_INPUT", index=index, contract_id=contract_id)
            if not _canonical_id(option.underlying_contract_id):
                return _unavailable("INVALID_INPUT", index=index, contract_id=contract_id)
            if not _finite(option.time_to_expiry) or option.time_to_expiry <= 0:
                return _unavailable("INVALID_INPUT", index=index, contract_id=contract_id)
            if not _finite(option.underlying_shock):
                return _unavailable("INVALID_INPUT", index=index, contract_id=contract_id)
            if option.underlying_shock != 0:
                # T01/T03 offer per-leg underlying_shock; T04 takes an
                # UNSHOCKED reference only to avoid hidden baseline drift.
                return _unavailable(
                    "BASELINE_SHOCK_CONFLICT", index=index, contract_id=contract_id,
                )
            duration = known_expiries.get(item.expiry_id)
            if duration is not None and duration != option.time_to_expiry:
                return _unavailable(
                    "EXPIRY_METADATA_CONFLICT", index=index, contract_id=contract_id,
                )
            known_expiries[item.expiry_id] = option.time_to_expiry
            known_underlyings.add(option.underlying_contract_id)
            source_legs.append(leg)

        price_overrides, price_error = _read_overrides(
            request.price_shocks_by_underlying, known_underlyings,
        )
        if price_error is not None:
            return _unavailable(price_error)
        iv_overrides, iv_error = _read_overrides(
            request.iv_shocks_by_expiry, set(known_expiries),
        )
        if iv_error is not None:
            return _unavailable(iv_error)
        if price_overrides is None or iv_overrides is None:
            return _unavailable("INVALID_INPUT")

        baseline = ResearchCombinationEngine.evaluate(
            ResearchCombinationRequest(legs=tuple(source_legs)),
        )
        if baseline.status is not ResearchValuationStatus.OK:
            return _unavailable(
                baseline.reason_code or "NUMERICAL_FAILURE",
                index=baseline.failed_leg_index,
                contract_id=baseline.failed_contract_id,
            )
        if baseline.baseline_value is None:
            return _unavailable("NUMERICAL_FAILURE")

        scenario_legs: list[ResearchCombinationLeg] = []
        effective_moves: list[tuple[float, float, float, float]] = []
        for index, (item, baseline_leg) in enumerate(
            zip(request.legs, baseline.contributions, strict=True)
        ):
            option = item.selected_leg.valuation
            contract_id = option.contract_id
            baseline_iv = baseline_leg.valuation_result.implied_volatility
            if baseline_iv is None or not _finite(baseline_iv) or baseline_iv <= 0:
                return _unavailable(
                    "NUMERICAL_FAILURE", index=index, contract_id=contract_id,
                )
            price_move = (
                price_overrides.get(option.underlying_contract_id, request.parallel_price_shock)
                + request.additional_price_shock
            )
            iv_move = (
                iv_overrides.get(item.expiry_id, request.parallel_iv_shock)
                + request.additional_iv_shock
            )
            scenario_underlying = option.underlying_price * (1.0 + price_move)
            scenario_iv = baseline_iv + iv_move
            if not all(_finite(value) for value in (
                price_move, iv_move, scenario_underlying, scenario_iv,
            )) or scenario_underlying <= 0 or scenario_iv <= 0:
                return _unavailable(
                    "INVALID_SCENARIO", index=index, contract_id=contract_id,
                )
            scenario_option = replace(
                option,
                underlying_price=scenario_underlying,
                implied_volatility=scenario_iv,
                reference_option_price=None,
                underlying_shock=0.0,
            )
            scenario_legs.append(replace(item.selected_leg, valuation=scenario_option))
            effective_moves.append((price_move, iv_move, baseline_iv, scenario_iv))

        scenario = ResearchCombinationEngine.evaluate(
            ResearchCombinationRequest(legs=tuple(scenario_legs)),
        )
        if scenario.status is not ResearchValuationStatus.OK:
            return _unavailable(
                scenario.reason_code or "NUMERICAL_FAILURE",
                index=scenario.failed_leg_index,
                contract_id=scenario.failed_contract_id,
            )
        if scenario.baseline_value is None:
            return _unavailable("NUMERICAL_FAILURE")

        aggregate_delta = scenario.baseline_value - baseline.baseline_value
        if not _finite(aggregate_delta):
            return _unavailable("NUMERICAL_FAILURE")

        contributions: list[ResearchScenarioContribution] = []
        for i, (item, historical, shocked, effective) in enumerate(zip(
            request.legs, baseline.contributions, scenario.contributions, effective_moves,
            strict=True,
        )):
            delta_value = shocked.baseline_value - historical.baseline_value
            if not _finite(delta_value):
                return _unavailable(
                    "NUMERICAL_FAILURE",
                    index=i,
                    contract_id=historical.contract_id,
                )
            price_move, iv_move, original_iv, shock_iv = effective
            contributions.append(
                ResearchScenarioContribution(
                    contract_id=historical.contract_id,
                    underlying_contract_id=historical.underlying_contract_id,
                    expiry_id=item.expiry_id,
                    signed_quantity=historical.signed_quantity,
                    contract_multiplier=historical.contract_multiplier,
                    baseline_iv=original_iv,
                    scenario_iv=shock_iv,
                    effective_price_shock=price_move,
                    effective_iv_shock=iv_move,
                    baseline_value=historical.baseline_value,
                    scenario_value=shocked.baseline_value,
                    value_change=delta_value,
                    baseline_result=historical.valuation_result,
                    scenario_result=shocked.valuation_result,
                )
            )
        return ResearchScenarioResult(
            status=ResearchValuationStatus.OK,
            baseline_value=baseline.baseline_value,
            scenario_value=scenario.baseline_value,
            value_change=aggregate_delta,
            contributions=tuple(contributions),
            underlying_contract_ids=baseline.underlying_contract_ids,
        )
