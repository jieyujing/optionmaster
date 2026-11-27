"""M1-D01: source-neutral, read-only historical quote-pair basis resolution.

This module consumes a SINGLE caller-materialized set of explicit historical
option and underlying quote rows. It never connects to ClickHouse, queries
market data, guesses vendor symbol aliases, forward-fills, or trades.

An exchange/product/alias mapping is an authoritative INPUT with a provenance
label; a non-empty label alone does not establish the mapping's truth. The
ClickHouse adapter must separately verify such identity contracts before it
can claim real-data readiness. Same-date/dual-source read consistency is also
a separate M1 gate, not established by these Python dataclasses.
"""
from dataclasses import dataclass
from datetime import date, datetime, timezone
from enum import Enum
from math import isfinite

from vnpy_optionmaster.research_combination import ResearchAssetClass
from vnpy_optionmaster.research_risk import ResearchValuationStatus


class HistoricalBasis(str, Enum):
    SETTLEMENT = "SETTLEMENT"
    CLOSE_FALLBACK = "CLOSE_FALLBACK"
    CLOSE = "CLOSE"


@dataclass(frozen=True)
class HistoricalQuoteRow:
    """One raw, already-materialized source quote, never a synthetic mark."""

    symbol: str
    exchange: str
    trade_date: date
    close: float | None
    settlement: float | None
    source: str
    ingested_at: datetime


@dataclass(frozen=True)
class HistoricalLegSelection:
    """Exact source symbol mapping supplied by an independently checked registry.

    `underlying_contract_id` is the option-terms underlying identity. It may
    differ from `underlying_quote_symbol` / quote exchange in the futures table.
    The resolver NEVER derives one from the other.
    """

    option_symbol: str
    option_exchange: str
    underlying_contract_id: str
    underlying_quote_symbol: str
    underlying_quote_exchange: str
    product_family_id: str
    asset_class: ResearchAssetClass | str
    mapping_provenance: str


@dataclass(frozen=True)
class HistoricalBasisRequest:
    """One consistent requested historical date, read time and row bundle."""

    trade_date: date
    read_at: datetime
    selections: tuple[HistoricalLegSelection, ...]
    option_quotes: tuple[HistoricalQuoteRow, ...]
    underlying_quotes: tuple[HistoricalQuoteRow, ...]


@dataclass(frozen=True)
class HistoricalResolvedLeg:
    option_symbol: str
    option_exchange: str
    underlying_contract_id: str
    underlying_quote_symbol: str
    underlying_quote_exchange: str
    product_family_id: str
    asset_class: ResearchAssetClass
    mapping_provenance: str
    option_price: float
    underlying_price: float
    option_source: str
    underlying_source: str
    option_ingested_at: datetime
    underlying_ingested_at: datetime


@dataclass(frozen=True)
class HistoricalBasisResult:
    status: ResearchValuationStatus
    reason_code: str | None = None
    price_basis: HistoricalBasis | None = None
    fallback_reason: str | None = None
    trade_date: date | None = None
    read_at: datetime | None = None
    legs: tuple[HistoricalResolvedLeg, ...] = ()
    failed_leg_index: int | None = None


def _unavailable(reason: str, failed_leg_index: int | None = None) -> HistoricalBasisResult:
    """Atomic failure; no partially usable marks returned."""
    return HistoricalBasisResult(
        status=ResearchValuationStatus.UNAVAILABLE,
        reason_code=reason,
        failed_leg_index=failed_leg_index,
    )


def _canonical(value: object) -> bool:
    return (
        isinstance(value, str)
        and bool(value)
        and value == value.strip()
        and not any(c.isspace() for c in value)
    )


def _aware(value: object) -> bool:
    return (
        isinstance(value, datetime)
        and value.tzinfo is not None
        and value.utcoffset() is not None
    )


def _valid_mark(value: object) -> bool:
    if value is None:
        return True
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return False
    try:
        return isfinite(value) and value > 0
    except (OverflowError, ValueError):
        return False


def _load_quotes(
    raw: tuple[HistoricalQuoteRow, ...],
    trade_date: date,
    read_at: datetime,
) -> tuple[dict[tuple[str, str], HistoricalQuoteRow] | None, str | None]:
    if not isinstance(raw, tuple):
        return None, "INVALID_INPUT"
    by_key: dict[tuple[str, str], HistoricalQuoteRow] = {}
    for row in raw:
        if not isinstance(row, HistoricalQuoteRow):
            return None, "INVALID_INPUT"
        if (
            not _canonical(row.symbol) or not _canonical(row.exchange)
            or not _canonical(row.source) or not _aware(row.ingested_at)
        ):
            return None, "INVALID_QUOTE"
        if type(row.trade_date) is not date:
            return None, "INVALID_QUOTE"
        if row.trade_date != trade_date:
            return None, "MISMATCHED_TRADE_DATE"
        if row.ingested_at.astimezone(timezone.utc) > read_at.astimezone(timezone.utc):
            return None, "FUTURE_INGESTED_AT"
        if not _valid_mark(row.close) or not _valid_mark(row.settlement):
            return None, "INVALID_QUOTE"
        key = (row.exchange, row.symbol)
        if key in by_key:
            return None, "DUPLICATE_QUOTE"
        by_key[key] = row
    return by_key, None


class HistoricalBasisEngine:
    """Choose one coherent basis for all selected option/underlying pairs."""

    @staticmethod
    def evaluate(request: HistoricalBasisRequest) -> HistoricalBasisResult:
        if (
            not isinstance(request, HistoricalBasisRequest)
            or type(request.trade_date) is not date
            or not _aware(request.read_at)
            or not isinstance(request.selections, tuple)
            or len(request.selections) < 2
        ):
            return _unavailable("INVALID_INPUT")

        reference_class: ResearchAssetClass | None = None
        reference_exchange: str | None = None
        reference_family: str | None = None
        reference_underlying: str | None = None

        seen_options: set[tuple[str, str]] = set()
        aliases_by_id: dict[str, tuple[str, str]] = {}
        ids_by_quote: dict[tuple[str, str], str] = {}
        option_keys: set[tuple[str, str]] = set()
        underlying_keys: set[tuple[str, str]] = set()

        for index, selected in enumerate(request.selections):
            if not isinstance(selected, HistoricalLegSelection):
                return _unavailable("INVALID_INPUT", index)
            if not all(_canonical(x) for x in (
                selected.option_symbol, selected.option_exchange,
                selected.underlying_contract_id, selected.underlying_quote_symbol,
                selected.underlying_quote_exchange, selected.product_family_id,
                selected.mapping_provenance,
            )):
                return _unavailable("INVALID_INPUT", index)
            try:
                asset = ResearchAssetClass(selected.asset_class)
            except (ValueError, TypeError):
                return _unavailable("INVALID_INPUT", index)

            if reference_class is None:
                reference_class = asset
                reference_exchange = selected.option_exchange
                reference_family = selected.product_family_id
                reference_underlying = selected.underlying_contract_id
            elif (
                asset != reference_class
                or selected.option_exchange != reference_exchange
                or selected.product_family_id != reference_family
                or (
                    asset is not ResearchAssetClass.FUTURES_OPTION
                    and selected.underlying_contract_id != reference_underlying
                )
            ):
                return _unavailable("UNSUPPORTED_COMBINATION", index)

            opt_key = (selected.option_exchange, selected.option_symbol)
            if opt_key in seen_options:
                return _unavailable("DUPLICATE_LEG", index)
            seen_options.add(opt_key)
            option_keys.add(opt_key)

            quote_key = (selected.underlying_quote_exchange, selected.underlying_quote_symbol)
            canonical_id = selected.underlying_contract_id
            if canonical_id in aliases_by_id and aliases_by_id[canonical_id] != quote_key:
                return _unavailable("UNDERLYING_ALIAS_CONFLICT", index)
            if quote_key in ids_by_quote and ids_by_quote[quote_key] != canonical_id:
                return _unavailable("UNDERLYING_ALIAS_CONFLICT", index)
            aliases_by_id[canonical_id] = quote_key
            ids_by_quote[quote_key] = canonical_id
            underlying_keys.add(quote_key)

        options, option_error = _load_quotes(
            request.option_quotes, request.trade_date, request.read_at,
        )
        if option_error is not None or options is None:
            return _unavailable(option_error or "INVALID_INPUT")
        underlyings, under_error = _load_quotes(
            request.underlying_quotes, request.trade_date, request.read_at,
        )
        if under_error is not None or underlyings is None:
            return _unavailable(under_error or "INVALID_INPUT")

        pairs: list[tuple[HistoricalLegSelection, HistoricalQuoteRow, HistoricalQuoteRow]] = []
        for index, selected in enumerate(request.selections):
            opt = options.get((selected.option_exchange, selected.option_symbol))
            if opt is None:
                return _unavailable("MISSING_OPTION_QUOTE", index)
            underlying = underlyings.get(
                (selected.underlying_quote_exchange, selected.underlying_quote_symbol)
            )
            if underlying is None:
                return _unavailable("MISSING_UNDERLYING_QUOTE", index)
            pairs.append((selected, opt, underlying))

        if (set(options) - option_keys) or (set(underlyings) - underlying_keys):
            return _unavailable("UNEXPECTED_QUOTE")

        if reference_class is ResearchAssetClass.FUTURES_OPTION:
            if all(opt.settlement is not None and und.settlement is not None for _, opt, und in pairs):
                basis = HistoricalBasis.SETTLEMENT
                fallback_reason = None
            elif all(opt.close is not None and und.close is not None for _, opt, und in pairs):
                basis = HistoricalBasis.CLOSE_FALLBACK
                fallback_reason = "INCOMPLETE_SETTLEMENT_COVERAGE"
            else:
                return _unavailable("NO_COMMON_PRICE_BASIS")
        else:
            if not all(opt.close is not None and und.close is not None for _, opt, und in pairs):
                return _unavailable("NO_COMMON_PRICE_BASIS")
            basis = HistoricalBasis.CLOSE
            fallback_reason = None

        resolved: list[HistoricalResolvedLeg] = []
        for selected, opt, underlying in pairs:
            opt_price = opt.settlement if basis is HistoricalBasis.SETTLEMENT else opt.close
            und_price = underlying.settlement if basis is HistoricalBasis.SETTLEMENT else underlying.close
            if opt_price is None or und_price is None:
                return _unavailable("NO_COMMON_PRICE_BASIS")
            resolved.append(HistoricalResolvedLeg(
                option_symbol=selected.option_symbol,
                option_exchange=selected.option_exchange,
                underlying_contract_id=selected.underlying_contract_id,
                underlying_quote_symbol=selected.underlying_quote_symbol,
                underlying_quote_exchange=selected.underlying_quote_exchange,
                product_family_id=selected.product_family_id,
                asset_class=ResearchAssetClass(selected.asset_class),
                mapping_provenance=selected.mapping_provenance,
                option_price=float(opt_price),
                underlying_price=float(und_price),
                option_source=opt.source,
                underlying_source=underlying.source,
                option_ingested_at=opt.ingested_at,
                underlying_ingested_at=underlying.ingested_at,
            ))

        return HistoricalBasisResult(
            status=ResearchValuationStatus.OK,
            price_basis=basis,
            fallback_reason=fallback_reason,
            trade_date=request.trade_date,
            read_at=request.read_at,
            legs=tuple(resolved),
        )
