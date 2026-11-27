"""Explicit v1 JSON contract for the optional archived research Web API.

No domain dataclass is automatically exposed on the wire. The request is
validated after bounded duplicate-key-aware JSON parsing in research_api.
"""
from datetime import date, datetime, timedelta
from enum import Enum
from typing import Annotated, Literal
import re

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, StringConstraints, field_validator, model_validator

SCHEMA_VERSION: Literal["1.0"] = "1.0"
SLICE_ID: Literal["shfe-cu2211-2022-09-07-v1"] = "shfe-cu2211-2022-09-07-v1"
MODEL_VERSION: Literal["m0-american-binomial-v1+d05-archive-v1"] = "m0-american-binomial-v1+d05-archive-v1"
Number = Annotated[float, Field(allow_inf_nan=False)]
Shock = Annotated[float, Field(ge=-1.0, le=1.0, allow_inf_nan=False)]
Quantity = Annotated[int, Field(ge=-1000, le=1000)]
Symbol = Annotated[str, StringConstraints(min_length=1, max_length=128)]


class ReasonCode(str, Enum):
    """Stable v1 errors; internal exception text never crosses this seam."""

    UNAUTHORIZED = "UNAUTHORIZED"
    NOT_FOUND = "NOT_FOUND"
    METHOD_NOT_ALLOWED = "METHOD_NOT_ALLOWED"
    INVALID_REQUEST = "INVALID_REQUEST"
    INVALID_JSON = "INVALID_JSON"
    PAYLOAD_TOO_LARGE = "PAYLOAD_TOO_LARGE"
    UNSUPPORTED_MEDIA_TYPE = "UNSUPPORTED_MEDIA_TYPE"
    SLICE_SCOPE_MISMATCH = "SLICE_SCOPE_MISMATCH"
    BUSY = "BUSY"
    ARCHIVE_UNAVAILABLE = "ARCHIVE_UNAVAILABLE"
    RATE_UNAVAILABLE = "RATE_UNAVAILABLE"
    EXPIRED_SCENARIO = "EXPIRED_SCENARIO"
    INVALID_SCENARIO_TIME = "INVALID_SCENARIO_TIME"
    INVALID_SCENARIO = "INVALID_SCENARIO"
    IV_UNAVAILABLE = "IV_UNAVAILABLE"
    NUMERICAL_FAILURE = "NUMERICAL_FAILURE"
    VALUATION_UNAVAILABLE = "VALUATION_UNAVAILABLE"
    INTERNAL_ERROR = "INTERNAL_ERROR"


class WireModel(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, allow_inf_nan=False, frozen=True)


class ValuationRequestDTO(WireModel):
    schema_version: Literal["1.0"]
    slice_id: Literal["shfe-cu2211-2022-09-07-v1"]
    positions: Annotated[dict[Symbol, Quantity], Field(min_length=2, max_length=2)]
    scenario_at: Annotated[str, StringConstraints(min_length=25, max_length=32)]
    curve_price_shocks: Annotated[list[Shock], Field(min_length=1, max_length=11)]
    heatmap_price_shocks: Annotated[list[Shock], Field(min_length=1, max_length=5)]
    heatmap_iv_shocks: Annotated[list[Shock], Field(min_length=1, max_length=5)]

    @field_validator("positions")
    @classmethod
    def nonzero_positions(cls, value: dict[str, int]) -> dict[str, int]:
        if any(q == 0 for q in value.values()):
            raise ValueError("Position quantity must be nonzero")
        return value

    @field_validator("scenario_at")
    @classmethod
    def market_clock(cls, value: str) -> str:
        if not re.fullmatch(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{1,6})?\+08:00", value):
            raise ValueError("Explicit Asia/Shanghai ISO-8601 offset required")
        parsed = datetime.fromisoformat(value)
        if parsed.utcoffset() != timedelta(hours=8):
            raise ValueError("Expected market timezone")
        return value

    @field_validator("curve_price_shocks", "heatmap_price_shocks", "heatmap_iv_shocks")
    @classmethod
    def axis(cls, value: list[float]) -> list[float]:
        if 0.0 not in value or len(set(value)) != len(value):
            raise ValueError("Axes must contain zero and distinct values")
        return value


class EvidenceDigestDTO(WireModel):
    artifact: str
    sha256: str


class SourceVersionsDTO(WireModel):
    packet_sha256: str
    registry_sha256: str
    artifacts: list[EvidenceDigestDTO]


class RateDTO(WireModel):
    quote_date: date
    quoted_percent: Number
    tenor_years: Number
    continuous_rate: Number
    source_uri: str
    artifact_sha256: str
    convention: str


class UnitsDTO(WireModel):
    currency: Literal["CNY"] = "CNY"
    mark: Literal["CNY_PER_TON"] = "CNY_PER_TON"
    value: Literal["CNY"] = "CNY"
    quantity: Literal["CONTRACTS"] = "CONTRACTS"
    multiplier: Literal["TONS_PER_CONTRACT"] = "TONS_PER_CONTRACT"
    price_shock: Literal["DECIMAL_RETURN"] = "DECIMAL_RETURN"
    implied_volatility: Literal["ANNUAL_DECIMAL"] = "ANNUAL_DECIMAL"
    iv_shock: Literal["ABSOLUTE_DECIMAL_VOLATILITY"] = "ABSOLUTE_DECIMAL_VOLATILITY"
    delta: Literal["CNY_PER_PRICE_UNIT"] = "CNY_PER_PRICE_UNIT"
    gamma: Literal["CNY_PER_PRICE_UNIT_SQUARED"] = "CNY_PER_PRICE_UNIT_SQUARED"
    theta: Literal["CNY_PER_CALENDAR_YEAR"] = "CNY_PER_CALENDAR_YEAR"
    vega: Literal["CNY_PER_1_ABSOLUTE_SIGMA"] = "CNY_PER_1_ABSOLUTE_SIGMA"


class GreeksDTO(WireModel):
    delta: Number
    gamma: Number
    theta_annual: Number
    vega_per_unit: Number


class CoverageLegDTO(WireModel):
    contract_id: str
    source_namespace: str
    source_exchange: str
    source_symbol: str
    underlying_id: str
    expiry_date: date
    option_type: Literal["CALL", "PUT"]
    exercise_style: str
    strike: Number
    contract_multiplier: Number


class CoverageDTO(WireModel):
    slice_id: Literal["shfe-cu2211-2022-09-07-v1"]
    scope: Literal["ARCHIVED_HISTORICAL_INPUTS_ONLY"] = "ARCHIVED_HISTORICAL_INPUTS_ONLY"
    production_valuation_ready: Literal[False] = False
    trade_date: date
    data_observed_at: AwareDatetime
    admitted_at: AwareDatetime
    price_basis: str
    requested_legs: int
    admitted_legs: int
    legs: list[CoverageLegDTO]
    source_versions: SourceVersionsDTO


class LegDTO(CoverageLegDTO):
    quantity: int
    raw_contract_version: str
    raw_exercise_style: str
    effective_contract_version: str
    official_record_id: str
    official_source_uri: str
    official_artifact_sha256: str
    terms_ingested_at: AwareDatetime
    option_ingested_at: AwareDatetime
    underlying_ingested_at: AwareDatetime
    option_source: str
    underlying_source: str
    market_option_price: Number
    market_underlying_price: Number
    implied_volatility: Number
    iv_price_residual: Number
    baseline_value: Number
    baseline_greeks: GreeksDTO
    scenario_iv: Number
    scenario_time_to_expiry: Number
    scenario_value: Number
    value_change: Number
    scenario_greeks: GreeksDTO


class CellContributionDTO(WireModel):
    contract_id: str
    scenario_value: Number
    value_change: Number
    scenario_iv: Number
    scenario_time_to_expiry: Number


class GridPointDTO(WireModel):
    price_shock: Number
    iv_shock: Number
    status: Literal["OK", "UNAVAILABLE"]
    reason_code: ReasonCode | None
    baseline_value: Number | None
    scenario_value: Number | None
    value_change: Number | None
    contributions: list[CellContributionDTO]

    @model_validator(mode="after")
    def status_consistency(self) -> "GridPointDTO":
        values = (self.baseline_value, self.scenario_value, self.value_change)
        if self.status == "OK":
            if self.reason_code is not None or any(v is None for v in values) or len(self.contributions) != 2:
                raise ValueError("OK cell requires complete values and contributions")
        elif self.reason_code is None or any(v is not None for v in values) or self.contributions:
            raise ValueError("Unavailable cell must not contain numerical values")
        return self


class GridDTO(WireModel):
    baseline_value: Number
    configured_scenario_value: Number
    configured_value_change: Number
    curve_price_axis: list[Number]
    heatmap_price_axis: list[Number]
    heatmap_iv_axis: list[Number]
    price_curve: list[GridPointDTO]
    heatmap_rows: list[list[GridPointDTO]]


class UnderlyingExposureDTO(WireModel):
    underlying_id: str
    baseline_greeks: GreeksDTO
    scenario_greeks: GreeksDTO


class ValuationDTO(WireModel):
    slice_id: Literal["shfe-cu2211-2022-09-07-v1"]
    scope: Literal["ARCHIVED_HISTORICAL_RESEARCH_ONLY"] = "ARCHIVED_HISTORICAL_RESEARCH_ONLY"
    production_valuation_ready: Literal[False] = False
    trade_date: date
    data_observed_at: AwareDatetime
    approved_at: AwareDatetime
    admitted_at: AwareDatetime
    reference_at: AwareDatetime
    scenario_at: AwareDatetime
    nominal_expiry_at: AwareDatetime
    expiry_guard: Literal["DATE_ONLY_NO_EXPIRY_DAY"] = "DATE_ONLY_NO_EXPIRY_DAY"
    day_count_basis: Literal["ACT/365F"] = "ACT/365F"
    clock_policy: str
    time_to_expiry_years: Number
    price_basis: str
    price_basis_fallback_reason: str | None
    source_versions: SourceVersionsDTO
    rate: RateDTO
    model: str
    model_version: Literal["m0-american-binomial-v1+d05-archive-v1"] = MODEL_VERSION
    tree_steps: int
    units: UnitsDTO
    warnings: list[str]
    legs: list[LegDTO]
    underlying_exposures: list[UnderlyingExposureDTO]
    grid: GridDTO


class ApiEnvelope(WireModel):
    schema_version: Literal["1.0"] = SCHEMA_VERSION
    calculation_id: Annotated[str, StringConstraints(pattern=r"^[0-9a-f]{32}$")]
    status: Literal["OK", "UNAVAILABLE"]
    reason_code: ReasonCode | None = None
    coverage: CoverageDTO | None = None
    valuation: ValuationDTO | None = None

    @model_validator(mode="after")
    def no_partial_result(self) -> "ApiEnvelope":
        count = int(self.coverage is not None) + int(self.valuation is not None)
        if self.status == "OK":
            if count != 1 or self.reason_code is not None:
                raise ValueError("OK envelope requires exactly one result")
        elif count != 0 or self.reason_code is None:
            raise ValueError("UNAVAILABLE envelope cannot include a partial result")
        return self
