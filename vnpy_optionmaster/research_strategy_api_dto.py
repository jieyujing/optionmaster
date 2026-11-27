"""M1-D12 additive, bounded strategy wire contract; no provider or trading access.

Pydantic is authoritative for structural AND cross-field validation. OpenAPI
and generated client validators are structural guards; the server must repeat
validation and establish source admission independently.
"""
from datetime import date, datetime
from math import isclose
from typing import Annotated, Any, Literal

from pydantic import Field, StringConstraints, field_validator, model_validator

from vnpy_optionmaster.research_api_dto import Number, Shock, Symbol, ValuationRequestDTO, WireModel

BASE_PATH = "/api/v4/research/strategy"
MAX_OPTIONS = 8
MAX_UNDERLYINGS = 8
MAX_QUANTITY = 1000
MAX_MODEL_POINTS = 11
MAX_PAYOFF_POINTS = 40
MAX_CHAIN_ROWS = 64
MAX_COVERAGE_ITEMS = 32
MAX_BODY_BYTES = 16384
MAX_RESPONSE_BYTES = 524288

Digest = Annotated[str, StringConstraints(pattern=r"^[0-9a-f]{64}$")]
Revision = Annotated[int, Field(ge=0, le=9007199254740991)]
Quantity = Annotated[int, Field(ge=-MAX_QUANTITY, le=MAX_QUANTITY, json_schema_extra={"not": {"const": 0}})]
Premium = Annotated[float, Field(ge=0, allow_inf_nan=False)]
Positive = Annotated[float, Field(gt=0, allow_inf_nan=False)]
Day = Annotated[str, StringConstraints(pattern=r"^\d{4}-\d{2}-\d{2}$"), Field(json_schema_extra={"format": "date"})]
MarketClock = Annotated[str, StringConstraints(
    pattern=r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(\.\d{1,6})?\+08:00$",
    min_length=25, max_length=32)]
Clock = Annotated[str, StringConstraints(min_length=20, max_length=40), Field(json_schema_extra={"format": "date-time"})]
Reason = Literal[
    "UNAUTHORIZED", "NOT_FOUND", "METHOD_NOT_ALLOWED", "INVALID_REQUEST", "INVALID_JSON",
    "PAYLOAD_TOO_LARGE", "UNSUPPORTED_MEDIA_TYPE", "BUSY", "INTERNAL_ERROR", "DEADLINE_EXCEEDED",
    "SCOPE_UNAVAILABLE", "SNAPSHOT_STALE", "CANDIDATE_ONLY", "SOURCE_INCOMPLETE",
    "SOURCE_AMBIGUOUS", "SOURCE_VERSION_UNVERIFIED", "TERMS_UNAVAILABLE", "MARK_UNAVAILABLE",
    "ENTRY_UNAVAILABLE", "ENTRY_DATE_INVALID", "INSTRUMENT_UNAVAILABLE", "IV_UNAVAILABLE",
    "MODEL_UNAVAILABLE", "RATE_UNAVAILABLE", "HORIZON_UNAVAILABLE", "EXPIRED_SCENARIO",
    "INVALID_SCENARIO", "DOMAIN_UNAVAILABLE", "NUMERICAL_FAILURE", "CONDITIONAL_ONLY",
    "NOT_EVALUATED", "INCOMPATIBLE_LEGACY",
]
Status = Literal["OK", "PARTIAL", "UNAVAILABLE"]
Unit = Literal["CONTRACTS", "NATIVE_SPOT_UNITS"]
Origin = Literal["ADMITTED_MARK", "USER_EDITED"]


class EntryDTO(WireModel):
    quantity: Quantity
    entry_price: Number
    entry_trade_date: Day
    entry_snapshot_id: Digest
    entry_origin: Origin

    @field_validator("quantity")
    @classmethod
    def nonzero(cls, value: int) -> int:
        if not value:
            raise ValueError("Position quantity must be nonzero")
        return value

    @field_validator("entry_trade_date")
    @classmethod
    def valid_day(cls, value: str) -> str:
        if date.fromisoformat(value).isoformat() != value:
            raise ValueError("Invalid entry trade date")
        return value


class StrategyOptionDTO(EntryDTO):
    contract_id: Symbol
    entry_price: Premium


class StrategyUnderlyingDTO(EntryDTO):
    underlying_id: Symbol
    quantity_unit: Unit


class StrategyPayoffRequestDTO(WireModel):
    schema_version: Literal["4.0"]
    portfolio_revision: Revision
    snapshot_id: Digest
    options: Annotated[list[StrategyOptionDTO], Field(min_length=1, max_length=MAX_OPTIONS)]
    underlyings: Annotated[list[StrategyUnderlyingDTO], Field(max_length=MAX_UNDERLYINGS)]
    reference_underlying_id: Symbol
    curve_price_returns: Annotated[list[Shock], Field(min_length=1, max_length=MAX_MODEL_POINTS)]
    global_price_shock: Shock
    price_shocks_by_underlying: Annotated[dict[Symbol, Shock], Field(max_length=MAX_UNDERLYINGS)]

    @field_validator("curve_price_returns")
    @classmethod
    def axis(cls, value: list[float]) -> list[float]:
        ValuationRequestDTO.axis(value)
        if value != sorted(value):
            raise ValueError("Axis must be strictly increasing")
        return value

    @model_validator(mode="after")
    def unique_positions(self) -> "StrategyPayoffRequestDTO":
        option_ids = [leg.contract_id for leg in self.options]
        underlying_ids = [leg.underlying_id for leg in self.underlyings]
        if len(set(option_ids)) != len(option_ids) or len(set(underlying_ids)) != len(underlying_ids):
            raise ValueError("Duplicate exact position")
        if set(option_ids) & set(underlying_ids):
            raise ValueError("Instrument identity collision")
        return self


class StrategyValuationRequestDTO(StrategyPayoffRequestDTO):
    scenario_at: MarketClock
    global_iv_shock: Shock
    iv_shocks_by_expiry: Annotated[dict[Symbol, Shock], Field(max_length=MAX_OPTIONS)]

    @field_validator("scenario_at")
    @classmethod
    def market_clock(cls, value: str) -> str:
        return ValuationRequestDTO.market_clock(value)


class CapabilityDTO(WireModel):
    status: Literal["ELIGIBLE", "UNAVAILABLE", "NOT_EVALUATED"]
    reason: Reason | None

    @model_validator(mode="after")
    def reason_matches(self) -> "CapabilityDTO":
        if (self.status == "ELIGIBLE") != (self.reason is None):
            raise ValueError("Capability reason mismatch")
        return self


class SnapshotDTO(WireModel):
    snapshot_id: Digest
    provider: Literal["APPROVED_ARCHIVE", "ADMITTED_CLICKHOUSE", "SYNTHETIC_FIXTURE"]
    exchange: Symbol
    product: Symbol
    trade_date: Day
    observed_at: Clock
    read_at: Clock
    admitted_at: Clock
    reference_at: MarketClock
    source_pin: Digest
    source_versions: Annotated[dict[Symbol, Digest], Field(min_length=1, max_length=64)]
    price_basis: Literal["SETTLEMENT", "APPROVED_CLOSE"]
    basis_fallback_reason: Symbol | None
    currency: Literal["CNY"]
    production_valuation_ready: Literal[False]

    @field_validator("observed_at", "read_at", "admitted_at")
    @classmethod
    def aware_clock(cls, value: str) -> str:
        if datetime.fromisoformat(value).utcoffset() is None:
            raise ValueError("Source clock must be aware")
        return value

    @model_validator(mode="after")
    def basis(self) -> "SnapshotDTO":
        if date.fromisoformat(self.trade_date).isoformat() != self.trade_date:
            raise ValueError("Invalid snapshot trade date")
        ValuationRequestDTO.market_clock(self.reference_at)
        if self.reference_at[:10] != self.trade_date:
            raise ValueError("Reference clock not on snapshot trade date")
        if not (datetime.fromisoformat(self.observed_at) <= datetime.fromisoformat(self.admitted_at) <= datetime.fromisoformat(self.read_at)):
            raise ValueError("Source clock ordering invalid")
        if (self.price_basis == "SETTLEMENT") != (self.basis_fallback_reason is None):
            raise ValueError("Atomic basis and fallback provenance required")
        return self


class DomainDTO(WireModel):
    status: Literal["VERIFIED", "UNAVAILABLE"]
    min_price: Number | None
    max_price: Number | None
    min_inclusive: bool
    max_inclusive: bool

    @model_validator(mode="after")
    def bounds(self) -> "DomainDTO":
        if self.status == "UNAVAILABLE" and (self.min_price is not None or self.max_price is not None):
            raise ValueError("Unknown domain cannot declare endpoints")
        if self.min_price is not None and self.max_price is not None and self.min_price >= self.max_price:
            raise ValueError("Empty price domain")
        if (self.min_price is None and self.min_inclusive) or (self.max_price is None and self.max_inclusive):
            raise ValueError("Unbounded endpoints cannot be included")
        return self


class UnderlyingQuoteDTO(WireModel):
    underlying_id: Symbol
    kind: Literal["FUTURE", "SPOT", "INDEX", "ETF"]
    delivery_month: Symbol | None
    market_price: Number | None
    multiplier: Positive
    quantity_unit: Unit
    quote_unit: Symbol
    terms_version: Symbol
    domain: DomainDTO
    eligibility: CapabilityDTO

    @model_validator(mode="after")
    def mark(self) -> "UnderlyingQuoteDTO":
        if self.eligibility.status == "ELIGIBLE" and self.market_price is None:
            raise ValueError("Eligible underlying requires a mark")
        if self.kind == "FUTURE" and (self.delivery_month is None or self.quantity_unit != "CONTRACTS"):
            raise ValueError("Future needs exact delivery and contract unit")
        if self.kind != "FUTURE" and (self.delivery_month is not None or self.quantity_unit != "NATIVE_SPOT_UNITS"):
            raise ValueError("Spot identity needs native units")
        return self


class OptionQuoteDTO(WireModel):
    contract_id: Symbol
    underlying_id: Symbol
    option_type: Literal["CALL", "PUT"]
    strike: Premium
    expiry_id: Symbol
    expiry_date: Day
    expiry_at: MarketClock | None
    expiry_clock: Literal["VERIFIED_CUTOFF", "APPROVED_NOMINAL", "DATE_ONLY"]
    exercise_style: Literal["AMERICAN", "EUROPEAN"]
    model: Literal["BLACK_76", "BLACK_SCHOLES", "AMERICAN_BINOMIAL"]
    multiplier: Positive
    quote_unit: Symbol
    terms_version: Symbol
    market_price: Premium | None
    implied_volatility: Positive | None
    payoff_eligibility: CapabilityDTO
    model_eligibility: CapabilityDTO

    @model_validator(mode="after")
    def capabilities(self) -> "OptionQuoteDTO":
        if (self.expiry_clock == "DATE_ONLY") != (self.expiry_at is None):
            raise ValueError("Expiry clock evidence inconsistent")
        if self.payoff_eligibility.status == "ELIGIBLE" and self.market_price is None:
            raise ValueError("Payoff eligible mark missing")
        if self.model_eligibility.status == "ELIGIBLE" and (
            self.payoff_eligibility.status != "ELIGIBLE" or self.implied_volatility is None
        ):
            raise ValueError("Model eligibility needs admitted mark and IV")
        return self


class ChainRowDTO(WireModel):
    strike: Premium
    call: OptionQuoteDTO | None
    put: OptionQuoteDTO | None

    @model_validator(mode="after")
    def match_strike(self) -> "ChainRowDTO":
        if self.call is None and self.put is None:
            raise ValueError("Empty chain row")
        for right, option in (("CALL", self.call), ("PUT", self.put)):
            if option is not None and (option.option_type != right or option.strike != self.strike):
                raise ValueError("Chain side identity mismatch")
        return self


class CoverageMonthDTO(WireModel):
    underlying_id: Symbol
    delivery_month: Symbol | None
    discovered_contracts: Annotated[int, Field(ge=0)]
    admitted_contracts: Annotated[int, Field(ge=0)]
    status: Literal["ADMITTED", "DISCOVERY_ONLY", "INCOMPLETE", "UNAVAILABLE"]
    reason: Reason | None

    @model_validator(mode="after")
    def counts(self) -> "CoverageMonthDTO":
        if self.admitted_contracts > self.discovered_contracts:
            raise ValueError("Admission exceeds roster")
        if self.status == "ADMITTED":
            if self.admitted_contracts == 0 or self.admitted_contracts != self.discovered_contracts or self.reason is not None:
                raise ValueError("Admitted month needs complete roster")
        elif self.status == "INCOMPLETE":
            if self.reason is None or (self.admitted_contracts >= self.discovered_contracts and self.admitted_contracts != 0):
                raise ValueError("Incomplete month needs a reason and incomplete admission")
        elif self.admitted_contracts or self.reason is None:
            raise ValueError("Unadmitted month cannot expose eligible contracts")
        return self


class CoverageDayDTO(WireModel):
    trade_date: Day
    status: Literal["COMPLETE", "ARCHIVE_ONLY", "INCOMPLETE", "NO_SESSION", "UNAVAILABLE"]
    reason: Reason | None
    months: Annotated[list[CoverageMonthDTO], Field(max_length=MAX_COVERAGE_ITEMS)]


class CoverageProductDTO(WireModel):
    exchange: Symbol
    product: Symbol
    latest_complete_trade_date: Day | None
    days: Annotated[list[CoverageDayDTO], Field(max_length=MAX_COVERAGE_ITEMS)]
    spot_status: Literal["ADMITTED", "UNAVAILABLE"]
    spot_reason: Reason | None


class StrategyCoverageDTO(WireModel):
    products: Annotated[list[CoverageProductDTO], Field(max_length=MAX_COVERAGE_ITEMS)]
    next_cursor: Digest | None


class StrategyChainDTO(WireModel):
    snapshot: SnapshotDTO
    underlying: UnderlyingQuoteDTO
    rows: Annotated[list[ChainRowDTO], Field(max_length=MAX_CHAIN_ROWS)]
    next_cursor: Digest | None

    @model_validator(mode="after")
    def exact_page(self) -> "StrategyChainDTO":
        strikes = [row.strike for row in self.rows]
        if strikes != sorted(set(strikes)):
            raise ValueError("Duplicate or unordered strike")
        legs = [leg for row in self.rows for leg in (row.call, row.put) if leg is not None]
        ids = [leg.contract_id for leg in legs]
        if len(ids) != len(set(ids)) or any(leg.underlying_id != self.underlying.underlying_id for leg in legs):
            raise ValueError("Chain contract collision or unrelated underlying")
        return self


class LegContributionDTO(WireModel):
    instrument_id: Symbol
    pnl: Number


class StrategyPointDTO(WireModel):
    price_return: Number
    underlying_prices: Annotated[dict[Symbol, Number], Field(max_length=MAX_UNDERLYINGS)]
    status: Literal["OK", "UNAVAILABLE"]
    reason: Reason | None
    pnl: Number | None
    contributions: Annotated[list[LegContributionDTO], Field(max_length=MAX_OPTIONS + MAX_UNDERLYINGS)]

    @model_validator(mode="after")
    def total(self) -> "StrategyPointDTO":
        if self.status == "UNAVAILABLE":
            if self.pnl is not None or self.contributions or self.reason is None:
                raise ValueError("Unavailable point cannot have a partial total")
        else:
            ids = [c.instrument_id for c in self.contributions]
            if self.pnl is None or self.reason is not None or not ids or len(ids) != len(set(ids)):
                raise ValueError("Point needs unique complete contributions")
            if not isclose(sum(c.pnl for c in self.contributions), self.pnl, rel_tol=1e-12, abs_tol=1e-8):
                raise ValueError("Point does not reconcile")
        return self


class StrategyCurveDTO(WireModel):
    kind: Literal["CURRENT", "SCENARIO", "TERMINAL", "FIRST_EXPIRY_CONDITIONAL"]
    status: Status
    reason: Reason | None
    evaluation_date: Day | None
    evaluation_at: MarketClock | None
    points: Annotated[list[StrategyPointDTO], Field(max_length=MAX_PAYOFF_POINTS)]

    @model_validator(mode="after")
    def curve(self) -> "StrategyCurveDTO":
        if self.status == "OK" and (not self.points or self.reason is not None or
                                    any(p.status != "OK" for p in self.points)):
            raise ValueError("OK curve must be complete")
        if self.status == "UNAVAILABLE" and (self.reason is None or any(p.status == "OK" for p in self.points)):
            raise ValueError("Unavailable curve cannot expose usable points")
        if self.status == "PARTIAL" and (self.reason is None or not any(p.status == "OK" for p in self.points)
                                         or not any(p.status == "UNAVAILABLE" for p in self.points)):
            raise ValueError("Partial curve requires both statuses")
        returns = [p.price_return for p in self.points]
        if returns != sorted(set(returns)):
            raise ValueError("Curve points must be unique and ordered")
        if self.kind in ("CURRENT", "SCENARIO", "FIRST_EXPIRY_CONDITIONAL") and len(self.points) > MAX_MODEL_POINTS:
            raise ValueError("Model axis cap exceeded")
        return self


class RiskMetricDTO(WireModel):
    kind: Literal["FINITE", "UNBOUNDED", "CONDITIONAL_RANGE", "UNAVAILABLE"]
    value: Premium | None
    reason: Reason | None

    @model_validator(mode="after")
    def finite_only(self) -> "RiskMetricDTO":
        if (self.kind in ("FINITE", "CONDITIONAL_RANGE")) != (self.value is not None):
            raise ValueError("Risk metric cannot use a numeric infinity or unavailable value")
        if (self.kind == "UNAVAILABLE") != (self.reason is not None):
            raise ValueError("Risk metric reason mismatch")
        return self


class RootIntervalDTO(WireModel):
    start: Number | None
    end: Number | None
    start_inclusive: bool
    end_inclusive: bool

    @model_validator(mode="after")
    def order(self) -> "RootIntervalDTO":
        if self.start is not None and self.end is not None and self.start > self.end:
            raise ValueError("Reversed root interval")
        if (self.start is None and self.start_inclusive) or (self.end is None and self.end_inclusive):
            raise ValueError("Infinite endpoint cannot be included")
        return self


class StrategyRiskDTO(WireModel):
    scope: Literal["EXACT_DOMAIN", "CONDITIONAL_COMMON_RETURN", "CONDITIONAL_RANGE", "UNAVAILABLE"]
    domain: RootIntervalDTO | None
    max_profit: RiskMetricDTO
    max_loss: RiskMetricDTO
    reward_risk_ratio: RiskMetricDTO
    breakevens: Annotated[list[Number], Field(max_length=MAX_PAYOFF_POINTS)]
    zero_intervals: Annotated[list[RootIntervalDTO], Field(max_length=MAX_PAYOFF_POINTS)]
    roots_exhaustive: bool


class PayoffAccountingDTO(WireModel):
    status: Literal["OK", "UNAVAILABLE"]
    reason: Reason | None
    net_premium: Number | None
    spot_entry_cash: Number | None

    @model_validator(mode="after")
    def null_accounting(self) -> "PayoffAccountingDTO":
        if self.status == "OK":
            if self.reason is not None or self.net_premium is None or self.spot_entry_cash is None:
                raise ValueError("Accounting requires complete monetary values")
        elif self.reason is None or self.net_premium is not None or self.spot_entry_cash is not None:
            raise ValueError("Unavailable accounting cannot use zero")
        return self


class SelectedLegDTO(EntryDTO):
    instrument_id: Symbol
    underlying_id: Symbol
    kind: Literal["OPTION", "UNDERLYING"]
    quantity_unit: Unit
    expiry_id: Symbol | None
    payoff_eligibility: CapabilityDTO
    model_eligibility: CapabilityDTO
    market_price: Number | None
    multiplier: Positive | None
    quote_unit: Symbol | None
    implied_volatility: Positive | None


def _require_contribution_coverage(legs: list[SelectedLegDTO], curves: tuple[StrategyCurveDTO, ...]) -> None:
    ids = [leg.instrument_id for leg in legs]
    if len(ids) != len(set(ids)) or not any(leg.kind == "OPTION" for leg in legs):
        raise ValueError("Selected leg identities invalid")
    if any((leg.kind == "OPTION") != (leg.expiry_id is not None) for leg in legs):
        raise ValueError("Expiry identity mismatch")
    for curve in curves:
        for point in curve.points:
            if point.status == "OK" and [c.instrument_id for c in point.contributions] != ids:
                raise ValueError("Point contributions must match all selected legs in order")


class StrategyPayoffDTO(WireModel):
    snapshot: SnapshotDTO
    reference_underlying_id: Symbol
    global_price_shock: Shock
    price_shocks_by_underlying: Annotated[dict[Symbol, Shock], Field(max_length=MAX_UNDERLYINGS)]
    legs: Annotated[list[SelectedLegDTO], Field(min_length=1, max_length=MAX_OPTIONS + MAX_UNDERLYINGS)]
    accounting: PayoffAccountingDTO
    terminal: StrategyCurveDTO
    risk: StrategyRiskDTO

    @model_validator(mode="after")
    def coverage(self) -> "StrategyPayoffDTO":
        _require_contribution_coverage(self.legs, (self.terminal,))
        if self.terminal.kind not in ("TERMINAL", "FIRST_EXPIRY_CONDITIONAL"):
            raise ValueError("Wrong deterministic curve kind")
        if self.terminal.kind == "FIRST_EXPIRY_CONDITIONAL" and self.terminal.status != "UNAVAILABLE":
            raise ValueError("Conditional model horizon belongs to valuation")
        return self


class StrategyGreekGroupDTO(WireModel):
    underlying_id: Symbol
    quote_unit: Symbol
    status: Literal["OK", "UNAVAILABLE"]
    reason: Reason | None
    delta: Number | None
    gamma: Number | None
    theta_annual: Number | None
    vega_per_unit: Number | None

    @model_validator(mode="after")
    def group_values(self) -> "StrategyGreekGroupDTO":
        values = (self.delta, self.gamma, self.theta_annual, self.vega_per_unit)
        if self.status == "OK":
            if self.reason is not None or any(v is None for v in values):
                raise ValueError("Incomplete Greek group")
        elif self.reason is None or any(v is not None for v in values):
            raise ValueError("Unavailable Greek group has numeric result")
        return self


class StrategyRateEvidenceDTO(WireModel):
    expiry_id: Symbol
    quote_date: Day
    continuous_rate: Number
    policy_id: Symbol
    source_pin: Digest


class StrategyValuationDTO(WireModel):
    snapshot: SnapshotDTO
    reference_underlying_id: Symbol
    global_price_shock: Shock
    price_shocks_by_underlying: Annotated[dict[Symbol, Shock], Field(max_length=MAX_UNDERLYINGS)]
    global_iv_shock: Shock
    iv_shocks_by_expiry: Annotated[dict[Symbol, Shock], Field(max_length=MAX_OPTIONS)]
    rates: Annotated[list[StrategyRateEvidenceDTO], Field(max_length=MAX_OPTIONS)]
    model_diagnostics: Annotated[list[Symbol], Field(max_length=32)]
    legs: Annotated[list[SelectedLegDTO], Field(min_length=1, max_length=MAX_OPTIONS + MAX_UNDERLYINGS)]
    current: StrategyCurveDTO
    scenario: StrategyCurveDTO
    horizon: StrategyCurveDTO
    horizon_risk: StrategyRiskDTO
    current_greeks: Annotated[list[StrategyGreekGroupDTO], Field(max_length=MAX_UNDERLYINGS)]
    scenario_greeks: Annotated[list[StrategyGreekGroupDTO], Field(max_length=MAX_UNDERLYINGS)]
    tree_steps: Literal[128]
    iv_policy: Literal["STICKY_STRIKE"]
    day_count: Literal["ACT/365F"]

    @model_validator(mode="after")
    def coverage(self) -> "StrategyValuationDTO":
        _require_contribution_coverage(self.legs, (self.current, self.scenario, self.horizon))
        if self.current.kind != "CURRENT" or self.scenario.kind != "SCENARIO" or self.horizon.kind not in ("TERMINAL", "FIRST_EXPIRY_CONDITIONAL"):
            raise ValueError("Wrong valuation curve kind")
        return self


class StrategyEnvelopeDTO(WireModel):
    schema_version: Literal["4.0"]
    calculation_id: Annotated[str, StringConstraints(pattern=r"^[0-9a-f]{32}$")]
    status: Status
    reason: Reason | None
    portfolio_revision: Revision | None
    snapshot_id: Digest | None
    input_hash: Digest | None
    coverage: StrategyCoverageDTO | None
    chain: StrategyChainDTO | None
    payoff: StrategyPayoffDTO | None
    valuation: StrategyValuationDTO | None

    @model_validator(mode="after")
    def result_identity(self) -> "StrategyEnvelopeDTO":
        count = sum(item is not None for item in (self.coverage, self.chain, self.payoff, self.valuation))
        if self.status == "UNAVAILABLE":
            if count or self.reason is None:
                raise ValueError("Failure envelope has no result")
        elif count != 1 or (self.status == "OK") != (self.reason is None):
            raise ValueError("Exactly one result and consistent status required")
        if self.chain is not None and self.snapshot_id != self.chain.snapshot.snapshot_id:
            raise ValueError("Chain snapshot mismatch")
        for result in (self.payoff, self.valuation):
            if result is not None and (
                self.snapshot_id != result.snapshot.snapshot_id or self.portfolio_revision is None or self.input_hash is None
            ):
                raise ValueError("Portfolio result identity missing")
        return self


def strategy_openapi() -> dict[str, Any]:
    """Offline schema assembly, not an application or a data admission.

    The eventual ASGI adapter must conform to these exact routes and models,
    use the existing bounded duplicate-key parser semantics and enforce
    cross-field/source checks before responding.
    """
    schemas: dict[str, Any] = {}
    for model in (StrategyPayoffRequestDTO, StrategyValuationRequestDTO, StrategyEnvelopeDTO):
        schema = model.model_json_schema(ref_template="#/components/schemas/{model}")
        for name, definition in schema.pop("$defs", {}).items():
            if name in schemas and schemas[name] != definition:
                raise ValueError("Schema definition collision")
            schemas[name] = definition
        schemas[model.__name__] = schema

    def ref(name: str) -> dict[str, str]:
        return {"$ref": f"#/components/schemas/{name}"}

    def query(name: str, schema: dict[str, Any], required: bool = False) -> dict[str, Any]:
        return {"name": name, "in": "query", "required": required, "schema": schema}

    def operation(name: str, parameters: list[dict[str, Any]] | None = None,
                  request: str | None = None) -> dict[str, Any]:
        codes = ["200", "401", "422", "429", "500"]
        if request is not None:
            codes += ["413", "415"]
        result: dict[str, Any] = {
            "operationId": name,
            "security": [{"ResearchBearer": []}],
            "responses": {code: {
                "description": "Strategy result" if code == "200" else "Structured failure",
                "content": {"application/json": {"schema": ref("StrategyEnvelopeDTO")}},
            } for code in codes},
        }
        if parameters is not None:
            result["parameters"] = parameters
        if request is not None:
            result["requestBody"] = {"required": True, "content": {"application/json": {"schema": ref(request)}}}
        return result

    symbol = {"type": "string", "minLength": 1, "maxLength": 128}
    digest = {"type": "string", "pattern": "^[0-9a-f]{64}$"}
    return {
        "openapi": "3.1.0",
        "info": {"title": "OptionMaster Research Strategy API", "version": "4.0"},
        "paths": {
            BASE_PATH + "/coverage": {"get": operation("strategyCoverage", [
                query("exchange", symbol), query("product", symbol), query("cursor", digest),
            ])},
            BASE_PATH + "/chain": {"get": operation("strategyChain", [
                query("exchange", symbol, True), query("product", symbol, True),
                query("trade_date", {"type": "string", "format": "date"}, True),
                query("underlying_id", symbol, True), query("snapshot_id", digest), query("cursor", digest),
            ])},
            BASE_PATH + "/payoffs": {"post": operation("strategyPayoffs", request="StrategyPayoffRequestDTO")},
            BASE_PATH + "/valuations": {"post": operation("strategyValuations", request="StrategyValuationRequestDTO")},
        },
        "components": {"schemas": schemas, "securitySchemes": {"ResearchBearer": {"type": "http", "scheme": "bearer"}}},
        "x-strategy-units": {"currency": "CNY", "pnl": "CNY", "priceReturn": "DECIMAL_RETURN",
                             "iv": "ANNUAL_DECIMAL", "ivShock": "ABSOLUTE_DECIMAL_VOLATILITY",
                             "delta": "CNY_PER_PRICE_UNIT", "gamma": "CNY_PER_PRICE_UNIT_SQUARED",
                             "theta": "CNY_PER_CALENDAR_YEAR", "vega": "CNY_PER_1_ABSOLUTE_SIGMA"},
        "x-strategy-limits": {
            "maxOptions": MAX_OPTIONS, "maxUnderlyings": MAX_UNDERLYINGS, "maxQuantity": MAX_QUANTITY,
            "maxModelPoints": MAX_MODEL_POINTS, "maxPayoffPoints": MAX_PAYOFF_POINTS,
            "maxChainRows": MAX_CHAIN_ROWS, "maxCoverageItems": MAX_COVERAGE_ITEMS,
            "maxBodyBytes": MAX_BODY_BYTES, "maxResponseBytes": MAX_RESPONSE_BYTES,
        },
    }
