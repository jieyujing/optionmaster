"""M1-D05: one approved historical archive -> M0 research-only valuation.

The rate/time convention is explicit and narrow. This is not a fresh database
valuation, point-in-time backtest feed, or production authorization. All
archive admission is rerun from the independently approved packet and bytes.
"""
from collections.abc import Callable, Mapping
from dataclasses import dataclass, replace
from datetime import date, datetime, time, timezone
from hashlib import sha256
from html.parser import HTMLParser
from math import fsum, isfinite, log1p
from zoneinfo import ZoneInfo

from vnpy_optionmaster.research_combination import ResearchAssetClass, ResearchCombinationLeg
from vnpy_optionmaster.research_contract_terms import ExerciseStyle
from vnpy_optionmaster.research_data_basis import HistoricalBasis
from vnpy_optionmaster.research_grid import ResearchGridRequest, ResearchRiskGridEngine, ResearchRiskGridResult
from vnpy_optionmaster.research_history_repository import HistoricalReadRequest
from vnpy_optionmaster.research_pricing import ResearchPricingModel
from vnpy_optionmaster.research_risk import (
    ResearchRiskEngine,
    ResearchValuationRequest,
    ResearchValuationResult,
    ResearchValuationStatus,
)
from vnpy_optionmaster.research_scenario import ResearchScenarioLeg, ResearchScenarioRequest
from vnpy_optionmaster.research_time import ResearchTimeRequest
from vnpy_optionmaster.research_verified_slice import SliceApproval, SliceResult, SliceStatus, VerifiedHistoricalSlice, admit_verified_slice

_ZONE = ZoneInfo("Asia/Shanghai")
_RATE_URL = (
    "https://yield.chinabond.com.cn/cbweb-pbc-web/pbc/historyQuery?"
    "endDate=2022-09-07&gjqx=0.25&locale=en_US&qxId=hzsylqx&startDate=2022-09-06"
)
_RATE_NAME = "ChinaBond Government Bond Yield Curve"
_DAY = date(2022, 9, 7)
_RATE_DAY = date(2022, 9, 6)
_SECONDS_PER_YEAR = 365 * 86400
_MAX_RATE_BYTES = 512 * 1024
# D05 only exercises a small archived sample; Q17 large-grid performance
# remains separate. M0's own wider axis and numerical guards stay unchanged.
_MAX_CURVE = 11
_MAX_AXIS = 5


@dataclass(frozen=True)
class ArchivedValuationPolicy:
    """Trusted research configuration, not a live/production approval."""

    clock_policy: str
    day_count_basis: str
    rate_policy: str
    rate_date: date
    rate_source_uri: str
    rate_artifact_sha256: str
    tree_steps: int


@dataclass(frozen=True)
class ArchivedValuationRequest:
    selection: HistoricalReadRequest
    quantities: tuple[int, ...]
    scenario_at: datetime
    curve_price_shocks: tuple[float, ...]
    heatmap_price_shocks: tuple[float, ...]
    heatmap_iv_shocks: tuple[float, ...]


@dataclass(frozen=True)
class HistoricalRateProxy:
    """Observed 3M government YTM and declared conversion assumption.

    The quote is not asserted to be a zero-coupon rate at the option maturity.
    We approximate it as a simple rate over 0.25 years, convert to a flat
    continuously compounded rate and hold that constant in scenarios.
    """

    quote_date: date
    quoted_percent: float
    tenor_years: float
    continuous_rate: float
    source_uri: str
    artifact_sha256: str
    convention: str = "ASSUMED_SIMPLE_3M_TO_FLAT_CONTINUOUS_ACT365F"


@dataclass(frozen=True)
class ArchivedValuation:
    historical_slice: VerifiedHistoricalSlice
    policy: ArchivedValuationPolicy
    rate: HistoricalRateProxy
    reference_at: datetime
    nominal_expiry_at: datetime
    time_to_expiry: float
    model: ResearchPricingModel
    calibrations: tuple[ResearchValuationResult, ...]
    grid: ResearchRiskGridResult

    @property
    def production_valuation_ready(self) -> bool:
        return False

    @property
    def research_only(self) -> bool:
        return True


@dataclass(frozen=True)
class ArchivedValuationResult:
    status: ResearchValuationStatus
    reason_code: str | None = None
    valuation: ArchivedValuation | None = None


class _Rejected(Exception):
    pass


def _require(condition: bool, reason: str) -> None:
    if not condition:
        raise _Rejected(reason)


class _TableRows(HTMLParser):
    """Read actual cells, including the maturity column header."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.rows: list[tuple[str, ...]] = []
        self.row: list[str] | None = None
        self.cell: list[str] | None = None

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag == "tr":
            self.row = []
        elif tag in ("td", "th") and self.row is not None:
            self.cell = []

    def handle_data(self, data: str) -> None:
        if self.cell is not None:
            self.cell.append(data)

    def handle_endtag(self, tag: str) -> None:
        if tag in ("td", "th") and self.cell is not None and self.row is not None:
            self.row.append(" ".join("".join(self.cell).split()))
            self.cell = None
        elif tag == "tr" and self.row is not None:
            self.rows.append(tuple(self.row))
            self.row = None


def _rate(policy: ArchivedValuationPolicy, body: bytes) -> HistoricalRateProxy:
    _require(type(body) is bytes and 0 < len(body) <= _MAX_RATE_BYTES, "RATE_EVIDENCE_UNAVAILABLE")
    digest = sha256(body).hexdigest()
    _require(digest == policy.rate_artifact_sha256, "RATE_EVIDENCE_DIGEST_MISMATCH")
    parser = _TableRows()
    parser.feed(body.decode("utf-8"))
    parser.close()
    header = ("Yield Curve Name", "Date", "3M", "6M", "1Y", "3Y", "5Y", "7Y", "10Y", "30Y")
    _require(parser.rows.count(header) == 1, "RATE_EVIDENCE_SCHEMA_MISMATCH")
    rows = [
        row for row in parser.rows
        if len(row) == len(header) and row[:2] == (_RATE_NAME, policy.rate_date.isoformat())
    ]
    _require(len(rows) == 1, "RATE_DATE_UNAVAILABLE")
    try:
        quoted = float(rows[0][2])
        _require(isfinite(quoted) and 0 < quoted < 100, "INVALID_RATE")
        continuous = log1p(quoted / 100 * 0.25) / 0.25
    except ValueError:
        raise _Rejected("INVALID_RATE") from None
    return HistoricalRateProxy(policy.rate_date, quoted, 0.25, continuous, policy.rate_source_uri, digest)


def _axis(values: tuple[float, ...], cap: int) -> None:
    _require(isinstance(values, tuple) and bool(values), "INVALID_AXES")
    _require(len(values) <= cap, "GRID_LIMIT_EXCEEDED")
    _require(
        all(not isinstance(v, bool) and isinstance(v, (int, float)) and isfinite(v) for v in values)
        and len(set(values)) == len(values) and 0.0 in values,
        "INVALID_AXES",
    )


def evaluate_archived_valuation(
    request: ArchivedValuationRequest,
    packet: bytes,
    evidence: Mapping[str, bytes],
    approval: SliceApproval | None,
    rate_artifact: bytes,
    policy: ArchivedValuationPolicy | None,
    read_at: datetime,
) -> ArchivedValuationResult:
    """Re-admit exact archive, solve IV once per leg, and reuse M0 grid.

    No partial portfolio is returned if a baseline leg fails. M0's valid
    independent scenario cells may coexist with unavailable cells.
    """
    try:
        return _evaluate(request, packet, evidence, approval, rate_artifact, policy, read_at)
    except _Rejected as error:
        return ArchivedValuationResult(ResearchValuationStatus.UNAVAILABLE, str(error))
    except (TypeError, ValueError, OverflowError, AttributeError, KeyError, IndexError, RecursionError):
        return ArchivedValuationResult(ResearchValuationStatus.UNAVAILABLE, "INVALID_INPUT")


def _evaluate(
    request: ArchivedValuationRequest, packet: bytes, evidence: Mapping[str, bytes],
    approval: SliceApproval | None, rate_artifact: bytes,
    policy: ArchivedValuationPolicy | None, read_at: datetime,
    *, admission_fn: Callable[[HistoricalReadRequest, bytes, Mapping[str, bytes], SliceApproval | None, datetime], SliceResult] = admit_verified_slice,
    max_legs: int = 2,
    require_positive_extrinsic: bool = False,
) -> ArchivedValuationResult:
    if not isinstance(request, ArchivedValuationRequest) or not isinstance(policy, ArchivedValuationPolicy):
        raise _Rejected("INVALID_INPUT")
    _require(
        policy.clock_policy == "EOD_NOMINAL_1500_DATE_ONLY" and policy.day_count_basis == "ACT/365F"
        and policy.rate_policy == "PRIOR_DAY_CGB_3M_YTM_FLAT_SIMPLE_PROXY"
        and policy.rate_source_uri == _RATE_URL,
        "UNSUPPORTED_VALUATION_POLICY",
    )
    _require(type(policy.rate_date) is date and policy.rate_date == _RATE_DAY, "RATE_DATE_UNAVAILABLE")
    _require(
        isinstance(request.selection, HistoricalReadRequest) and type(request.selection.trade_date) is date
        and request.selection.trade_date == _DAY and isinstance(request.quantities, tuple)
        and 2 <= len(request.quantities) == len(request.selection.legs) <= max_legs
        and all(type(q) is int and q != 0 for q in request.quantities)
        and type(policy.tree_steps) is int and 2 <= policy.tree_steps <= 512,
        "INVALID_INPUT",
    )
    _axis(request.curve_price_shocks, _MAX_CURVE)
    _axis(request.heatmap_price_shocks, _MAX_AXIS)
    _axis(request.heatmap_iv_shocks, _MAX_AXIS)
    reference = datetime.combine(request.selection.trade_date, time(15), tzinfo=_ZONE)
    # A nominal pricing clock, NOT actual settlement publication time.
    _require(
        isinstance(request.scenario_at, datetime) and request.scenario_at.tzinfo == _ZONE
        and request.scenario_at >= reference,
        "INVALID_SCENARIO_TIME",
    )
    rate = _rate(policy, rate_artifact)
    admission = admission_fn(request.selection, packet, evidence, approval, read_at)
    if admission.status is not SliceStatus.ADMITTED_REFERENCE or admission.slice is None:
        raise _Rejected(admission.reason_code or "ARCHIVE_UNAVAILABLE")
    archive = admission.slice
    _require(archive.basis.price_basis is HistoricalBasis.SETTLEMENT, "UNSUPPORTED_PRICE_BASIS")
    _require(len(archive.legs) == len(archive.basis.legs) == len(request.quantities), "ARCHIVE_SCOPE_MISMATCH")
    expires = {leg.contract.terms.expiry_date for leg in archive.legs}
    _require(len(expires) == 1, "ARCHIVE_SCOPE_MISMATCH")
    expiry = next(iter(expires))
    _require(reference.date() < expiry, "EXPIRED_BASELINE")
    # The date-only guard is intentionally stricter than the known 15:30
    # exercise submission deadline; the whole expiry date is out of scope.
    _require(request.scenario_at.date() < expiry, "EXPIRED_SCENARIO")
    nominal = datetime.combine(expiry, time(15), tzinfo=_ZONE)
    maturity = (nominal.astimezone(timezone.utc) - reference.astimezone(timezone.utc)).total_seconds() / _SECONDS_PER_YEAR
    _require(maturity > 0, "EXPIRED_BASELINE")
    model = ResearchPricingModel.BINOMIAL_TREE
    scenarios: list[ResearchScenarioLeg] = []
    calibrations: list[ResearchValuationResult] = []
    expected_values: list[float] = []
    for source, marks, quantity in zip(archive.legs, archive.basis.legs, request.quantities, strict=True):
        verified = source.contract.terms
        identity = verified.underlying
        _require(
            identity.asset_class is ResearchAssetClass.FUTURES_OPTION
            and verified.exercise_style is ExerciseStyle.AMERICAN
            and verified.option_type in ("CALL", "PUT"),
            "UNSUPPORTED_MODEL",
        )
        _require(
            marks.option_symbol == verified.option_key.symbol
            and marks.option_exchange == verified.option_key.exchange
            and marks.product_family_id == identity.product_family_id
            and marks.asset_class is identity.asset_class
            and marks.underlying_contract_id == source.contract.selection.underlying_contract_id
            and marks.underlying_quote_symbol == archive.underlying_quote.symbol,
            "ARCHIVE_SCOPE_MISMATCH",
        )
        if require_positive_extrinsic:
            direction = 1 if verified.option_type == "CALL" else -1
            intrinsic = max(0.0, direction * (marks.underlying_price - verified.strike))
            _require(marks.option_price > intrinsic, "IV_UNAVAILABLE")
        native_underlying = identity.exchange + "." + identity.contract_code
        valuation = ResearchValuationRequest(
            contract_id=verified.option_contract_code, underlying_contract_id=native_underlying,
            underlying_price=marks.underlying_price, strike=verified.strike,
            risk_free_rate=rate.continuous_rate, time_to_expiry=maturity,
            implied_volatility=None, option_type=1 if verified.option_type == "CALL" else -1,
            signed_quantity=quantity, contract_multiplier=verified.contract_size,
            model_name=model, reference_option_price=marks.option_price, tree_steps=policy.tree_steps,
        )
        calibrated = ResearchRiskEngine.evaluate(valuation)
        if (
            calibrated.status is not ResearchValuationStatus.OK
            or calibrated.implied_volatility is None or calibrated.iv_result is None
            or not calibrated.iv_result.valid or calibrated.baseline_value is None
        ):
            raise _Rejected(calibrated.reason_code or "IV_UNAVAILABLE")
        expected = quantity * verified.contract_size * marks.option_price
        # Use the M0 solver price tolerance in monetary position units.
        tolerance = abs(quantity * verified.contract_size) * max(1e-8, marks.option_price * 1e-7)
        _require(abs(calibrated.baseline_value - expected) <= tolerance, "IV_REPRICE_CONFLICT")
        expected_values.append(calibrated.baseline_value)
        calibrations.append(calibrated)
        frozen = replace(valuation, implied_volatility=calibrated.implied_volatility, reference_option_price=None)
        scenarios.append(ResearchScenarioLeg(
            ResearchCombinationLeg(frozen, verified.option_exchange, identity.product_family_id, identity.asset_class),
            identity.exchange + "|" + identity.product_family_id + "|" + expiry.isoformat(),
        ))
    expiry_id = scenarios[0].expiry_id
    _require(all(leg.expiry_id == expiry_id for leg in scenarios), "ARCHIVE_SCOPE_MISMATCH")
    timed = ResearchTimeRequest(
        ResearchScenarioRequest(tuple(scenarios)), reference, request.scenario_at,
        ((expiry_id, expiry),), policy.day_count_basis,
    )
    grid = ResearchRiskGridEngine.evaluate(ResearchGridRequest(
        timed, request.curve_price_shocks, request.heatmap_price_shocks, request.heatmap_iv_shocks,
    ))
    if grid.status is not ResearchValuationStatus.OK or grid.baseline_value is None:
        raise _Rejected(grid.reason_code or "GRID_UNAVAILABLE")
    _require(abs(grid.baseline_value - fsum(expected_values)) <= 1e-8, "BASELINE_DRIFT")
    return ArchivedValuationResult(ResearchValuationStatus.OK, valuation=ArchivedValuation(
        archive, policy, rate, reference, nominal, maturity, model, tuple(calibrations), grid,
    ))
