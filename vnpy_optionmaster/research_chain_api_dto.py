"""Version 2 archived chain API; v1 wire models remain unchanged."""
from datetime import date
from typing import Annotated, Literal

from pydantic import AwareDatetime, Field, StringConstraints, field_validator, model_validator

from vnpy_optionmaster.research_api_dto import (
    CellContributionDTO, CoverageLegDTO, EvidenceDigestDTO, LegDTO,
    Number, Quantity, RateDTO, ReasonCode, Shock, SourceVersionsDTO, Symbol,
    UnderlyingExposureDTO, UnitsDTO, ValuationRequestDTO, WireModel,
)

CHAIN_SLICE_ID: Literal["shfe-cu2211-2022-09-07-chain-v2"] = "shfe-cu2211-2022-09-07-chain-v2"


class ChainValuationRequestDTO(WireModel):
    schema_version: Literal["2.0"]
    slice_id: Literal["shfe-cu2211-2022-09-07-chain-v2"]
    positions: Annotated[dict[Symbol, Quantity], Field(min_length=2, max_length=8)]
    scenario_at: Annotated[str, StringConstraints(min_length=25, max_length=32)]
    curve_price_shocks: Annotated[list[Shock], Field(min_length=1, max_length=11)]
    heatmap_price_shocks: Annotated[list[Shock], Field(min_length=1, max_length=5)]
    heatmap_iv_shocks: Annotated[list[Shock], Field(min_length=1, max_length=5)]

    @field_validator("positions")
    @classmethod
    def nonzero(cls, value: dict[str, int]) -> dict[str, int]:
        return ValuationRequestDTO.nonzero_positions(value)

    @field_validator("scenario_at")
    @classmethod
    def market_clock(cls, value: str) -> str:
        return ValuationRequestDTO.market_clock(value)

    @field_validator("curve_price_shocks", "heatmap_price_shocks", "heatmap_iv_shocks")
    @classmethod
    def axis(cls, value: list[float]) -> list[float]:
        return ValuationRequestDTO.axis(value)


class ChainEvidenceDTO(WireModel):
    packet_sha256: str
    artifacts: list[EvidenceDigestDTO]


class ChainCoverageLegDTO(CoverageLegDTO):
    settlement: Number
    close: Number
    underlying_settlement: Number
    volume: int
    open_interest: int
    effective_contract_version: str
    official_record_id: str
    at_intrinsic: bool


class ChainMonthDTO(WireModel):
    underlying_id: str
    expiry_date: date
    discovered_contracts: int
    admitted_contracts: int


class ChainCoverageDTO(WireModel):
    slice_id: Literal["shfe-cu2211-2022-09-07-chain-v2"]
    scope: Literal["ARCHIVED_HISTORICAL_CHAIN_INPUTS_ONLY"] = "ARCHIVED_HISTORICAL_CHAIN_INPUTS_ONLY"
    production_valuation_ready: Literal[False] = False
    trade_date: date
    data_observed_at: AwareDatetime
    approved_at: AwareDatetime
    admitted_at: AwareDatetime
    price_basis: Literal["SETTLEMENT"] = "SETTLEMENT"
    requested_legs: int
    admitted_legs: int
    min_selection_legs: Literal[2] = 2
    max_selection_legs: Literal[8] = 8
    months: list[ChainMonthDTO]
    legs: list[ChainCoverageLegDTO]
    source_versions: ChainEvidenceDTO


class ChainGridPointDTO(WireModel):
    price_shock: Number
    iv_shock: Number
    status: Literal["OK", "UNAVAILABLE"]
    reason_code: ReasonCode | None
    baseline_value: Number | None
    scenario_value: Number | None
    value_change: Number | None
    contributions: list[CellContributionDTO]

    @model_validator(mode="after")
    def status_consistency(self) -> "ChainGridPointDTO":
        values = (self.baseline_value, self.scenario_value, self.value_change)
        if self.status == "OK":
            if self.reason_code is not None or any(v is None for v in values) or not 2 <= len(self.contributions) <= 8:
                raise ValueError("OK cell requires complete values and contributions")
        elif self.reason_code is None or any(v is not None for v in values) or self.contributions:
            raise ValueError("Unavailable cell must not contain numerical values")
        return self


class ChainGridDTO(WireModel):
    baseline_value: Number
    configured_scenario_value: Number
    configured_value_change: Number
    curve_price_axis: list[Number]
    heatmap_price_axis: list[Number]
    heatmap_iv_axis: list[Number]
    price_curve: list[ChainGridPointDTO]
    heatmap_rows: list[list[ChainGridPointDTO]]


class ChainValuationDTO(WireModel):
    slice_id: Literal["shfe-cu2211-2022-09-07-chain-v2"]
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
    model_version: Literal["m0-american-binomial-v1+d05-archive-v1"] = "m0-american-binomial-v1+d05-archive-v1"
    tree_steps: int
    units: UnitsDTO
    warnings: list[str]
    legs: Annotated[list[LegDTO], Field(min_length=2, max_length=8)]
    underlying_exposures: list[UnderlyingExposureDTO]
    grid: ChainGridDTO

    @model_validator(mode="after")
    def selected_contributions(self) -> "ChainValuationDTO":
        ids = [leg.contract_id for leg in self.legs]
        if len(set(ids)) != len(ids) or len({leg.underlying_id for leg in self.legs}) != 1:
            raise ValueError("Distinct single-underlying legs required")
        for point in [*self.grid.price_curve, *(p for row in self.grid.heatmap_rows for p in row)]:
            if point.status == "OK" and [c.contract_id for c in point.contributions] != ids:
                raise ValueError("Cell contribution order or coverage differs")
        return self


class ChainApiEnvelope(WireModel):
    schema_version: Literal["2.0"] = "2.0"
    calculation_id: Annotated[str, StringConstraints(pattern=r"^[0-9a-f]{32}$")]
    status: Literal["OK", "UNAVAILABLE"]
    reason_code: ReasonCode | None = None
    coverage: ChainCoverageDTO | None = None
    valuation: ChainValuationDTO | None = None

    @model_validator(mode="after")
    def no_partial_result(self) -> "ChainApiEnvelope":
        count = int(self.coverage is not None) + int(self.valuation is not None)
        if self.status == "OK":
            if count != 1 or self.reason_code is not None:
                raise ValueError("OK envelope requires exactly one result")
        elif count or self.reason_code is None:
            raise ValueError("Unavailable envelope has no partial result")
        return self
