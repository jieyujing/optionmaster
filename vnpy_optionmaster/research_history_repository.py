"""M1-D03: bounded read-only historical futures-option materialization.

The external transport is operator supplied and MUST use server-bound query
parameters, the same restricted server/session/profile, complete result rows
and an enforced client deadline. No driver, credentials, live transport, or
production registry are bundled. Queries are fixed SELECTs, not caller SQL.

This stage returns CANDIDATE for a D02/D01-checked diagnostic bundle only.
It NEVER returns valuation-ready data: cross-table consistency, official
calendar and real-data acceptance remain separate M1 gates.
"""
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
from enum import Enum
from time import monotonic
from typing import Protocol

from vnpy_optionmaster.research_combination import ResearchAssetClass
from vnpy_optionmaster.research_contract_terms import (
    HistoricalContractEngine,
    HistoricalContractRegistry,
    HistoricalContractRequest,
    HistoricalTermsRow,
    SourceKey,
    UnderlyingIdentity,
    ValidatedHistoricalContract,
)
from vnpy_optionmaster.research_data_basis import (
    HistoricalBasisEngine,
    HistoricalBasisRequest,
    HistoricalBasisResult,
    HistoricalQuoteRow,
)
from vnpy_optionmaster.research_registry_admission import RegistryApproval, admit_registry
from vnpy_optionmaster.research_risk import ResearchValuationStatus


Row = Mapping[str, object]
_OPTION_NS = "quantfeed.option/"
_FUTURES_NS = "quantfeed.futures/"
_MAX_LEGS = 8
_REQUEST_SECONDS = 20.0
_QUERY_SECONDS = 5.0

# Conservative admission ceilings, not measurements or changes to CH settings.
_PROFILE_CEILINGS = {
    "max_rows_to_read": 100000,
    "max_bytes_to_read": 64 * 1024 * 1024,
    "max_execution_time": 5,
    "max_result_rows": 64,
    "max_result_bytes": 1024 * 1024,
}
_PROFILE_EXACT = {
    "readonly": "1",
    "read_overflow_mode": "throw",
    "result_overflow_mode": "throw",
    "timeout_overflow_mode": "throw",
}

_PROFILE_SQL = """SELECT name, value FROM system.settings
WHERE name IN ('readonly','max_rows_to_read','max_bytes_to_read','max_execution_time',
               'max_result_rows','max_result_bytes','read_overflow_mode',
               'result_overflow_mode','timeout_overflow_mode')
ORDER BY name LIMIT 10"""

# Do not filter exchange/source/version/chain in ways that can hide a conflicting
# row in the selected physical key scope. LIMIT 2 is an ambiguity sentinel.
_TERMS_SQL = """SELECT symbol, exchange, trade_date, portfolio_code, chain_id,
       underlying_symbol, expiry_date, option_type, exercise_style, strike,
       contract_size, contract_version, source, provenance, ingested_at
FROM quantfeed.option_contract_terms_daily
WHERE portfolio_code = {portfolio_code:String} AND trade_date = {trade_date:Date}
  AND symbol = {symbol:String}
LIMIT 2"""
_OPTION_SQL = """SELECT symbol, exchange, trade_date, close, settlement, source, ingested_at
FROM quantfeed.option_daily
WHERE symbol = {symbol:String} AND trade_date = {trade_date:Date}
LIMIT 2"""
_FUTURES_SQL = """SELECT symbol, exchange, trade_date, product_symbol, close, settlement,
       price_source, settlement_source, ingested_at
FROM quantfeed.futures_daily
WHERE product_symbol = {product_symbol:String} AND trade_date = {trade_date:Date}
  AND symbol = {symbol:String}
LIMIT 2"""


class ReadOnlySelect(Protocol):
    """Trusted transport seam; not a database connection implementation."""

    def select(
        self, sql: str, parameters: Mapping[str, str], *, timeout_seconds: float,
    ) -> tuple[Row, ...]: ...


class HistoricalReadStatus(str, Enum):
    CANDIDATE = "CANDIDATE"
    UNAVAILABLE = "UNAVAILABLE"


@dataclass(frozen=True)
class HistoricalFuturesLeg:
    """Explicit lookup keys; never derived from symbol spelling."""

    portfolio_code: str
    chain_id: str
    option_key: SourceKey
    product_symbol: str
    underlying_quote_key: SourceKey
    settlement_source: str


@dataclass(frozen=True)
class HistoricalReadRequest:
    trade_date: date
    legs: tuple[HistoricalFuturesLeg, ...]


@dataclass(frozen=True)
class TermsLineage:
    portfolio_code: str
    chain_id: str
    source: str
    provenance: str


@dataclass(frozen=True)
class FuturesQuoteLineage:
    quote: HistoricalQuoteRow
    product_symbol: str
    price_source: str
    settlement_source: str


@dataclass(frozen=True)
class HistoricalCandidate:
    """Diagnostic only; not a coherent snapshot or valuation admission token."""

    trade_date: date
    read_at: datetime
    registry_sha256: str
    contracts: tuple[ValidatedHistoricalContract, ...]
    terms_lineage: tuple[TermsLineage, ...]
    option_quotes: tuple[HistoricalQuoteRow, ...]
    futures_quotes: tuple[FuturesQuoteLineage, ...]
    basis: HistoricalBasisResult

    @property
    def valuation_ready(self) -> bool:
        return False

    @property
    def consistency(self) -> str:
        return "UNVERIFIED_CROSS_TABLE"


@dataclass(frozen=True)
class HistoricalReadResult:
    status: HistoricalReadStatus
    reason_code: str | None = None
    candidate: HistoricalCandidate | None = None


class _Rejected(Exception):
    pass


def _token(value: object) -> bool:
    return (
        isinstance(value, str) and 0 < len(value) <= 512 and value.isprintable()
        and not any(c.isspace() for c in value)
    )


def _key(value: object, namespace_prefix: str) -> bool:
    return (
        isinstance(value, SourceKey)
        and all(_token(x) for x in (value.namespace, value.exchange, value.symbol))
        and value.namespace.startswith(namespace_prefix)
        and len(value.namespace) > len(namespace_prefix)
    )


def _text(row: Row, name: str, *, blank_allowed: bool = False) -> str:
    value = row.get(name)
    if isinstance(value, str) and (_token(value) or (blank_allowed and value == "")):
        return value
    raise _Rejected("INVALID_SOURCE_ROW")


def _date(value: object) -> date:
    if type(value) is date:
        return value
    if isinstance(value, str) and len(value) == 10:
        try:
            parsed = date.fromisoformat(value)
            if parsed.isoformat() == value:
                return parsed
        except ValueError:
            pass
    raise _Rejected("INVALID_SOURCE_ROW")


def _utc(value: object, *, reason: str = "INVALID_SOURCE_ROW") -> datetime:
    try:
        if isinstance(value, str) and len(value) <= 64:
            value = datetime.fromisoformat(value)
        if isinstance(value, datetime) and value.tzinfo is not None and value.utcoffset() is not None:
            return value.astimezone(timezone.utc)
    except (ValueError, TypeError, OverflowError):
        pass
    raise _Rejected(reason)


def _number(row: Row, name: str, reason: str) -> float | None:
    if name not in row:
        raise _Rejected(reason)
    value = row[name]
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise _Rejected(reason)
    try:
        return float(value)
    except (OverflowError, ValueError):
        raise _Rejected(reason) from None


def _select(
    transport: ReadOnlySelect, sql: str, params: Mapping[str, str], deadline: float, max_rows: int,
) -> tuple[Row, ...]:
    started = monotonic()
    remaining = deadline - started
    if remaining <= 0:
        raise _Rejected("QUERY_BUDGET_EXCEEDED")
    timeout = min(_QUERY_SECONDS, remaining)
    try:
        rows = transport.select(sql, params, timeout_seconds=timeout)
    except Exception:
        # Transport failures may contain credentials or server internals.
        raise _Rejected("QUERY_FAILED") from None
    finished = monotonic()
    if finished > deadline or finished - started > timeout:
        raise _Rejected("QUERY_BUDGET_EXCEEDED")
    if not isinstance(rows, tuple) or len(rows) > max_rows or not all(isinstance(row, Mapping) for row in rows):
        raise _Rejected("INVALID_QUERY_RESULT")
    return rows


def _check_profile(transport: ReadOnlySelect, deadline: float) -> None:
    rows = _select(transport, _PROFILE_SQL, {}, deadline, 10)
    values: dict[str, str] = {}
    for row in rows:
        name, value = row.get("name"), row.get("value")
        if not isinstance(name, str) or not isinstance(value, str) or name in values:
            raise _Rejected("UNSAFE_READ_PROFILE")
        values[name] = value
    if set(values) != set(_PROFILE_CEILINGS) | set(_PROFILE_EXACT):
        raise _Rejected("UNSAFE_READ_PROFILE")
    if any(values[name] != expected for name, expected in _PROFILE_EXACT.items()):
        raise _Rejected("UNSAFE_READ_PROFILE")
    for name, cap in _PROFILE_CEILINGS.items():
        try:
            raw = values[name]
            if len(raw) > 32:
                raise ValueError("oversized setting")
            n = Decimal(raw)
            if not n.is_finite() or not 0 < n <= cap:
                raise ValueError("invalid limit")
            if name != "max_execution_time" and n != n.to_integral_value():
                raise ValueError("fractional count")
        except (ValueError, InvalidOperation):
            raise _Rejected("UNSAFE_READ_PROFILE") from None


def _one(
    transport: ReadOnlySelect, sql: str, params: Mapping[str, str], deadline: float, kind: str,
) -> Row:
    rows = _select(transport, sql, params, deadline, 2)
    if not rows:
        raise _Rejected(f"MISSING_{kind}")
    if len(rows) != 1:
        raise _Rejected(f"DUPLICATE_{kind}")
    return rows[0]


def _check_source(row: Row, key: SourceKey, prefix: str, source_field: str, trade_date: date) -> str:
    source = _text(row, source_field)
    if (
        _text(row, "symbol") != key.symbol
        or _text(row, "exchange") != key.exchange
        or _date(row.get("trade_date")) != trade_date
        or prefix + source != key.namespace
    ):
        raise _Rejected("SOURCE_KEY_CONFLICT")
    return source


def _quote(row: Row, key: SourceKey, trade_date: date, source: str) -> HistoricalQuoteRow:
    return HistoricalQuoteRow(
        key.symbol, key.exchange, trade_date,
        _number(row, "close", "INVALID_QUOTE"), _number(row, "settlement", "INVALID_QUOTE"),
        source, _utc(row.get("ingested_at")),
    )


def _now() -> datetime:
    return datetime.now(timezone.utc)


class HistoricalRepository:
    """Only operator configuration supplies transport, registry and approval.

    One new materialization per request. Nothing is cached across requests.
    No public input can supply SQL, approval pins, or a consistency waiver.
    """

    def __init__(
        self, transport: ReadOnlySelect, registry: HistoricalContractRegistry,
        approval: RegistryApproval | None, artifacts: Mapping[str, bytes],
        *, allowed_settlement_sources: tuple[str, ...] = (), clock: Callable[[], datetime] = _now,
    ) -> None:
        self._transport = transport
        self._registry = registry
        self._approval = approval
        self._artifacts = artifacts
        self._allowed_settlement_sources = allowed_settlement_sources
        self._clock = clock

    def materialize(self, request: HistoricalReadRequest) -> HistoricalReadResult:
        try:
            return self._materialize(request)
        except _Rejected as error:
            return HistoricalReadResult(HistoricalReadStatus.UNAVAILABLE, str(error))

    def _materialize(self, request: HistoricalReadRequest) -> HistoricalReadResult:
        if (
            not isinstance(request, HistoricalReadRequest)
            or type(request.trade_date) is not date
            or not isinstance(request.legs, tuple)
            or not 2 <= len(request.legs) <= _MAX_LEGS
        ):
            raise _Rejected("INVALID_INPUT")
        seen: set[str] = set()
        for leg in request.legs:
            if (
                not isinstance(leg, HistoricalFuturesLeg)
                or not _key(leg.option_key, _OPTION_NS)
                or not _key(leg.underlying_quote_key, _FUTURES_NS)
                or not all(_token(x) for x in (leg.portfolio_code, leg.chain_id, leg.product_symbol, leg.settlement_source))
            ):
                raise _Rejected("INVALID_INPUT")
            # Option table's physical key omits exchange and provider.
            if leg.option_key.symbol in seen:
                raise _Rejected("DUPLICATE_LEG")
            seen.add(leg.option_key.symbol)
        day = request.trade_date
        read_at = _utc(self._clock(), reason="INVALID_INPUT")
        try:
            if day > read_at.astimezone(timezone(timedelta(hours=8))).date():
                raise _Rejected("INVALID_INPUT")
        except (OverflowError, ValueError):
            raise _Rejected("INVALID_INPUT") from None
        admission = admit_registry(self._registry, self._approval, self._artifacts, read_at)
        if admission.registry is None or admission.registry_sha256 is None:
            raise _Rejected(admission.reason_code or "REGISTRY_NOT_APPROVED")
        # Lookup admission precedes even the settings query. It is not a
        # substitute for D02's active-date and exact raw-record checks.
        allowed_sources = self._allowed_settlement_sources
        if not isinstance(allowed_sources, tuple) or not all(_token(source) for source in allowed_sources):
            raise _Rejected("INVALID_SOURCE_POLICY")
        for leg in request.legs:
            if not any(t.option_key == leg.option_key for t in admission.registry.terms):
                raise _Rejected("MISSING_TERMS_EVIDENCE")
            if not any(a.source_key == leg.underlying_quote_key for a in admission.registry.aliases):
                raise _Rejected("MISSING_ALIAS")
            if leg.settlement_source not in allowed_sources:
                raise _Rejected("UNAPPROVED_SETTLEMENT_SOURCE")
        deadline = monotonic() + _REQUEST_SECONDS
        _check_profile(self._transport, deadline)

        contracts: list[ValidatedHistoricalContract] = []
        lineage: list[TermsLineage] = []
        native_options: set[tuple[str, str]] = set()
        by_identity: dict[UnderlyingIdentity, tuple[str, SourceKey, str]] = {}
        by_physical: dict[tuple[str, str], UnderlyingIdentity] = {}
        # Complete all terms/alias validation before any price queries.
        for leg in request.legs:
            row = _one(self._transport, _TERMS_SQL, {
                "portfolio_code": leg.portfolio_code, "trade_date": day.isoformat(),
                "symbol": leg.option_key.symbol,
            }, deadline, "TERMS_ROW")
            source = _check_source(row, leg.option_key, _OPTION_NS, "source", day)
            if _text(row, "portfolio_code") != leg.portfolio_code or _text(row, "chain_id") != leg.chain_id:
                raise _Rejected("SOURCE_KEY_CONFLICT")
            # Source schema exposes no separate underlying exchange column.
            # Only futures options are admitted in this D03 table contract.
            raw = HistoricalTermsRow(
                leg.option_key, day,
                SourceKey(leg.option_key.namespace, leg.option_key.exchange, _text(row, "underlying_symbol")),
                _date(row.get("expiry_date")), _text(row, "option_type"), _text(row, "exercise_style"),
                _number(row, "strike", "INVALID_TERMS_ROW"), _number(row, "contract_size", "INVALID_TERMS_ROW"),
                _text(row, "contract_version", blank_allowed=True), _utc(row.get("ingested_at")),
            )
            resolved = HistoricalContractEngine.evaluate(HistoricalContractRequest(
                day, read_at, leg.option_key, leg.underlying_quote_key, (raw,),
            ), admission.registry)
            contract = resolved.contract
            if contract is None or resolved.status is not ResearchValuationStatus.OK:
                raise _Rejected(resolved.reason_code or "CONTRACT_UNAVAILABLE")
            identity = contract.terms.underlying
            if identity.asset_class is not ResearchAssetClass.FUTURES_OPTION:
                raise _Rejected("UNSUPPORTED_ASSET_CLASS")
            if identity.product_family_id != leg.product_symbol:
                raise _Rejected("SOURCE_KEY_CONFLICT")
            native = (contract.terms.option_exchange, contract.terms.option_contract_code)
            if native in native_options:
                raise _Rejected("DUPLICATE_NATIVE_OPTION")
            native_options.add(native)
            key = (leg.product_symbol, leg.underlying_quote_key, leg.settlement_source)
            physical = (leg.product_symbol, leg.underlying_quote_key.symbol)
            if (
                (identity in by_identity and by_identity[identity] != key)
                or (physical in by_physical and by_physical[physical] != identity)
            ):
                raise _Rejected("UNDERLYING_ALIAS_CONFLICT")
            by_identity[identity] = key
            by_physical[physical] = identity
            contracts.append(contract)
            lineage.append(TermsLineage(leg.portfolio_code, leg.chain_id, source, _text(row, "provenance")))

        option_quotes: list[HistoricalQuoteRow] = []
        futures_quotes: list[FuturesQuoteLineage] = []
        for leg in request.legs:
            row = _one(self._transport, _OPTION_SQL, {
                "trade_date": day.isoformat(), "symbol": leg.option_key.symbol,
            }, deadline, "OPTION_QUOTE")
            source = _check_source(row, leg.option_key, _OPTION_NS, "source", day)
            option_quotes.append(_quote(row, leg.option_key, day, source))

        for product, quote_key, expected_settlement in by_identity.values():
            row = _one(self._transport, _FUTURES_SQL, {
                "product_symbol": product, "trade_date": day.isoformat(), "symbol": quote_key.symbol,
            }, deadline, "UNDERLYING_QUOTE")
            source = _check_source(row, quote_key, _FUTURES_NS, "price_source", day)
            if _text(row, "product_symbol") != product:
                raise _Rejected("SOURCE_KEY_CONFLICT")
            quote = _quote(row, quote_key, day, source)
            settlement_source = _text(row, "settlement_source", blank_allowed=True)
            if settlement_source != expected_settlement and not (quote.settlement is None and settlement_source == ""):
                raise _Rejected("SOURCE_KEY_CONFLICT")
            futures_quotes.append(FuturesQuoteLineage(quote, product, source, settlement_source))

        basis = HistoricalBasisEngine.evaluate(HistoricalBasisRequest(
            day, read_at, tuple(c.selection for c in contracts), tuple(option_quotes),
            tuple(f.quote for f in futures_quotes),
        ))
        if basis.status is not ResearchValuationStatus.OK:
            raise _Rejected(basis.reason_code or "BASIS_UNAVAILABLE")
        return HistoricalReadResult(
            HistoricalReadStatus.CANDIDATE,
            candidate=HistoricalCandidate(
                day, read_at, admission.registry_sha256, tuple(contracts), tuple(lineage),
                tuple(option_quotes), tuple(futures_quotes), basis,
            ),
        )
