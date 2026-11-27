"""Archived multi-expiry option research from independently pinned D09 data.

Same-day complete settlement inputs, per-contract underlying and maturity,
one dated rate proxy, locked reference IVs, and full scenario repricing.
No database, network, trading, expiry settlement or production approval.
"""
from dataclasses import dataclass, replace
from datetime import date, datetime, time, timezone
from math import fsum
from pathlib import Path
from zoneinfo import ZoneInfo

from vnpy_optionmaster.research_archived_valuation import (
    ArchivedValuationPolicy, HistoricalRateProxy, _Rejected, _RATE_URL,
    _axis, _rate, _require,
)
from vnpy_optionmaster.research_combination import ResearchAssetClass, ResearchCombinationLeg
from vnpy_optionmaster.research_contract_terms import ExerciseStyle
from vnpy_optionmaster.research_data_basis import HistoricalBasis
from vnpy_optionmaster.research_grid import ResearchGridRequest, ResearchRiskGridEngine, ResearchRiskGridResult
from vnpy_optionmaster.research_multi_chain_archive import (
    CatalogChainSource, CatalogSelection, VerifiedCatalogPortfolio, admit_catalog_selection, _read,
)
from vnpy_optionmaster.research_pricing import ResearchPricingModel
from vnpy_optionmaster.research_risk import (
    ResearchGreeks, ResearchRiskEngine, ResearchValuationRequest,
    ResearchValuationResult, ResearchValuationStatus,
)
from vnpy_optionmaster.research_scenario import ResearchScenarioLeg, ResearchScenarioRequest
from vnpy_optionmaster.research_time import ResearchTimeRequest
from vnpy_optionmaster.research_verified_slice import SliceApproval, SliceStatus, _document, _hash

_ZONE = ZoneInfo("Asia/Shanghai")
_SECONDS_PER_YEAR = 365 * 86400
_POLICY_PIN = "4c2ef1ce4173a385168189b74b7bbf1361e35276b3c34056a675f107e26e2aa9"
_RATE_PIN = "8dabc429bb9cccbcc479813ae7743813602e1a6d5c67c8e8b40ea12b5971b0f1"
# Both are actual preceding trading dates in the explicit pilot evidence.
# Do not assume "previous calendar day" for new/holiday dates.
_RATE_DAYS = {
    date(2022, 9, 7): date(2022, 9, 6),
    date(2022, 9, 8): date(2022, 9, 7),
}


def load_pilot_rate(root: Path, day: date) -> tuple[ArchivedValuationPolicy, bytes]:
    """Trusted local configuration; dated 3M proxy from the unchanged D05 artifact."""
    if type(day) is not date or day not in _RATE_DAYS:
        raise ValueError("RATE_DATE_UNAVAILABLE")
    d05 = root / "m1-d05/cu2211-20220907"
    raw = _read(d05 / "research_valuation_policy.json", 65536)
    if _hash(raw) != _POLICY_PIN:
        raise ValueError("RATE_POLICY_DIGEST_MISMATCH")
    p = _document(raw)
    if p["rate_artifact"] != "chinabond_3m_20220906_07.html" or p["rate_artifact_sha256"] != _RATE_PIN:
        raise ValueError("RATE_POLICY_DIGEST_MISMATCH")
    body = _read(d05 / p["rate_artifact"], 524288)
    if _hash(body) != _RATE_PIN:
        raise ValueError("RATE_EVIDENCE_DIGEST_MISMATCH")
    return ArchivedValuationPolicy(
        p["clock_policy"], p["day_count_basis"], p["rate_policy"], _RATE_DAYS[day],
        p["rate_source_uri"], p["rate_artifact_sha256"], p["tree_steps"],
    ), body


@dataclass(frozen=True)
class CatalogValuationRequest:
    selection: CatalogSelection
    quantities: tuple[int, ...]
    scenario_at: datetime
    curve_price_shocks: tuple[float, ...]
    heatmap_price_shocks: tuple[float, ...]
    heatmap_iv_shocks: tuple[float, ...]
    parallel_price_shock: float = 0.0
    parallel_iv_shock: float = 0.0
    price_shocks_by_underlying: tuple[tuple[str, float], ...] = ()
    iv_shocks_by_expiry: tuple[tuple[str, float], ...] = ()


@dataclass(frozen=True)
class CatalogExpiry:
    expiry_id: str
    underlying_id: str
    expiry_date: date
    nominal_expiry_at: datetime
    baseline_time_to_expiry: float


@dataclass(frozen=True)
class CatalogExposure:
    underlying_id: str
    baseline_greeks: ResearchGreeks
    scenario_greeks: ResearchGreeks


@dataclass(frozen=True)
class CatalogValuation:
    portfolio: VerifiedCatalogPortfolio
    policy: ArchivedValuationPolicy
    rate: HistoricalRateProxy
    reference_at: datetime
    scenario_at: datetime
    expiries: tuple[CatalogExpiry, ...]
    model: ResearchPricingModel
    calibrations: tuple[ResearchValuationResult, ...]
    exposures: tuple[CatalogExposure, ...]
    grid: ResearchRiskGridResult

    @property
    def production_valuation_ready(self) -> bool:
        return False


@dataclass(frozen=True)
class CatalogValuationResult:
    status: ResearchValuationStatus
    reason_code: str | None = None
    valuation: CatalogValuation | None = None


def _sum_greeks(items: list[ResearchGreeks]) -> ResearchGreeks:
    return ResearchGreeks(
        fsum(x.delta for x in items), fsum(x.gamma for x in items),
        fsum(x.theta for x in items), fsum(x.vega for x in items),
    )


def evaluate_catalog_valuation(
    request: CatalogValuationRequest,
    packet: bytes,
    sources: tuple[CatalogChainSource, ...],
    approval: SliceApproval | None,
    rate_artifact: bytes,
    policy: ArchivedValuationPolicy,
    read_at: datetime,
) -> CatalogValuationResult:
    """Re-admit exact selected data before any model calculation."""
    try:
        return _evaluate(request, packet, sources, approval, rate_artifact, policy, read_at)
    except _Rejected as exc:
        return CatalogValuationResult(ResearchValuationStatus.UNAVAILABLE, str(exc))
    except (ValueError, TypeError, AttributeError, KeyError, IndexError, OverflowError, RecursionError):
        return CatalogValuationResult(ResearchValuationStatus.UNAVAILABLE, "INVALID_INPUT")


def _evaluate(
    request: CatalogValuationRequest, packet: bytes, sources: tuple[CatalogChainSource, ...],
    approval: SliceApproval | None, rate_artifact: bytes, policy: ArchivedValuationPolicy, read_at: datetime,
) -> CatalogValuationResult:
    _require(isinstance(request, CatalogValuationRequest) and isinstance(request.selection, CatalogSelection)
             and isinstance(policy, ArchivedValuationPolicy), "INVALID_INPUT")
    day = request.selection.trade_date
    _require(type(day) is date and day in _RATE_DAYS and isinstance(request.quantities, tuple)
             and 2 <= len(request.quantities) == len(request.selection.symbols) <= 8
             and all(type(q) is int and 0 < abs(q) <= 1000 for q in request.quantities), "INVALID_INPUT")
    _require(policy.clock_policy == "EOD_NOMINAL_1500_DATE_ONLY" and policy.day_count_basis == "ACT/365F"
             and policy.rate_policy == "PRIOR_DAY_CGB_3M_YTM_FLAT_SIMPLE_PROXY"
             and policy.rate_source_uri == _RATE_URL and policy.rate_artifact_sha256 == _RATE_PIN
             and type(policy.tree_steps) is int and 2 <= policy.tree_steps <= 512, "UNSUPPORTED_VALUATION_POLICY")
    _require(type(policy.rate_date) is date and policy.rate_date == _RATE_DAYS[day], "RATE_DATE_UNAVAILABLE")
    _axis(request.curve_price_shocks, 121)
    _axis(request.heatmap_price_shocks, 31)
    _axis(request.heatmap_iv_shocks, 31)
    reference = datetime.combine(day, time(15), tzinfo=_ZONE)
    _require(isinstance(request.scenario_at, datetime) and request.scenario_at.tzinfo == _ZONE
             and request.scenario_at >= reference, "INVALID_SCENARIO_TIME")
    admitted = admit_catalog_selection(request.selection, packet, sources, approval, read_at)
    if admitted.status is not SliceStatus.ADMITTED_REFERENCE or admitted.portfolio is None:
        raise _Rejected(admitted.reason_code or "ARCHIVE_UNAVAILABLE")
    portfolio = admitted.portfolio
    _require(portfolio.basis.price_basis is HistoricalBasis.SETTLEMENT
             and len(portfolio.legs) == len(portfolio.basis.legs) == len(request.quantities), "BASIS_UNAVAILABLE")
    # Fail the whole portfolio BEFORE calibrating if the earliest selected
    # expiry would be crossed. Date-only is not a verified exercise cutoff.
    _require(all(day < leg.contract.terms.expiry_date for leg in portfolio.legs), "EXPIRED_BASELINE")
    _require(all(request.scenario_at.date() < leg.contract.terms.expiry_date for leg in portfolio.legs), "EXPIRED_SCENARIO")
    rate = _rate(policy, rate_artifact)
    model = ResearchPricingModel.BINOMIAL_TREE
    expiries: dict[str, CatalogExpiry] = {}
    scenarios: list[ResearchScenarioLeg] = []
    calibrations: list[ResearchValuationResult] = []
    expected_values: list[float] = []
    for leg, mark, quantity in zip(portfolio.legs, portfolio.basis.legs, request.quantities, strict=True):
        t = leg.contract.terms
        u = t.underlying
        _require(u.asset_class is ResearchAssetClass.FUTURES_OPTION and t.exercise_style is ExerciseStyle.AMERICAN
                 and t.option_type in ("CALL", "PUT"), "UNSUPPORTED_MODEL")
        _require(mark.option_symbol == t.option_key.symbol and mark.option_exchange == t.option_key.exchange
                 and mark.product_family_id == u.product_family_id and mark.asset_class is u.asset_class
                 and mark.underlying_contract_id == leg.contract.selection.underlying_contract_id,
                 "ARCHIVE_SCOPE_MISMATCH")
        underlying = u.exchange + "." + u.contract_code
        expiry_id = u.exchange + "|" + u.contract_code + "|" + t.expiry_date.isoformat()
        nominal = datetime.combine(t.expiry_date, time(15), tzinfo=_ZONE)
        maturity = (nominal.astimezone(timezone.utc) - reference.astimezone(timezone.utc)).total_seconds() / _SECONDS_PER_YEAR
        expiry = CatalogExpiry(expiry_id, underlying, t.expiry_date, nominal, maturity)
        _require(expiries.setdefault(expiry_id, expiry) == expiry and maturity > 0, "EXPIRY_METADATA_CONFLICT")
        direction = 1 if t.option_type == "CALL" else -1
        _require(mark.option_price > max(0.0, direction * (mark.underlying_price - t.strike)), "IV_UNAVAILABLE")
        valuation = ResearchValuationRequest(
            contract_id=t.option_contract_code, underlying_contract_id=underlying,
            underlying_price=mark.underlying_price, strike=t.strike, risk_free_rate=rate.continuous_rate,
            time_to_expiry=maturity, implied_volatility=None, option_type=direction,
            signed_quantity=quantity, contract_multiplier=t.contract_size, model_name=model,
            reference_option_price=mark.option_price, tree_steps=policy.tree_steps,
        )
        calibrated = ResearchRiskEngine.evaluate(valuation)
        if (calibrated.status is not ResearchValuationStatus.OK or calibrated.implied_volatility is None
                or calibrated.iv_result is None or not calibrated.iv_result.valid or calibrated.baseline_value is None):
            raise _Rejected(calibrated.reason_code or "IV_UNAVAILABLE")
        expected = quantity * t.contract_size * mark.option_price
        tolerance = abs(quantity * t.contract_size) * max(1e-8, mark.option_price * 1e-7)
        _require(abs(calibrated.baseline_value - expected) <= tolerance, "IV_REPRICE_CONFLICT")
        expected_values.append(calibrated.baseline_value)
        calibrations.append(calibrated)
        locked = replace(valuation, implied_volatility=calibrated.implied_volatility, reference_option_price=None)
        scenarios.append(ResearchScenarioLeg(
            ResearchCombinationLeg(locked, t.option_exchange, u.product_family_id, u.asset_class), expiry_id,
        ))
    scenario = ResearchScenarioRequest(
        tuple(scenarios), parallel_price_shock=request.parallel_price_shock,
        price_shocks_by_underlying=request.price_shocks_by_underlying,
        parallel_iv_shock=request.parallel_iv_shock, iv_shocks_by_expiry=request.iv_shocks_by_expiry,
    )
    timed = ResearchTimeRequest(
        scenario, reference, request.scenario_at,
        tuple((e.expiry_id, e.expiry_date) for e in expiries.values()), policy.day_count_basis,
    )
    grid = ResearchRiskGridEngine.evaluate(ResearchGridRequest(
        timed, request.curve_price_shocks, request.heatmap_price_shocks, request.heatmap_iv_shocks,
    ))
    if grid.status is not ResearchValuationStatus.OK or grid.baseline_value is None:
        raise _Rejected(grid.reason_code or "GRID_UNAVAILABLE")
    _require(abs(grid.baseline_value - fsum(expected_values)) <= 1e-8, "BASELINE_DRIFT")
    exposures = []
    for key in sorted({c.underlying_contract_id for c in grid.configured_contributions}):
        bases: list[ResearchGreeks] = []
        shocked: list[ResearchGreeks] = []
        for c in grid.configured_contributions:
            if c.underlying_contract_id == key:
                b, v = c.baseline_result.baseline_greeks, c.scenario_result.scenario_greeks
                if b is None or v is None:
                    raise _Rejected("NUMERICAL_FAILURE")
                bases.append(b)
                shocked.append(v)
        exposures.append(CatalogExposure(key, _sum_greeks(bases), _sum_greeks(shocked)))
    return CatalogValuationResult(ResearchValuationStatus.OK, valuation=CatalogValuation(
        portfolio, policy, rate, reference, request.scenario_at,
        tuple(sorted(expiries.values(), key=lambda e: (e.expiry_date, e.underlying_id))),
        model, tuple(calibrations), tuple(exposures), grid,
    ))
