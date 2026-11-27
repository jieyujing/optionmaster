"""Independent versioned Qt display projection; admitted historical snapshot only."""
from datetime import date, datetime
from math import isfinite
from time import monotonic
from typing import Annotated, Literal

from pydantic import Field, model_validator

from vnpy_optionmaster.research_api_dto import Number, Symbol, WireModel
from vnpy_optionmaster.research_pricing import research_pricing_scope
from vnpy_optionmaster.research_strategy_api_dto import MAX_CHAIN_ROWS, Reason, SnapshotDTO, StrategyRateEvidenceDTO
from vnpy_optionmaster.research_strategy_payoff import StrategyQuoteSnapshot, StrategyUnavailable
from vnpy_optionmaster.research_strategy_valuation import calibrate_quote, quote_model_greeks, _years

Basis = Literal["UNAVAILABLE", "SETTLEMENT_MODEL", "APPROVED_CLOSE_MODEL", "ADMITTED_DAILY", "ADMITTED_BOOK"]

class QuoteFieldDTO(WireModel):
    value: Number | None
    basis: Basis
    reason: Reason | None

    @model_validator(mode="after")
    def validate_availability(self) -> "QuoteFieldDTO":
        if self.value is None:
            if self.basis != "UNAVAILABLE" or self.reason is None:
                raise ValueError("Missing value requires reason")
        elif self.basis == "UNAVAILABLE" or self.reason is not None or not isfinite(self.value):
            raise ValueError("Available value requires admitted basis")
        return self

class QuoteDetailDTO(WireModel):
    contract_id: Symbol
    theo_vega: QuoteFieldDTO
    theo_theta: QuoteFieldDTO
    theo_gamma: QuoteFieldDTO
    theo_delta: QuoteFieldDTO
    open_interest: QuoteFieldDTO
    volume: QuoteFieldDTO
    bid_impv: QuoteFieldDTO
    bid_volume: QuoteFieldDTO
    bid_price: QuoteFieldDTO
    ask_price: QuoteFieldDTO
    ask_volume: QuoteFieldDTO
    ask_impv: QuoteFieldDTO

    @model_validator(mode="after")
    def validate_bases(self) -> "QuoteDetailDTO":
        for name in ("theo_vega", "theo_theta", "theo_gamma", "theo_delta"):
            if getattr(self, name).basis not in ("UNAVAILABLE", "SETTLEMENT_MODEL", "APPROVED_CLOSE_MODEL"):
                raise ValueError("Greek basis invalid")
        for name in ("open_interest", "volume", "bid_impv", "bid_volume", "bid_price", "ask_price", "ask_volume", "ask_impv"):
            field = getattr(self, name)
            expected = "ADMITTED_DAILY" if name in ("open_interest", "volume") else "ADMITTED_BOOK"
            if field.basis not in ("UNAVAILABLE", expected) or (field.value is not None and field.value < 0):
                raise ValueError("Quote basis or value invalid")
        return self

class QuoteDetailsDTO(WireModel):
    schema_version: Literal["1.0"]
    snapshot: SnapshotDTO
    underlying_id: Symbol
    quotes: Annotated[list[QuoteDetailDTO], Field(max_length=2 * MAX_CHAIN_ROWS)]

    @model_validator(mode="after")
    def validate_identities(self) -> "QuoteDetailsDTO":
        ids = [q.contract_id for q in self.quotes]
        if len(ids) != len(set(ids)):
            raise ValueError("Duplicate quote details")
        return self

class HistoricalQuoteDetailDTO(QuoteDetailDTO):
    close_price: QuoteFieldDTO
    settlement_price: QuoteFieldDTO
    model_iv: QuoteFieldDTO

    @model_validator(mode="after")
    def validate_historical_bases(self) -> "HistoricalQuoteDetailDTO":
        for field in (self.close_price, self.settlement_price):
            if field.basis not in ("UNAVAILABLE", "ADMITTED_DAILY") or (field.value is not None and field.value < 0):
                raise ValueError("Historical price basis invalid")
        if self.model_iv.basis not in ("UNAVAILABLE", "SETTLEMENT_MODEL", "APPROVED_CLOSE_MODEL") or (self.model_iv.value is not None and self.model_iv.value <= 0):
            raise ValueError("Model IV basis invalid")
        return self


class HistoricalQuoteDetailsDTO(WireModel):
    schema_version: Literal["1.1"]
    snapshot: SnapshotDTO
    underlying_id: Symbol
    quotes: Annotated[list[HistoricalQuoteDetailDTO], Field(max_length=2 * MAX_CHAIN_ROWS)]

    @model_validator(mode="after")
    def validate_identities(self) -> "HistoricalQuoteDetailsDTO":
        if len({q.contract_id for q in self.quotes}) != len(self.quotes):
            raise ValueError("Duplicate quote details")
        return self


def unavailable(reason: Reason) -> QuoteFieldDTO:
    return QuoteFieldDTO(value=None, basis="UNAVAILABLE", reason=reason)

def project_quote_details(snapshot: StrategyQuoteSnapshot, underlying_id: str, *,
                          daily_marks: dict[str, dict[str, float | None]] | None = None
                          ) -> QuoteDetailsDTO | HistoricalQuoteDetailsDTO:
    u = next((u for u in snapshot.underlyings if u.underlying_id == underlying_id), None)
    if u is None:
        raise StrategyUnavailable("SCOPE_UNAVAILABLE")
    options = [q for q in snapshot.options if q.underlying_id == underlying_id]
    if len(options) > 2 * MAX_CHAIN_ROWS:
        raise StrategyUnavailable("SOURCE_INCOMPLETE")
    metadata = snapshot.metadata
    pins = {metadata.source_pin, *metadata.source_versions.values()}
    rates: dict[str, StrategyRateEvidenceDTO] = {}
    invalid: set[str] = set()
    for rate in snapshot.rates:
        try:
            valid_date = date.fromisoformat(rate.quote_date).isoformat() == rate.quote_date
        except ValueError:
            valid_date = False
        if (rate.expiry_id in rates or rate.source_pin not in pins
                or not valid_date or rate.quote_date > metadata.trade_date):
            invalid.add(rate.expiry_id)
        rates[rate.expiry_id] = rate
    for key in invalid:
        rates.pop(key, None)
    reference = datetime.fromisoformat(metadata.reference_at)
    basis: Basis = "SETTLEMENT_MODEL" if metadata.price_basis == "SETTLEMENT" else "APPROVED_CLOSE_MODEL"
    missing = unavailable("SOURCE_INCOMPLETE")
    quotes: list[QuoteDetailDTO] = []
    historical: list[HistoricalQuoteDetailDTO] = []
    def daily(value: float | None) -> QuoteFieldDTO:
        return missing if value is None else QuoteFieldDTO(value=value, basis="ADMITTED_DAILY", reason=None)
    end = monotonic() + 25.0
    with research_pricing_scope():
        for q in options:
            calibration = calibrate_quote(q, u, reference, rates, end,
                                          pricing_adjustment=snapshot.pricing_adjustment(q.expiry_id))
            greek_fields = [unavailable(calibration.reason or "NUMERICAL_FAILURE")] * 4
            if (calibration.reason is None and calibration.sigma is not None and calibration.rate is not None
                    and calibration.expiry is not None and u.market_price is not None):
                try:
                    # Contract display units exactly as Qt: delta/gamma * multiplier,
                    # theta per 240 trading days; vega per one IV percentage point.
                    _, delta, gamma, theta, vega = quote_model_greeks(
                        q, u, u.market_price, calibration.rate,
                        _years(reference, calibration.expiry), calibration.sigma,
                        1 if q.option_type == "CALL" else -1,
                        pricing_adjustment=calibration.pricing_adjustment)
                    values = (vega*q.multiplier/100, theta*q.multiplier/240, gamma*q.multiplier, delta*q.multiplier)
                    if all(isfinite(v) for v in values):
                        greek_fields = [QuoteFieldDTO(value=v, basis=basis, reason=None) for v in values]
                except (ArithmeticError, ValueError, RuntimeError):
                    pass
            quote = QuoteDetailDTO(contract_id=q.contract_id,
                theo_vega=greek_fields[0], theo_theta=greek_fields[1],
                theo_gamma=greek_fields[2], theo_delta=greek_fields[3],
                open_interest=missing, volume=missing, bid_impv=missing, bid_volume=missing,
                bid_price=missing, ask_price=missing, ask_volume=missing, ask_impv=missing)
            if daily_marks is None:
                quotes.append(quote)
            else:
                raw = daily_marks.get(q.contract_id, {})
                iv = (QuoteFieldDTO(value=calibration.sigma, basis=basis, reason=None)
                      if calibration.reason is None and calibration.sigma is not None
                      else unavailable(calibration.reason or "NUMERICAL_FAILURE"))
                historical.append(HistoricalQuoteDetailDTO(**{
                    **quote.model_dump(), "volume": daily(raw.get("volume")),
                    "open_interest": daily(raw.get("open_interest")),
                    "close_price": daily(raw.get("close")),
                    "settlement_price": daily(raw.get("settlement")), "model_iv": iv,
                }))
    if daily_marks is not None:
        return HistoricalQuoteDetailsDTO(schema_version="1.1", snapshot=metadata, underlying_id=underlying_id, quotes=historical)
    return QuoteDetailsDTO(schema_version="1.0", snapshot=metadata, underlying_id=underlying_id, quotes=quotes)
