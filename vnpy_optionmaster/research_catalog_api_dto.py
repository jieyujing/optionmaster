"""D09 explicit v3 wire models. D06/D08 schemas remain unchanged."""
from datetime import date
from typing import Annotated, Literal

from pydantic import AwareDatetime, Field, StringConstraints, field_validator, model_validator

from vnpy_optionmaster.research_api_dto import (
    LegDTO, Number, Quantity, RateDTO, ReasonCode, Shock, SourceVersionsDTO,
    Symbol, UnderlyingExposureDTO, UnitsDTO, ValuationRequestDTO, WireModel,
)
from vnpy_optionmaster.research_chain_api_dto import (
    ChainCoverageLegDTO, ChainEvidenceDTO, ChainGridDTO, ChainMonthDTO,
)

CATALOG_ID: Literal["shfe-cu-dated-catalog-v3"] = "shfe-cu-dated-catalog-v3"


class CatalogValuationRequestDTO(WireModel):
    schema_version: Literal["3.0"]
    catalog_id: Literal["shfe-cu-dated-catalog-v3"]
    trade_date: Annotated[str, StringConstraints(pattern=r"^\d{4}-\d{2}-\d{2}$")]
    positions: Annotated[dict[Symbol, Quantity], Field(min_length=2, max_length=8)]
    scenario_at: Annotated[str, StringConstraints(min_length=25, max_length=32)]
    curve_price_shocks: Annotated[list[Shock], Field(min_length=1, max_length=11)]
    heatmap_price_shocks: Annotated[list[Shock], Field(min_length=1, max_length=5)]
    heatmap_iv_shocks: Annotated[list[Shock], Field(min_length=1, max_length=5)]
    price_shocks_by_underlying: Annotated[dict[Symbol, Shock], Field(max_length=8)]
    iv_shocks_by_expiry: Annotated[dict[Symbol, Shock], Field(max_length=8)]

    @field_validator("trade_date")
    @classmethod
    def date_string(cls, value: str) -> str:
        if date.fromisoformat(value).isoformat() != value:
            raise ValueError("Invalid trade date")
        return value

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


class CatalogMonthDTO(ChainMonthDTO):
    child_packet_sha256: str | None
    observed_at: AwareDatetime | None
    underlying_settlement: Number | None


class CatalogCoverageDTO(WireModel):
    catalog_id: Literal["shfe-cu-dated-catalog-v3"]
    scope: Literal["ARCHIVED_HISTORICAL_CATALOG_INPUTS_ONLY"] = "ARCHIVED_HISTORICAL_CATALOG_INPUTS_ONLY"
    production_valuation_ready: Literal[False] = False
    trade_date: date
    available_dates: list[date]
    approved_contract_dates: int
    data_observed_at: AwareDatetime
    approved_at: AwareDatetime
    admitted_at: AwareDatetime
    price_basis: Literal["SETTLEMENT"] = "SETTLEMENT"
    requested_legs: int
    admitted_legs: int
    min_selection_legs: Literal[2] = 2
    max_selection_legs: Literal[8] = 8
    months: list[CatalogMonthDTO]
    legs: list[ChainCoverageLegDTO]
    source_versions: ChainEvidenceDTO


class CatalogExpiryDTO(WireModel):
    expiry_id: str
    underlying_id: str
    expiry_date: date
    nominal_expiry_at: AwareDatetime
    baseline_time_to_expiry: Number


class CatalogLegDTO(LegDTO):
    expiry_id: str
    baseline_time_to_expiry: Number


class CatalogValuationDTO(WireModel):
    catalog_id: Literal["shfe-cu-dated-catalog-v3"]
    scope: Literal["ARCHIVED_HISTORICAL_RESEARCH_ONLY"] = "ARCHIVED_HISTORICAL_RESEARCH_ONLY"
    production_valuation_ready: Literal[False] = False
    trade_date: date
    data_observed_at: AwareDatetime
    approved_at: AwareDatetime
    admitted_at: AwareDatetime
    reference_at: AwareDatetime
    scenario_at: AwareDatetime
    expiries: Annotated[list[CatalogExpiryDTO], Field(min_length=1, max_length=8)]
    expiry_guard: Literal["DATE_ONLY_NO_EXPIRY_DAY"] = "DATE_ONLY_NO_EXPIRY_DAY"
    day_count_basis: Literal["ACT/365F"] = "ACT/365F"
    clock_policy: str
    price_basis: Literal["SETTLEMENT"] = "SETTLEMENT"
    price_basis_fallback_reason: None = None
    source_versions: SourceVersionsDTO
    child_packet_sha256: list[str]
    rate: RateDTO
    model: str
    model_version: Literal["m0-american-binomial-v1+d09-catalog-v1"] = "m0-american-binomial-v1+d09-catalog-v1"
    tree_steps: int
    units: UnitsDTO
    warnings: list[str]
    price_shocks_by_underlying: dict[str, Shock]
    iv_shocks_by_expiry: dict[str, Shock]
    legs: Annotated[list[CatalogLegDTO], Field(min_length=2, max_length=8)]
    underlying_exposures: list[UnderlyingExposureDTO]
    grid: ChainGridDTO

    @model_validator(mode="after")
    def selected_contributions(self) -> "CatalogValuationDTO":
        ids = [leg.contract_id for leg in self.legs]
        underlyings = {leg.underlying_id for leg in self.legs}
        expiry_ids = {leg.expiry_id for leg in self.legs}
        if len(set(ids)) != len(ids) or len(self.expiries) != len(expiry_ids) or {e.expiry_id for e in self.expiries} != expiry_ids:
            raise ValueError("Distinct legs and exact expiry identities required")
        if [e.underlying_id for e in self.underlying_exposures] != sorted(underlyings):
            raise ValueError("Exposure identities must exactly cover selected underlyings")
        by_expiry = {e.expiry_id: e for e in self.expiries}
        if any(
            (leg.underlying_id, leg.expiry_date, leg.baseline_time_to_expiry)
            != (by_expiry[leg.expiry_id].underlying_id, by_expiry[leg.expiry_id].expiry_date,
                by_expiry[leg.expiry_id].baseline_time_to_expiry)
            for leg in self.legs
        ):
            raise ValueError("Leg expiry mismatch")
        for point in [*self.grid.price_curve, *(p for row in self.grid.heatmap_rows for p in row)]:
            if point.status == "OK" and [c.contract_id for c in point.contributions] != ids:
                raise ValueError("Cell contribution order or coverage differs")
        return self


class CatalogApiEnvelope(WireModel):
    schema_version: Literal["3.0"] = "3.0"
    calculation_id: Annotated[str, StringConstraints(pattern=r"^[0-9a-f]{32}$")]
    status: Literal["OK", "UNAVAILABLE"]
    reason_code: ReasonCode | None = None
    coverage: CatalogCoverageDTO | None = None
    valuation: CatalogValuationDTO | None = None

    @model_validator(mode="after")
    def no_partial_result(self) -> "CatalogApiEnvelope":
        count = int(self.coverage is not None) + int(self.valuation is not None)
        if self.status == "OK":
            if count != 1 or self.reason_code is not None:
                raise ValueError("OK envelope requires exactly one result")
        elif count or self.reason_code is None:
            raise ValueError("Unavailable envelope has no partial result")
        return self
