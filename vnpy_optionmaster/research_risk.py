"""Standalone research-only one-leg option valuation interface.

This module has no dependency on Qt, CTP, real account positions, or market
data sources. Its caller provides all reference pricing inputs explicitly.
Multi-leg aggregation is separately scoped to T03.
"""
from dataclasses import dataclass
from enum import Enum
from math import isfinite

from vnpy_optionmaster.pricing.binomial_tree import DEFAULT_STEP
from vnpy_optionmaster.pricing.iv_solver import IVResult, IVStatus
from vnpy_optionmaster.research_pricing import (
    ResearchPricingModel,
    research_greeks,
    research_iv,
)


class ResearchValuationStatus(str, Enum):
    OK = "OK"
    UNAVAILABLE = "UNAVAILABLE"


@dataclass(frozen=True)
class ResearchValuationRequest:
    """Explicit inputs for one option leg, with one and only one IV source.

    A reference option price is an IV inversion input, never an entry cost.
    The supplied volatility and reference price are mutually exclusive.
    """

    contract_id: str
    underlying_contract_id: str
    underlying_price: float
    strike: float
    risk_free_rate: float
    time_to_expiry: float
    implied_volatility: float | None
    option_type: int
    signed_quantity: int
    contract_multiplier: float
    underlying_shock: float = 0.0
    model_name: ResearchPricingModel | str = ResearchPricingModel.BLACK_76
    reference_option_price: float | None = None
    tree_steps: int = DEFAULT_STEP


@dataclass(frozen=True)
class ResearchGreeks:
    """Signed monetary position sensitivities: per price unit, annual theta, per 1.0 IV."""

    delta: float
    gamma: float
    theta: float
    vega: float


@dataclass(frozen=True)
class ResearchValuationResult:
    status: ResearchValuationStatus
    reason_code: str | None = None
    baseline_value: float | None = None
    scenario_value: float | None = None
    value_change: float | None = None
    implied_volatility: float | None = None
    iv_result: IVResult | None = None
    baseline_greeks: ResearchGreeks | None = None
    scenario_greeks: ResearchGreeks | None = None


def _unavailable(reason: str, iv_result: IVResult | None = None) -> ResearchValuationResult:
    return ResearchValuationResult(
        status=ResearchValuationStatus.UNAVAILABLE,
        reason_code=reason,
        iv_result=iv_result,
    )


def _finite_number(value: object) -> bool:
    """Reject nonfinite or unrepresentable numbers without raising on huge ints."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return False
    try:
        return isfinite(value)
    except (OverflowError, ValueError):
        # Python's arbitrary-precision int may not fit in a float.
        return False


class ResearchRiskEngine:
    """One-leg evaluate(request) boundary, backward-compatible with T01."""

    @staticmethod
    def evaluate(request: ResearchValuationRequest) -> ResearchValuationResult:
        if not isinstance(request, ResearchValuationRequest):
            return _unavailable("INVALID_INPUT")

        try:
            model = ResearchPricingModel(request.model_name)
        except (ValueError, TypeError):
            return _unavailable("UNSUPPORTED_MODEL")

        if (
            not isinstance(request.contract_id, str)
            or not request.contract_id.strip()
            or not isinstance(request.underlying_contract_id, str)
            or not request.underlying_contract_id.strip()
            or type(request.signed_quantity) is not int
            or request.signed_quantity == 0
            or type(request.option_type) is not int
            or request.option_type not in (-1, 1)
            or type(request.tree_steps) is not int
            or not 2 <= request.tree_steps <= 512
        ):
            return _unavailable("INVALID_INPUT")

        numbers = (
            request.underlying_price,
            request.strike,
            request.risk_free_rate,
            request.time_to_expiry,
            request.contract_multiplier,
            request.underlying_shock,
        )
        if not all(_finite_number(x) for x in numbers):
            return _unavailable("INVALID_INPUT")
        if (
            request.underlying_price <= 0
            or request.strike <= 0
            or request.time_to_expiry <= 0
            or request.contract_multiplier <= 0
            or request.underlying_shock <= -1
        ):
            return _unavailable("INVALID_INPUT")

        input_volatility = request.implied_volatility
        input_option_price = request.reference_option_price
        if (input_volatility is None) == (input_option_price is None):
            return _unavailable("INVALID_INPUT")
        if input_volatility is not None and (
            not _finite_number(input_volatility) or input_volatility <= 0
        ):
            return _unavailable("INVALID_INPUT")
        if input_option_price is not None and (
            not _finite_number(input_option_price) or input_option_price <= 0
        ):
            return _unavailable("INVALID_INPUT")

        iv_result: IVResult | None = None
        try:
            scenario_underlying = request.underlying_price * (1.0 + request.underlying_shock)
            if not isfinite(scenario_underlying) or scenario_underlying <= 0:
                return _unavailable("INVALID_INPUT")

            if input_option_price is not None:
                # Price cannot be substituted for a trade fill or marked as P&L.
                iv_result = research_iv(
                    model,
                    input_option_price,
                    request.underlying_price,
                    request.strike,
                    request.risk_free_rate,
                    request.time_to_expiry,
                    request.option_type,
                    request.tree_steps,
                )
                if not iv_result.valid:
                    code = {
                        IVStatus.INVALID_INPUT: "INVALID_INPUT",
                        IVStatus.NO_SOLUTION: "IV_NO_SOLUTION",
                        IVStatus.MAX_ITERATIONS: "IV_MAX_ITERATIONS",
                        IVStatus.NUMERICAL_FAILURE: "NUMERICAL_FAILURE",
                    }.get(iv_result.status, "NUMERICAL_FAILURE")
                    return _unavailable(code, iv_result)
                volatility = iv_result.volatility
            else:
                volatility = input_volatility
            if volatility is None:
                return _unavailable("NUMERICAL_FAILURE", iv_result)

            base = research_greeks(
                model, request.underlying_price, request.strike,
                request.risk_free_rate, request.time_to_expiry,
                volatility, request.option_type, request.tree_steps,
            )
            scenario = research_greeks(
                model, scenario_underlying, request.strike,
                request.risk_free_rate, request.time_to_expiry,
                volatility, request.option_type, request.tree_steps,
            )
            scale = request.signed_quantity * request.contract_multiplier
            baseline_value = float(base[0]) * scale
            scenario_value = float(scenario[0]) * scale
            value_change = scenario_value - baseline_value
            if not all(isfinite(x) for x in (
                *base, *scenario, scale, baseline_value, scenario_value, value_change,
            )):
                return _unavailable("NUMERICAL_FAILURE", iv_result)
            baseline_greeks = ResearchGreeks(*(float(x) * scale for x in base[1:]))
            scenario_greeks = ResearchGreeks(*(float(x) * scale for x in scenario[1:]))
            if not all(isfinite(x) for x in (
                *vars(baseline_greeks).values(), *vars(scenario_greeks).values(),
            )):
                return _unavailable("NUMERICAL_FAILURE", iv_result)
        except (ArithmeticError, ValueError, OverflowError, ZeroDivisionError):
            return _unavailable("NUMERICAL_FAILURE", iv_result)

        return ResearchValuationResult(
            status=ResearchValuationStatus.OK,
            baseline_value=baseline_value,
            scenario_value=scenario_value,
            value_change=value_change,
            implied_volatility=volatility,
            iv_result=iv_result,
            baseline_greeks=baseline_greeks,
            scenario_greeks=scenario_greeks,
        )
