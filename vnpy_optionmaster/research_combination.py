"""Offline multi-leg research combinations over the independently tested one-leg engine.

T03 handles combination identity, validation, and signed monetary aggregation.
Pricing/IV/Greeks remain owned by ResearchRiskEngine; this module never reads
history, accounts, Qt state or trading gateways. No cross-month Greek total is
invented: the per-leg valuation retains its model-specific sensitivities.
"""
from dataclasses import dataclass
from enum import Enum
from math import fsum, isfinite

from vnpy_optionmaster.research_pricing import ResearchPricingModel
from vnpy_optionmaster.research_risk import (
    ResearchRiskEngine,
    ResearchValuationRequest,
    ResearchValuationResult,
    ResearchValuationStatus,
)


class ResearchAssetClass(str, Enum):
    FUTURES_OPTION = "futures_option"
    INDEX_OPTION = "index_option"
    ETF_OPTION = "etf_option"


@dataclass(frozen=True)
class ResearchCombinationLeg:
    """A selected leg and its authoritative, caller-supplied product identity.

    Product family and exchange MUST come from validated contract metadata.
    They must not be inferred by slicing the contract/underlying symbol.
    """

    valuation: ResearchValuationRequest
    exchange: str
    product_family_id: str
    asset_class: ResearchAssetClass | str


@dataclass(frozen=True)
class ResearchCombinationRequest:
    """Ordered multi-leg research combination; at least two distinct options."""

    legs: tuple[ResearchCombinationLeg, ...]


@dataclass(frozen=True)
class ResearchLegContribution:
    """Monetary leg values, plus the full one-leg result for IV and Greeks."""

    contract_id: str
    underlying_contract_id: str
    signed_quantity: int
    contract_multiplier: float
    baseline_value: float
    scenario_value: float
    value_change: float
    valuation_result: ResearchValuationResult


@dataclass(frozen=True)
class ResearchCombinationResult:
    status: ResearchValuationStatus
    reason_code: str | None = None
    baseline_value: float | None = None
    scenario_value: float | None = None
    value_change: float | None = None
    contributions: tuple[ResearchLegContribution, ...] = ()
    underlying_contract_ids: tuple[str, ...] = ()
    failed_leg_index: int | None = None
    failed_contract_id: str | None = None


def _unavailable(
    reason: str,
    *,
    index: int | None = None,
    contract_id: str | None = None,
) -> ResearchCombinationResult:
    """Fail atomically: never expose partial totals or partial contributions."""
    return ResearchCombinationResult(
        status=ResearchValuationStatus.UNAVAILABLE,
        reason_code=reason,
        failed_leg_index=index,
        failed_contract_id=contract_id,
    )


def _canonical_identifier(value: object) -> bool:
    return (
        isinstance(value, str)
        and bool(value)
        and value == value.strip()
        and not any(character.isspace() for character in value)
    )


class ResearchCombinationEngine:
    """Evaluate multiple legs without touching any live trading system."""

    @staticmethod
    def evaluate(request: ResearchCombinationRequest) -> ResearchCombinationResult:
        if (
            not isinstance(request, ResearchCombinationRequest)
            or not isinstance(request.legs, tuple)
            or len(request.legs) < 2
        ):
            return _unavailable("INVALID_INPUT")

        reference_asset: ResearchAssetClass | None = None
        reference_exchange: str | None = None
        reference_family: str | None = None
        reference_underlying: str | None = None

        seen_contracts: set[tuple[str, str]] = set()
        underlying_inputs: dict[tuple[str, str], tuple[float, float]] = {}
        ordered_underlyings: list[str] = []
        contributions: list[ResearchLegContribution] = []

        for index, leg in enumerate(request.legs):
            if not isinstance(leg, ResearchCombinationLeg):
                return _unavailable("INVALID_INPUT", index=index)
            if not isinstance(leg.valuation, ResearchValuationRequest):
                return _unavailable("INVALID_INPUT", index=index)
            option = leg.valuation
            contract_id = option.contract_id if isinstance(option.contract_id, str) else None
            if (
                not _canonical_identifier(leg.exchange)
                or not _canonical_identifier(leg.product_family_id)
                or not _canonical_identifier(option.contract_id)
                or not _canonical_identifier(option.underlying_contract_id)
            ):
                return _unavailable("INVALID_INPUT", index=index, contract_id=contract_id)

            try:
                asset_class = ResearchAssetClass(leg.asset_class)
            except (ValueError, TypeError):
                return _unavailable("INVALID_INPUT", index=index, contract_id=contract_id)

            if reference_asset is None:
                reference_asset = asset_class
                reference_exchange = leg.exchange
                reference_family = leg.product_family_id
                reference_underlying = option.underlying_contract_id
            elif (
                asset_class != reference_asset
                or leg.exchange != reference_exchange
                or leg.product_family_id != reference_family
                or (
                    asset_class is not ResearchAssetClass.FUTURES_OPTION
                    and option.underlying_contract_id != reference_underlying
                )
            ):
                return _unavailable(
                    "UNSUPPORTED_COMBINATION", index=index, contract_id=contract_id,
                )

            # A canonical product identity restricts the permitted pricing models.
            # This keeps an equity/ETF underlying from silently entering a Black-76
            # futures-option combination, without changing existing model backends.
            try:
                model = ResearchPricingModel(option.model_name)
            except (ValueError, TypeError):
                return _unavailable("UNSUPPORTED_MODEL", index=index, contract_id=contract_id)
            if (
                asset_class is ResearchAssetClass.FUTURES_OPTION
                and model not in (ResearchPricingModel.BLACK_76, ResearchPricingModel.BINOMIAL_TREE)
            ) or (
                asset_class is not ResearchAssetClass.FUTURES_OPTION
                and model is not ResearchPricingModel.BLACK_SCHOLES
            ):
                return _unavailable(
                    "UNSUPPORTED_COMBINATION", index=index, contract_id=contract_id,
                )

            option_key = (leg.exchange, option.contract_id)
            if option_key in seen_contracts:
                return _unavailable("DUPLICATE_LEG", index=index, contract_id=contract_id)
            seen_contracts.add(option_key)

            underlying_key = (leg.exchange, option.underlying_contract_id)
            ref_inputs = underlying_inputs.get(underlying_key)
            current_inputs = (option.underlying_price, option.underlying_shock)
            if ref_inputs is not None and ref_inputs != current_inputs:
                return _unavailable(
                    "UNDERLYING_INPUT_CONFLICT", index=index, contract_id=contract_id,
                )

            result = ResearchRiskEngine.evaluate(option)
            if result.status is not ResearchValuationStatus.OK or (
                result.baseline_value is None
                or result.scenario_value is None
                or result.value_change is None
            ):
                return _unavailable(
                    result.reason_code or "NUMERICAL_FAILURE",
                    index=index,
                    contract_id=contract_id,
                )

            if ref_inputs is None:
                underlying_inputs[underlying_key] = current_inputs
                ordered_underlyings.append(option.underlying_contract_id)
            contributions.append(
                ResearchLegContribution(
                    contract_id=option.contract_id,
                    underlying_contract_id=option.underlying_contract_id,
                    signed_quantity=option.signed_quantity,
                    contract_multiplier=option.contract_multiplier,
                    baseline_value=result.baseline_value,
                    scenario_value=result.scenario_value,
                    value_change=result.value_change,
                    valuation_result=result,
                )
            )

        try:
            baseline = fsum(item.baseline_value for item in contributions)
            scenario = fsum(item.scenario_value for item in contributions)
            change = fsum(item.value_change for item in contributions)
        except (OverflowError, ValueError):
            return _unavailable("NUMERICAL_FAILURE")
        if not all(isfinite(x) for x in (baseline, scenario, change)):
            return _unavailable("NUMERICAL_FAILURE")

        return ResearchCombinationResult(
            status=ResearchValuationStatus.OK,
            baseline_value=baseline,
            scenario_value=scenario,
            value_change=change,
            contributions=tuple(contributions),
            underlying_contract_ids=tuple(ordered_underlyings),
        )
