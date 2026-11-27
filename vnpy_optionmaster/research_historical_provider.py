"""D12 T06 historical-only ClickHouse provider (explicit bounded transport).

No default connection, secret, service, live subscription, write or scheduler.
The operator supplies a finite dated scope, reviewed terms/aliases and a
trusted transport that enforces actual server limits and client deadlines.
A fixture is always labelled SYNTHETIC_FIXTURE. Historical data are frozen
once, not refreshed from the calculation/HTTP path. No PIT, full-day or
cross-table transaction claim. The former archive provider is unchanged.
"""
from collections import defaultdict
from collections.abc import Callable, Mapping, Sequence
from copy import deepcopy
from dataclasses import asdict, dataclass
from datetime import date, datetime, timezone
from hashlib import sha256
import json
from math import isfinite
import re
from threading import Lock
from time import monotonic
from typing import Literal, Protocol

from vnpy_optionmaster.research_strategy_api_dto import (
    CapabilityDTO, ChainRowDTO, CoverageDayDTO, CoverageMonthDTO,
    CoverageProductDTO, DomainDTO, OptionQuoteDTO, Reason, SnapshotDTO,
    StrategyChainDTO, StrategyCoverageDTO, StrategyRateEvidenceDTO,
    UnderlyingQuoteDTO, ValuationRequestDTO,
)
from vnpy_optionmaster.research_strategy_payoff import StrategyQuoteSnapshot, StrategyUnavailable

CAPS = {
    "readonly": "1", "max_rows_to_read": "100000", "max_bytes_to_read": "16777216",
    "max_execution_time": "5", "max_memory_usage": "67108864",
    "max_result_rows": "64", "max_result_bytes": "1048576",
    "read_overflow_mode": "throw", "result_overflow_mode": "throw",
    "timeout_overflow_mode": "throw",
}
PROFILE_SQL = """SELECT hostName() AS server_name, currentUser() AS reader,
       currentDatabase() AS database, name, value
FROM system.settings
WHERE name IN ('readonly','max_rows_to_read','max_bytes_to_read','max_execution_time',
               'max_memory_usage','max_result_rows','max_result_bytes',
               'read_overflow_mode','result_overflow_mode','timeout_overflow_mode')
ORDER BY name LIMIT 12 FORMAT JSON"""
# Filter the physical sorting-key prefix first; no source/version/chain filter
# may hide a conflict for one selected option key.
TERMS_SQL = """SELECT symbol, exchange, trade_date, portfolio_code, chain_id,
       underlying_symbol, expiry_date, option_type, exercise_style, strike,
       contract_size, contract_version, source, provenance, ingested_at
FROM quantfeed.option_contract_terms_daily
WHERE portfolio_code={portfolio:String} AND trade_date={day:Date}
  AND symbol IN {symbols:Array(String)}
ORDER BY portfolio_code,trade_date,chain_id,symbol LIMIT 64 FORMAT JSON"""
OPTION_SQL = """SELECT symbol, exchange, trade_date, close, settlement, source, ingested_at
FROM quantfeed.option_daily
WHERE symbol IN {symbols:Array(String)} AND trade_date={day:Date}
ORDER BY symbol,trade_date LIMIT 64 FORMAT JSON"""
FUTURES_SQL = """SELECT symbol, exchange, trade_date, product_symbol, close, settlement,
       price_source, settlement_source, ingested_at
FROM quantfeed.futures_daily
WHERE product_symbol={product:String} AND trade_date={day:Date}
  AND symbol={symbol:String}
LIMIT 2 FORMAT JSON"""
CATALOG_SQL = """SELECT trade_date, chain_id, underlying_symbol, count() AS physical_rows
FROM quantfeed.option_contract_terms_daily
WHERE portfolio_code={portfolio:String} AND trade_date BETWEEN {start:Date} AND {end:Date}
GROUP BY trade_date,chain_id,underlying_symbol
ORDER BY trade_date,chain_id,underlying_symbol LIMIT 64 FORMAT JSON"""
_TABLES = {TERMS_SQL: "option_contract_terms_daily", OPTION_SQL: "option_daily",
           FUTURES_SQL: "futures_daily", CATALOG_SQL: "option_contract_terms_daily"}
_TERM_FIELDS = frozenset("symbol exchange trade_date portfolio_code chain_id underlying_symbol expiry_date option_type exercise_style strike contract_size contract_version source provenance ingested_at".split())
_OPTION_FIELDS = frozenset("symbol exchange trade_date close settlement source ingested_at".split())
_FUTURE_FIELDS = frozenset("symbol exchange trade_date product_symbol close settlement price_source settlement_source ingested_at".split())
_CHUNK = 63
_MAX_SCOPES = 8
_MAX_REQUESTS = 64  # engineering ceiling, NOT a granted request budget
_MAX_SECONDS = 120.0
_ELIGIBLE = CapabilityDTO(status="ELIGIBLE", reason=None)


def _guard(ok: bool, reason: Reason = "SOURCE_VERSION_UNVERIFIED") -> None:
    if not ok:
        raise StrategyUnavailable(reason)


def _digest(value: object) -> str:
    return sha256(json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True,
                             allow_nan=False).encode()).hexdigest()


def _pin(value: str) -> bool:
    return re.fullmatch(r"[0-9a-f]{64}", value) is not None


def _token(value: object) -> bool:
    # Also keeps typed CH parameter escaping unambiguous.
    return isinstance(value, str) and re.fullmatch(r"[A-Za-z0-9_.|:+-]{1,128}", value) is not None


def _day(value: str) -> date:
    try:
        d = date.fromisoformat(value)
        _guard(d.isoformat() == value, "INVALID_REQUEST")
        return d
    except ValueError as exc:
        raise StrategyUnavailable("INVALID_REQUEST") from exc


def _aware(value: str) -> datetime:
    try:
        stamp = datetime.fromisoformat(value)
        _guard(stamp.utcoffset() is not None)
        return stamp
    except (ValueError, TypeError) as exc:
        raise StrategyUnavailable("SOURCE_VERSION_UNVERIFIED") from exc


def _number(value: object) -> float | None:
    if not isinstance(value, (int, float)) or isinstance(value, bool):
        return None
    try:
        return float(value) if isfinite(value) else None
    except OverflowError:
        return None


def _uint(value: object) -> int:
    if type(value) is int:
        n = int(value)
    elif isinstance(value, str) and value.isascii() and value.isdecimal() and len(value) <= 20:
        n = int(value)
    else:
        raise StrategyUnavailable("SOURCE_VERSION_UNVERIFIED")
    _guard(n >= 0)
    return n


def _unavailable(reason: Reason) -> CapabilityDTO:
    return CapabilityDTO(status="UNAVAILABLE", reason=reason)


def _in_domain(price: float, domain: DomainDTO) -> bool:
    return (domain.status == "VERIFIED"
            and (domain.min_price is None or price > domain.min_price or price == domain.min_price and domain.min_inclusive)
            and (domain.max_price is None or price < domain.max_price or price == domain.max_price and domain.max_inclusive))


@dataclass(frozen=True)
class HistoricalReadReceipt:
    """Transport returns complete typed rows, raw-body digest and actual stats.

    The trusted transport must enforce the timeout and same authenticated
    server/account/session, not synthesize these fields from requested caps.
    This protocol intentionally does not read credentials or open sockets.
    """
    target: str
    server: str
    account: str
    query_id: str
    observed_at: str
    rows: tuple[dict[str, object], ...]
    scan_rows: int
    scan_bytes: int
    response_bytes: int
    elapsed_seconds: float
    body_sha256: str


class HistoricalSelectTransport(Protocol):
    def select(self, sql: str, parameters: Mapping[str, str], *,
               timeout_seconds: float) -> HistoricalReadReceipt: ...


class _Batch:
    def __init__(self, transport: HistoricalSelectTransport, target: str, server: str,
                 account: str, max_requests: int) -> None:
        _guard(type(max_requests) is int and 0 <= max_requests <= _MAX_REQUESTS, "INVALID_REQUEST")
        _guard(bool(target) and bool(server) and bool(account), "INVALID_REQUEST")
        self.transport = transport
        self.target, self.server, self.account = target, server, account
        self.maximum = max_requests
        self.attempts = 0
        self.start = monotonic()
        self.receipts: list[HistoricalReadReceipt] = []
        self.query_ids: set[str] = set()

    def _call(self, sql: str, params: Mapping[str, str]) -> tuple[dict[str, object], ...]:
        _guard(self.attempts < self.maximum and monotonic() - self.start <= _MAX_SECONDS - 8,
               "DEADLINE_EXCEEDED")
        self.attempts += 1  # charge before transport; caller never auto-retries
        start = monotonic()
        try:
            receipt = self.transport.select(sql, dict(params), timeout_seconds=8.0)
        except Exception as exc:
            raise StrategyUnavailable("SOURCE_INCOMPLETE") from exc
        _guard(monotonic() - start <= 8 and monotonic() - self.start <= _MAX_SECONDS, "DEADLINE_EXCEEDED")
        _guard(isinstance(receipt, HistoricalReadReceipt)
               and (receipt.target, receipt.server, receipt.account) == (self.target, self.server, self.account)
               and _token(receipt.query_id) and receipt.query_id not in self.query_ids
               and _pin(receipt.body_sha256))
        _aware(receipt.observed_at)
        _guard(type(receipt.scan_rows) is int and 0 <= receipt.scan_rows <= 100000
               and type(receipt.scan_bytes) is int and 0 <= receipt.scan_bytes <= 16777216
               and type(receipt.response_bytes) is int and 0 < receipt.response_bytes <= 1048576
               and _number(receipt.elapsed_seconds) is not None and 0 <= receipt.elapsed_seconds <= 8
               and isinstance(receipt.rows, tuple) and len(receipt.rows) <= 64
               and all(isinstance(row, dict) for row in receipt.rows))
        self.query_ids.add(receipt.query_id)
        # Detach data owned by the transport; no mutable alias to stored evidence.
        self.receipts.append(deepcopy(receipt))
        return deepcopy(receipt.rows)

    def seal(self) -> None:
        # Concrete durable transports close any unused authorized tail on
        # consumer rejection. Fixture/legacy transport needs no journal.
        close = getattr(self.transport, "seal", None)
        if callable(close):
            close()

    def profile(self) -> None:
        rows = self._call(PROFILE_SQL, {})
        _guard(len(rows) == len(CAPS)
               and all(set(row) == {"name", "value", "server_name", "reader", "database"} for row in rows)
               and all(row["server_name"] == self.server and row["reader"] == self.account
                       and row["database"] == "quantfeed" and isinstance(row["name"], str) for row in rows))
        settings = {str(row["name"]): str(row["value"]) for row in rows}
        _guard(len(settings) == len(CAPS) and settings == CAPS)

    def read(self, sql: str, params: Mapping[str, str]) -> tuple[dict[str, object], ...]:
        _guard(sql in _TABLES, "INVALID_REQUEST")
        plan = self._call("EXPLAIN ESTIMATE " + sql, params)
        _guard(len(plan) == 1 and plan[0].get("database") == "quantfeed"
               and plan[0].get("table") == _TABLES[sql])
        _guard(_uint(plan[0].get("rows")) <= 100000, "SOURCE_INCOMPLETE")
        _uint(plan[0].get("parts"))
        _uint(plan[0].get("marks"))
        rows = self._call(sql, params)
        _guard(len(rows) < (2 if sql == FUTURES_SQL else 64), "SOURCE_AMBIGUOUS")
        return rows


@dataclass(frozen=True)
class CatalogWindow:
    exchange: str
    product: str
    portfolio_code: str
    start_date: str
    end_date: str


@dataclass(frozen=True)
class CatalogCandidate:
    exchange: str
    product: str
    portfolio_code: str
    trade_date: str
    chain_id: str
    vendor_underlying: str
    physical_terms_rows: int
    coverage: Literal["COVERAGE_UNKNOWN"] = "COVERAGE_UNKNOWN"


def discover_history(window: CatalogWindow, *, transport: HistoricalSelectTransport,
                     target: str, server: str, account: str,
                     max_requests: int) -> tuple[CatalogCandidate, ...]:
    """Three fixed queries for one explicit portfolio/date window.

    A catalog row is only observed terms, not unique contracts or quote coverage.
    No broad product enumeration, automatic widening or subsequent dispatch.
    """
    start, end = _day(window.start_date), _day(window.end_date)
    plan = catalog_query_plan(window)
    batch = _Batch(transport, target, server, account, max_requests)
    _guard(max_requests >= len(plan), "SOURCE_INCOMPLETE")
    try:
        batch.profile()
        rows = batch.read(CATALOG_SQL, {"portfolio": window.portfolio_code,
                                        "start": window.start_date, "end": window.end_date})
    finally:
        batch.seal()
    candidates: list[CatalogCandidate] = []
    seen: set[tuple[str, str, str]] = set()
    for row in rows:
        _guard(set(row) == {"trade_date", "chain_id", "underlying_symbol", "physical_rows"})
        day, chain, vendor = str(row["trade_date"]), str(row["chain_id"]), str(row["underlying_symbol"])
        key = (day, chain, vendor)
        count = _uint(row["physical_rows"])
        _guard(start <= _day(day) <= end and _token(chain) and _token(vendor) and count > 0
               and key not in seen, "SOURCE_AMBIGUOUS")
        seen.add(key)
        candidates.append(CatalogCandidate(window.exchange, window.product, window.portfolio_code,
                                           day, chain, vendor, count))
    return tuple(sorted(candidates, key=lambda x: (x.trade_date, x.chain_id, x.vendor_underlying)))


@dataclass(frozen=True)
class ReviewedOption:
    """Exact dated terms already selected by the trusted operator registry."""
    symbol: str
    option_type: Literal["CALL", "PUT"]
    strike: float
    multiplier: float
    expiry_date: str
    exercise_style: Literal["AMERICAN", "EUROPEAN"]
    terms_version: str
    expiry_at: str | None = None
    # Exact raw vendor values may differ from the independently reviewed
    # economic terms (older JQ rows have UNKNOWN/empty). Only a trusted
    # explicitly pinned dated binding may supply these raw expectations.
    raw_exercise_style: str | None = None
    raw_contract_version: str | None = None
    binding_pin: str | None = None


@dataclass(frozen=True)
class ChainScope:
    """Finite reviewed historical slice, not a claim of full-day coverage.

    policy_pin identifies the external reviewed registry/alias/unit policy.
    reference_at is an explicit reviewed research clock (may be nominal).
    No source names, contract mapping, prices, rates or units inferred from
    instrument-code spelling. The provider never changes prior D02/D09 pins.
    """
    exchange: str
    product: str
    trade_date: str
    underlying_id: str
    delivery_month: str
    portfolio_code: str
    chain_id: str
    vendor_underlying: str
    vendor_exchange: str
    futures_product: str
    futures_exchange: str
    option_source: str
    future_price_source: str
    future_settlement_source: str
    multiplier: float
    quote_unit: str
    underlying_terms_version: str
    policy_pin: str
    reference_at: str
    terms_provenance: tuple[str, ...]
    options: tuple[ReviewedOption, ...]
    price_domain: DomainDTO
    rates: tuple[StrategyRateEvidenceDTO, ...] = ()


def _validate_scope(s: ChainScope) -> None:
    _day(s.trade_date)
    _guard(all(_token(x) for x in (s.exchange, s.product, s.underlying_id, s.delivery_month,
               s.portfolio_code, s.chain_id, s.vendor_underlying, s.vendor_exchange, s.futures_product,
               s.futures_exchange, s.option_source, s.future_price_source, s.future_settlement_source,
               s.quote_unit, s.underlying_terms_version, *s.terms_provenance))
           and _pin(s.policy_pin) and bool(s.terms_provenance)
           and _number(s.multiplier) is not None and s.multiplier > 0
           and s.price_domain.status == "VERIFIED", "INVALID_REQUEST")
    _guard(s.reference_at[:10] == s.trade_date, "INVALID_REQUEST")
    ValuationRequestDTO.market_clock(s.reference_at)
    _guard(1 <= len(s.options) <= 128 and len({o.symbol for o in s.options}) == len(s.options)
           and len({(o.strike, o.option_type) for o in s.options}) == len(s.options)
           and len({o.strike for o in s.options}) <= 64
           and len({o.expiry_date for o in s.options}) == 1, "INVALID_REQUEST")
    for o in s.options:
        _guard(_token(o.symbol) and _token(o.terms_version) and o.symbol != s.underlying_id
               and o.option_type in ("CALL", "PUT") and o.exercise_style in ("AMERICAN", "EUROPEAN")
               and _number(o.strike) is not None and o.strike >= 0
               and o.multiplier == s.multiplier, "INVALID_REQUEST")
        if any(x is not None for x in (o.raw_exercise_style, o.raw_contract_version, o.binding_pin)):
            _guard(o.binding_pin == s.policy_pin and o.raw_exercise_style in (o.exercise_style, "UNKNOWN")
                   and o.raw_contract_version is not None
                   and (o.raw_contract_version == "" or _token(o.raw_contract_version)),
                   "SOURCE_VERSION_UNVERIFIED")
        _day(o.expiry_date)
        if o.expiry_at is not None:
            ValuationRequestDTO.market_clock(o.expiry_at)
            _guard(o.expiry_at[:10] == o.expiry_date, "INVALID_REQUEST")
    expiry_id = s.underlying_id + "|" + s.options[0].expiry_date
    _guard(len(s.rates) <= 1 and len(expiry_id) <= 128, "INVALID_REQUEST")
    for r in s.rates:
        _guard(r.expiry_id == expiry_id and _day(r.quote_date) <= _day(s.trade_date)
               and _pin(r.source_pin), "INVALID_REQUEST")


@dataclass(frozen=True)
class HistoricalQuery:
    sql: str
    parameters: tuple[tuple[str, str], ...]


def _query(sql: str, params: Mapping[str, str]) -> HistoricalQuery:
    return HistoricalQuery(sql, tuple(sorted(params.items())))


def catalog_query_plan(window: CatalogWindow) -> tuple[HistoricalQuery, ...]:
    """Freeze the exact plan; a plan is not an authorization."""
    start, end = _day(window.start_date), _day(window.end_date)
    _guard(all(_token(x) for x in (window.exchange, window.product, window.portfolio_code))
           and 0 <= (end - start).days <= 30, "INVALID_REQUEST")
    params = {"portfolio": window.portfolio_code, "start": window.start_date, "end": window.end_date}
    return (_query(PROFILE_SQL, {}), _query("EXPLAIN ESTIMATE " + CATALOG_SQL, params), _query(CATALOG_SQL, params))


def history_query_plan(scopes: Sequence[ChainScope]) -> tuple[HistoricalQuery, ...]:
    """Freeze exact statements and parameters before any actual read approval."""
    _guard(1 <= len(scopes) <= _MAX_SCOPES, "INVALID_REQUEST")
    keys: set[tuple[str, str, str, str]] = set()
    option_keys: set[tuple[str, str]] = set()
    clocks: dict[tuple[str, str, str], str] = {}
    queries = [_query(PROFILE_SQL, {})]
    for s in scopes:
        _validate_scope(s)
        day_key = (s.exchange, s.product, s.trade_date)
        key = (*day_key, s.underlying_id)
        _guard(key not in keys and clocks.get(day_key, s.reference_at) == s.reference_at, "SOURCE_AMBIGUOUS")
        keys.add(key)
        clocks[day_key] = s.reference_at
        for option in s.options:
            option_key = (s.trade_date, option.symbol)
            _guard(option_key not in option_keys, "SOURCE_AMBIGUOUS")
            option_keys.add(option_key)
        symbols = sorted(o.symbol for o in s.options)
        for offset in range(0, len(symbols), _CHUNK):
            params = {"day": s.trade_date, "symbols": repr(symbols[offset:offset + _CHUNK])}
            for sql, args in ((TERMS_SQL, {**params, "portfolio": s.portfolio_code}), (OPTION_SQL, params)):
                queries.extend((_query("EXPLAIN ESTIMATE " + sql, args), _query(sql, args)))
        future = {"day": s.trade_date, "product": s.futures_product, "symbol": s.underlying_id}
        queries.extend((_query("EXPLAIN ESTIMATE " + FUTURES_SQL, future), _query(FUTURES_SQL, future)))
    _guard(len(queries) <= _MAX_REQUESTS, "INVALID_REQUEST")
    return tuple(queries)


def _row_map(rows: Sequence[dict[str, object]], fields: frozenset[str],
             symbols: set[str], day: str) -> dict[str, dict[str, object]]:
    result: dict[str, dict[str, object]] = {}
    for row in rows:
        sym = row.get("symbol")
        _guard(set(row) == fields and isinstance(sym, str) and sym in symbols
               and row.get("trade_date") == day and isinstance(row.get("ingested_at"), str)
               and bool(row.get("ingested_at")), "SOURCE_AMBIGUOUS")
        key = str(sym)
        _guard(key not in result, "SOURCE_AMBIGUOUS")
        result[key] = row
    return result


def _matches_terms(s: ChainScope, o: ReviewedOption, row: dict[str, object]) -> bool:
    return (row["exchange"] == s.vendor_exchange and row["portfolio_code"] == s.portfolio_code
            and row["chain_id"] == s.chain_id and row["underlying_symbol"] == s.vendor_underlying
            and row["expiry_date"] == o.expiry_date and row["option_type"] == o.option_type
            and row["exercise_style"] == (o.raw_exercise_style if o.binding_pin else o.exercise_style)
            and _number(row["strike"]) == o.strike
            and _number(row["contract_size"]) == o.multiplier
            and row["contract_version"] == (o.raw_contract_version if o.binding_pin else o.terms_version)
            and row["source"] == s.option_source and row["provenance"] in s.terms_provenance)


@dataclass(frozen=True)
class _ChainMaterialization:
    scope: ChainScope
    underlying: UnderlyingQuoteDTO
    options: tuple[OptionQuoteDTO, ...]
    coverage: CoverageMonthDTO
    details: dict[str, object]


def _chain(batch: _Batch, s: ChainScope) -> _ChainMaterialization:
    terms: dict[str, dict[str, object]] = {}
    quotes: dict[str, dict[str, object]] = {}
    symbols = sorted(o.symbol for o in s.options)
    for offset in range(0, len(symbols), _CHUNK):
        chunk = symbols[offset:offset + _CHUNK]
        params = {"day": s.trade_date, "symbols": repr(chunk)}
        tr = batch.read(TERMS_SQL, {**params, "portfolio": s.portfolio_code})
        terms.update(_row_map(tr, _TERM_FIELDS, set(chunk), s.trade_date))
        qr = batch.read(OPTION_SQL, params)
        quotes.update(_row_map(qr, _OPTION_FIELDS, set(chunk), s.trade_date))
    fr = batch.read(FUTURES_SQL, {"day": s.trade_date, "product": s.futures_product, "symbol": s.underlying_id})
    future = _row_map(fr, _FUTURE_FIELDS, {s.underlying_id}, s.trade_date).get(s.underlying_id)
    mark = None
    if (future is not None and future["exchange"] == s.futures_exchange
            and future["product_symbol"] == s.futures_product and future["price_source"] == s.future_price_source
            and future["settlement_source"] == s.future_settlement_source):
        value = _number(future["settlement"])
        if value is not None and _in_domain(value, s.price_domain):
            mark = value
    u = UnderlyingQuoteDTO(underlying_id=s.underlying_id, kind="FUTURE", delivery_month=s.delivery_month,
        market_price=mark, multiplier=s.multiplier, quantity_unit="CONTRACTS", quote_unit=s.quote_unit,
        terms_version=s.underlying_terms_version, domain=s.price_domain,
        eligibility=_ELIGIBLE if mark is not None else _unavailable("MARK_UNAVAILABLE"))
    out: list[OptionQuoteDTO] = []
    missing: dict[str, str] = {}
    for o in sorted(s.options, key=lambda x: x.symbol):
        t, q = terms.get(o.symbol), quotes.get(o.symbol)
        if t is None or not _matches_terms(s, o, t):
            missing[o.symbol] = "TERMS_UNAVAILABLE"
            continue  # never fabricate a terms row from the review roster
        price = None
        if q is not None and q["exchange"] == s.vendor_exchange and q["source"] == s.option_source:
            candidate = _number(q["settlement"])
            if candidate is not None and candidate >= 0:
                price = candidate
        reason: Reason | None = ("MARK_UNAVAILABLE" if price is None or mark is None
                                else "EXPIRED_SCENARIO" if s.trade_date >= o.expiry_date else None)
        if reason is not None:
            missing[o.symbol] = reason
        if reason is not None:
            model = _unavailable(reason)
        elif o.expiry_at is None:
            model = _unavailable("HORIZON_UNAVAILABLE")
        elif not s.rates:
            model = _unavailable("RATE_UNAVAILABLE")
        else:
            model = CapabilityDTO(status="NOT_EVALUATED", reason="NOT_EVALUATED")
        out.append(OptionQuoteDTO(contract_id=o.symbol, underlying_id=s.underlying_id, option_type=o.option_type,
            strike=o.strike, expiry_id=s.underlying_id + "|" + o.expiry_date, expiry_date=o.expiry_date,
            expiry_at=o.expiry_at, expiry_clock="APPROVED_NOMINAL" if o.expiry_at else "DATE_ONLY",
            exercise_style=o.exercise_style, model="AMERICAN_BINOMIAL" if o.exercise_style == "AMERICAN" else "BLACK_76",
            multiplier=o.multiplier, quote_unit=s.quote_unit, terms_version=o.terms_version, market_price=price,
            implied_volatility=None, payoff_eligibility=_ELIGIBLE if reason is None else _unavailable(reason),
            model_eligibility=model))
    admitted = sum(o.payoff_eligibility.status == "ELIGIBLE" for o in out)
    full = admitted == len(s.options)
    coverage = CoverageMonthDTO(underlying_id=s.underlying_id, delivery_month=s.delivery_month,
        discovered_contracts=len(s.options), admitted_contracts=admitted,
        status="ADMITTED" if full else "INCOMPLETE", reason=None if full else "SOURCE_INCOMPLETE")
    details: dict[str, object] = {"exchange": s.exchange, "product": s.product, "trade_date": s.trade_date,
        "underlying_id": s.underlying_id, "coverage": "COVERAGE_UNKNOWN" if full else "PARTIAL",
        "expected_reviewed_options": len(s.options), "observed_terms": len(terms),
        "observed_quotes": len(quotes), "eligible_options": admitted, "missing_or_invalid": missing,
        "fresh_underlying": mark is not None, "price_basis": "SETTLEMENT",
        "full_day_verified": False}
    return _ChainMaterialization(s, u, tuple(out), coverage, details)


def _copy_snapshot(source: StrategyQuoteSnapshot) -> StrategyQuoteSnapshot:
    return StrategyQuoteSnapshot(source.metadata.model_copy(deep=True),
        tuple(q.model_copy(deep=True) for q in source.options),
        tuple(u.model_copy(deep=True) for u in source.underlyings),
        tuple(r.model_copy(deep=True) for r in source.rates))


class HistoricalClickHouseProvider:
    """Explicit one-shot historical observation; implements the v4 provider seam.

    One bounded batch per instance. No dispatch in constructor, coverage,
    chain, load_snapshot or valuation. A new observation needs a new explicitly
    budgeted instance, never an implicit retry. All configured months for one
    product/date share one frozen snapshot, preserving exact entry identities.
    """
    def __init__(self, scopes: Sequence[ChainScope], *, transport: HistoricalSelectTransport,
                 target: str, server: str, account: str, max_requests: int,
                 fixture: bool, clock: Callable[[], datetime] = lambda: datetime.now(timezone.utc)) -> None:
        self._scopes = tuple(deepcopy(s) for s in scopes)
        _guard(1 <= len(self._scopes) <= _MAX_SCOPES, "INVALID_REQUEST")
        self._plan = history_query_plan(self._scopes)
        self._batch_args = transport, target, server, account, max_requests
        self._fixture = fixture
        self._clock = clock
        self._started = False
        self._lock = Lock()
        self._ids: dict[str, StrategyQuoteSnapshot] = {}
        self._days: dict[tuple[str, str, str], StrategyQuoteSnapshot] = {}
        self._coverage: StrategyCoverageDTO | None = None
        self._details: tuple[dict[str, object], ...] = ()
        self._attempts = 0

    @property
    def attempts(self) -> int:
        return self._attempts

    def observe(self) -> None:
        with self._lock:
            _guard(not self._started, "SNAPSHOT_STALE")
            self._started = True
            batch = _Batch(*self._batch_args)
            _guard(len(self._plan) <= batch.maximum, "SOURCE_INCOMPLETE")
            try:
                batch.profile()
                grouped: dict[tuple[str, str, str], list[_ChainMaterialization]] = defaultdict(list)
                # All receipt digests are retained; never requery when valuing.
                for s in self._scopes:
                    grouped[s.exchange, s.product, s.trade_date].append(_chain(batch, s))
                read_at = self._clock()
                _guard(read_at.utcoffset() is not None)
                observed = min(_aware(r.observed_at) for r in batch.receipts)
                _guard(all(_aware(r.observed_at) <= read_at for r in batch.receipts))
                # One digest covers all consumed read clocks, versions and exact rows.
                receipt_pin = _digest([asdict(r) for r in batch.receipts])
                days: dict[tuple[str, str, str], StrategyQuoteSnapshot] = {}
                coverage: dict[tuple[str, str], list[CoverageDayDTO]] = defaultdict(list)
                for (exchange, product, day), chains in sorted(grouped.items(), reverse=True):
                    scopes = [c.scope for c in chains]
                    _guard(len({s.reference_at for s in scopes}) == 1, "SOURCE_AMBIGUOUS")
                    source_versions = {"READ_RECEIPTS": receipt_pin}
                    for i, s in enumerate(scopes):
                        source_versions[f"POLICY_{i}"] = s.policy_pin
                        for r in s.rates:
                            source_versions[f"RATE_{i}"] = r.source_pin
                    policy = _digest([{
                        "chain": {k: v for k, v in asdict(s).items() if k not in ("rates", "price_domain")},
                        "domain": s.price_domain.model_dump(mode="json"),
                        "rates": [r.model_dump(mode="json") for r in s.rates],
                    } for s in scopes])
                    snapshot_id = _digest({"scope": (exchange, product, day), "policy": policy,
                                           "receipts": receipt_pin, "versions": source_versions,
                                           "fixture": self._fixture})
                    meta = SnapshotDTO(snapshot_id=snapshot_id,
                        provider="SYNTHETIC_FIXTURE" if self._fixture else "ADMITTED_CLICKHOUSE",
                        exchange=exchange, product=product, trade_date=day, observed_at=observed.isoformat(),
                        read_at=read_at.isoformat(), admitted_at=read_at.isoformat(), reference_at=scopes[0].reference_at,
                        source_pin=policy, source_versions=source_versions, price_basis="SETTLEMENT",
                        basis_fallback_reason=None, currency="CNY", production_valuation_ready=False)
                    snap = StrategyQuoteSnapshot(meta, tuple(q for c in chains for q in c.options),
                        tuple(c.underlying for c in chains), tuple(r for s in scopes for r in s.rates))
                    days[exchange, product, day] = snap
                    coverage[exchange, product].append(CoverageDayDTO(trade_date=day,
                        status="INCOMPLETE", reason="SOURCE_INCOMPLETE",
                        months=[c.coverage for c in chains]))
                result = StrategyCoverageDTO(products=[CoverageProductDTO(exchange=k[0], product=k[1],
                    latest_complete_trade_date=None, days=v, spot_status="UNAVAILABLE", spot_reason="SCOPE_UNAVAILABLE")
                    for k, v in sorted(coverage.items())], next_cursor=None)
                self._days = days
                self._ids = {s.metadata.snapshot_id: s for s in days.values()}
                self._details = tuple(c.details for cs in grouped.values() for c in cs)
                self._coverage = result
            finally:
                self._attempts = batch.attempts
                batch.seal()

    def coverage_details(self) -> tuple[dict[str, object], ...]:
        _guard(self._coverage is not None, "SCOPE_UNAVAILABLE")
        return deepcopy(self._details)

    def coverage(self, exchange: str | None, product: str | None, cursor: str | None) -> StrategyCoverageDTO:
        if cursor is not None or (exchange is None) != (product is None):
            raise StrategyUnavailable("INVALID_REQUEST")
        source = self._coverage
        if source is None:
            raise StrategyUnavailable("SCOPE_UNAVAILABLE")
        selected = [p for p in source.products if exchange is None or (p.exchange, p.product) == (exchange, product)]
        _guard(bool(selected), "SCOPE_UNAVAILABLE")
        return StrategyCoverageDTO(products=[p.model_copy(deep=True) for p in selected], next_cursor=None)

    def load_snapshot(self, snapshot_id: str) -> StrategyQuoteSnapshot | None:
        source = self._ids.get(snapshot_id)
        return None if source is None else _copy_snapshot(source)

    def chain(self, exchange: str, product: str, trade_date: str, underlying_id: str,
              snapshot_id: str | None, cursor: str | None) -> StrategyChainDTO:
        _guard(cursor is None, "INVALID_REQUEST")
        source = self._days.get((exchange, product, trade_date))
        if source is None:
            raise StrategyUnavailable("SCOPE_UNAVAILABLE")
        _guard(snapshot_id is None or source.metadata.snapshot_id == snapshot_id, "SNAPSHOT_STALE")
        u = next((u for u in source.underlyings if u.underlying_id == underlying_id), None)
        if u is None:
            raise StrategyUnavailable("SCOPE_UNAVAILABLE")
        by_strike: dict[float, dict[str, OptionQuoteDTO]] = {}
        for q in source.options:
            if q.underlying_id != underlying_id:
                continue
            rights = by_strike.setdefault(q.strike, {})
            _guard(q.option_type not in rights, "SOURCE_AMBIGUOUS")
            rights[q.option_type] = q
        return StrategyChainDTO(snapshot=source.metadata, underlying=u,
            rows=[ChainRowDTO(strike=k, call=v.get("CALL"), put=v.get("PUT"))
                  for k, v in sorted(by_strike.items())], next_cursor=None).model_copy(deep=True)
