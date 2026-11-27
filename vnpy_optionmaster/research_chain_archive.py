"""D08: real dated copper chain discovery and independently pinned selection.

The official archive and bounded LAN observation are evidence, not authority
by themselves. Rebuild the candidate from its exact source bytes; only a
separate operator approval of that candidate authorizes its selected rows.
No fresh DB read, inferred vendor key, default contract version or live use.
"""
from collections.abc import Mapping
from dataclasses import dataclass, replace
from datetime import date, datetime, timedelta, timezone
from math import isfinite
from struct import pack, unpack
from types import MappingProxyType
from typing import Any

from vnpy_optionmaster.research_clickhouse_http import _parse_json, _QUERIES, _safe_profile
from vnpy_optionmaster.research_combination import ResearchAssetClass
from vnpy_optionmaster.research_contract_terms import (
    AliasAttestation, ContractEvidence, EvidenceKind, ExerciseStyle,
    HistoricalContractEngine, HistoricalContractRegistry, HistoricalContractRequest,
    HistoricalTermsRow, SourceKey, UnderlyingIdentity, VerifiedTerms,
)
from vnpy_optionmaster.research_data_basis import (
    HistoricalBasis, HistoricalBasisEngine, HistoricalBasisRequest, HistoricalQuoteRow,
)
from vnpy_optionmaster.research_history_repository import (
    HistoricalFuturesLeg, HistoricalReadRequest, _FUTURES_SQL, _OPTION_SQL, _PROFILE_SQL, _TERMS_SQL,
)
from vnpy_optionmaster.research_registry_admission import RegistryApproval, admit_registry, registry_fingerprint
from vnpy_optionmaster.research_risk import ResearchValuationStatus
from vnpy_optionmaster.research_verified_slice import (
    ReviewedSliceLeg, SliceApproval, SliceResult, SliceStatus, VerifiedHistoricalSlice,
    _canonical, _datetime, _digest, _document, _hash, _same, _utc,
)

DAY = date(2022, 9, 7)
PORTFOLIO = "XSGE.CU2211.XSGE"
CHAIN_ID = "XSGE|CU2211.XSGE|2022-10-25"
SOURCE_SYMBOL = "CU2211.XSGE"
UNDERLYING = "cu2211"
PRODUCT = "SHFE.CU"
FUTURES_SYMBOL = "SHFE.cu2211"
SETTLEMENT_SOURCE = "JOINQUANT_GET_EXTRAS:futures_sett_price"
SCHEMA = "optionmaster.d08.chain_candidate.v1"
OBSERVATION = "chain_observation.json"
OFFICIAL = {
    "option_contract_base.json": (
        "506650cf1935b91844f17722d4d1613fe24ce5097f014dc55a7d516a45182440",
        "https://www.shfe.com.cn/data/busiparamdata/option/ContractBaseInfo20220907.dat",
    ),
    "option_daily.json": (
        "c74d9f74877dd0cacc5ad9252871ddb9e2e1792a48fc573d1ea4e0668c8574e8",
        "https://www.shfe.com.cn/data/tradedata/option/dailydata/kx20220907.dat",
    ),
    "future_contract_base.json": (
        "52712182f3b7bd622bb4bba8dcab5f3bb6d725776c3e2d2958b3646e81c4c512",
        "https://www.shfe.com.cn/data/busiparamdata/future/ContractBaseInfo20220907.dat",
    ),
    "future_daily.json": (
        "bb216d92b83d4973cb85cc54ff95bde2cbaf54fd1142e7d2623eaeade30ddf13",
        "https://www.shfe.com.cn/data/tradedata/future/dailydata/kx20220907.dat",
    ),
    "cu_historical_contract_rule.html": (
        "9ae7dae336c0af122ff01ad92d502ec40f953ec8e7b976663e9cd91e148f168d",
        "https://www.shfe.com.cn/products/option/nonferrousmetal/cu_o/standard_cu_o/202401/t20240103_331328.html",
    ),
}
APPROVED_D04_SYMBOLS = frozenset(("CU2211C61000.XSGE", "CU2211P61000.XSGE"))
PROFILE = {
    "readonly": "1", "max_rows_to_read": "100000", "max_bytes_to_read": "67108864",
    "max_execution_time": "5", "max_result_rows": "64", "max_result_bytes": "1048576",
    "read_overflow_mode": "throw", "result_overflow_mode": "throw", "timeout_overflow_mode": "throw",
}
_EPOCH = datetime(1970, 1, 1, tzinfo=timezone.utc)


@dataclass(frozen=True)
class ChainArchiveScope:
    """Trusted explicit date/month source contract; never supplied by an HTTP request."""

    day: date
    underlying: str
    portfolio: str
    chain_id: str
    source_symbol: str
    futures_symbol: str
    split_symbol: str
    expected_count: int
    official: Mapping[str, tuple[str, str]]
    observation_schema: str
    candidate_schema: str
    quote_page_size: int
    catalog_sql: str | None = None
    calendar_sql: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "official", MappingProxyType(dict(self.official)))


D08_SCOPE = ChainArchiveScope(
    DAY, UNDERLYING, PORTFOLIO, CHAIN_ID, SOURCE_SYMBOL, FUTURES_SYMBOL,
    "CU2211P61000.XSGE", 66, OFFICIAL,
    "optionmaster.d08.bounded_chain_observation.v1", SCHEMA, 8,
)


class ChainEvidenceError(ValueError):
    """Sanitized failure at the candidate boundary."""


def require(ok: object, reason: str = "CHAIN_EVIDENCE_INVALID") -> None:
    if not ok:
        raise ChainEvidenceError(reason)


def _number(value: object) -> bool:
    return type(value) in (int, float) and isfinite(value)  # type: ignore[arg-type]


def _date(value: str) -> str:
    return date(int(value[:4]), int(value[4:6]), int(value[6:8])).isoformat()


def _unique(rows: list[dict[str, Any]], key: str) -> dict[Any, dict[str, Any]]:
    result: dict[Any, dict[str, Any]] = {}
    for row in rows:
        identifier = row[key]
        require(identifier not in result, "DUPLICATE_SOURCE_ROW")
        result[identifier] = row
    return result


def _wire(record: dict[str, Any]) -> tuple[bytes, dict[str, Any]]:
    raw = record["body"].encode("utf-8")
    require(0 < len(raw) <= 1048576 and _hash(raw) == record["body_sha256"], "WIRE_DIGEST_MISMATCH")
    doc = _document(raw)
    require(type(doc["rows"]) is int and doc["rows"] == len(doc["data"]) < 64)
    return raw, doc


def _iso_rows(rows: tuple[Mapping[str, object], ...]) -> list[dict[str, Any]]:
    return [{k: v.isoformat() if isinstance(v, datetime) else v for k, v in row.items()} for row in rows]


def _bits(value: str) -> float:
    require(isinstance(value, str) and value.isdecimal() and 0 <= int(value) < 2**64)
    result = unpack("<d", pack("<Q", int(value)))[0]
    require(isfinite(result))
    return float(result)


def _official_catalog(evidence: Mapping[str, bytes], scope: ChainArchiveScope = D08_SCOPE) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    day = scope.day.strftime("%Y%m%d")
    for name, (digest, _) in scope.official.items():
        require(_hash(evidence[name]) == digest, "OFFICIAL_DIGEST_MISMATCH")
    documents = {name: _document(evidence[name]) for name in scope.official if name.endswith(".json")}
    require(all(d["report_date"] == day for d in documents.values()))
    bases = _unique([
        r for r in documents["option_contract_base.json"]["OptionContractBaseInfo"]
        if r["COMMODITYID"].strip() == "cu" and r["EXCHANGEID"] == "SHFE"
    ], "INSTRUMENTID")
    daily = documents["option_daily.json"]["o_curinstrument"]
    daily = [r for r in daily if r["PRODUCTGROUPID"].strip() == "cu" and r["INSTRUMENTID"].strip() != "小计"]
    daily = [{**r, "INSTRUMENTID": r["INSTRUMENTID"].strip()} for r in daily]
    quotes = _unique(daily, "INSTRUMENTID")
    require(set(bases) == set(quotes), "OFFICIAL_UNIVERSE_MISMATCH")
    future_base = _unique(documents["future_contract_base.json"]["ContractBaseInfo"], "INSTRUMENTID")
    futures = _unique([
        {**r, "DELIVERYMONTH": r["DELIVERYMONTH"].strip()}
        for r in documents["future_daily.json"]["o_curinstrument"]
        if r["PRODUCTID"].strip() == "cu_f" and r["DELIVERYMONTH"].strip().isdigit()
    ], "DELIVERYMONTH")
    out = []
    for native, base in bases.items():
        q = quotes[native]
        underlying = q["UNDERLYINGINSTRID"].strip()
        require(underlying in future_base and base["TRADINGDAY"] == day)
        require(q["OPTIONSTYPE"] in ("1", "2") and _number(q["STRIKEPRICE"]) and q["STRIKEPRICE"] > 0)
        # Delivery month comes from the explicit underlying of the official
        # option daily row and exact official futures contract record.
        month = underlying.removeprefix("cu")
        require(underlying == "cu" + month and month in futures)
        future = futures[month]
        require(future_base[underlying]["TRADINGDAY"] == day)
        require(_number(q["CLOSEPRICE"]) and _number(q["SETTLEMENTPRICE"]))
        out.append({
            "native_symbol": native, "base_record_id": base["id"],
            "underlying_native": underlying, "delivery_month": "20" + month[:2] + "-" + month[2:],
            "option_type": "CALL" if q["OPTIONSTYPE"] == "1" else "PUT",
            "strike": float(q["STRIKEPRICE"]), "multiplier": float(base["TRADEUNIT"]),
            "expiry": _date(base["EXPIREDATE"]), "listed": _date(base["OPENDATE"]),
            # The earlier CU2210 rule differs. Discovery is not a style attestation.
            "exercise_style": "AMERICAN" if underlying == scope.underlying and date.fromisoformat("20" + month[:2] + "-" + month[2:] + "-01") >= date(2022, 11, 1) else "UNREVIEWED",
            "close": q["CLOSEPRICE"], "settlement": q["SETTLEMENTPRICE"],
            "volume": q["VOLUME"], "open_interest": q["OPENINTEREST"],
            "underlying_close": future["CLOSEPRICE"], "underlying_settlement": future["SETTLEMENTPRICE"],
        })
    require(len({(r["underlying_native"], r["option_type"], r["strike"]) for r in out}) == len(out))
    return sorted(out, key=lambda r: (r["underlying_native"], r["strike"], r["option_type"])), futures


def _observed(evidence: Mapping[str, bytes], scope: ChainArchiveScope = D08_SCOPE) -> tuple[dict[str, Any], list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]], dict[str, Any], dict[str, Any]]:
    day = scope.day.isoformat()
    obs = _document(evidence[OBSERVATION])
    require(obs["schema"] == scope.observation_schema and obs["status"] == "OBSERVED")
    require(obs["scope"] == "LAN_HTTP_DIAGNOSTIC_NOT_TLS_PRODUCTION" and obs["trade_date"] == day)
    require(obs["portfolio_code"] == scope.portfolio and obs["account"] == "optionmaster_history" and obs["server"] == "90cfcb9bcfb0")
    require(not obs["skipped"] and 0 < obs["query_count"] <= 32 and obs["elapsed_seconds"] <= 90)
    require(_datetime(obs["read_start"]) <= _datetime(obs["read_end"]))
    profile_raw, _ = _wire(obs["profile"])
    profile = _parse_json(profile_raw, _QUERIES[_PROFILE_SQL])
    require(_safe_profile(profile) and {r["name"]: r["value"] for r in profile} == PROFILE)
    terms: list[dict[str, Any]] = []
    catalog: list[dict[str, Any]] = []
    quotes: list[dict[str, Any]] = []
    fut: list[dict[str, Any]] = []
    calendar: list[dict[str, Any]] = []
    query_ids = {obs["profile"]["query_id"]}
    scanned = size = 0
    queries = obs["queries"]
    require(obs["query_count"] == 1 + 2 * len(queries))
    catalog_pages = (scope.expected_count + 32) // 33
    quote_pages = (scope.expected_count + scope.quote_page_size - 1) // scope.quote_page_size
    require(set(queries) == {"terms_low", "terms_high", "futures", "calendar"}
            | {f"catalog_{i}" for i in range(catalog_pages)} | {f"quotes_{i}" for i in range(quote_pages)})
    require(obs["profile"]["sql"] == _PROFILE_SQL and obs["profile"]["params"] == {})
    for name, item in queries.items():
        plan_record, response = item["estimate"], item["response"]
        _, plan = _wire(plan_record)
        raw, doc = _wire(response)
        require(plan_record["sql"] == "EXPLAIN ESTIMATE " + response["sql"] and plan_record["params"] == response["params"])
        require(response["query_id"] not in query_ids and plan_record["query_id"] not in query_ids and response["query_id"] != plan_record["query_id"])
        query_ids.update((response["query_id"], plan_record["query_id"]))
        require(_datetime(obs["read_start"]) <= _datetime(plan_record["read_at"]) <= _datetime(response["read_at"]) <= _datetime(obs["read_end"]))
        require(sum(int(r["rows"]) for r in plan["data"]) <= 100000)
        stats = doc["statistics"]
        n, b = int(stats["rows_read"]), int(stats["bytes_read"])
        require(0 <= n <= 100000 and 0 <= b <= 67108864 and 0 <= float(stats["elapsed"]) <= 5)
        scanned += n
        size += b
        if name.startswith("terms_"):
            spec = _QUERIES[_TERMS_SQL]
            op = "<" if name == "terms_low" else ">="
            sql = spec.sql.replace("symbol = {symbol:String}", "symbol " + op + " {split:String}").replace("LIMIT 2", "LIMIT 64")
            require(response["sql"] == sql and response["params"] == {"portfolio_code": scope.portfolio, "trade_date": day, "split": scope.split_symbol})
            page = _iso_rows(_parse_json(raw, replace(spec, max_rows=64)))
            require(all((r["symbol"] < scope.split_symbol) == (op == "<") for r in page))
            terms.extend(page)
        elif name.startswith("quotes_"):
            spec = _QUERIES[_OPTION_SQL]
            sql = spec.sql.replace("symbol = {symbol:String}", "symbol IN {keys:Array(String)}").replace("LIMIT 2", "LIMIT 64")
            require(response["sql"] == sql and response["params"]["trade_date"] == day)
            quotes.extend(_iso_rows(_parse_json(raw, replace(spec, max_rows=64))))
        elif name.startswith("catalog_"):
            require("FROM quantfeed.option_contracts" in response["sql"] and response["params"]["exchange"] == "XSGE")
            if scope.catalog_sql is not None:
                require(response["sql"] == scope.catalog_sql)
            for c in doc["data"]:
                require(c["strike_type"] == c["size_type"] == "Nullable(Float64)" and c["ingested_type"] == "DateTime64(3, 'Asia/Shanghai')")
                catalog.append({**c, "initial_strike": _bits(c["strike_bits"]), "initial_contract_size": _bits(c["size_bits"]),
                                "ingested_at": (_EPOCH + timedelta(milliseconds=int(c["ingested_epoch_ms"]))).isoformat()})
        elif name == "futures":
            spec = _QUERIES[_FUTURES_SQL]
            require(response["sql"] == spec.sql and response["params"] == {"product_symbol": PRODUCT, "trade_date": day, "symbol": scope.futures_symbol})
            fut.extend(_iso_rows(_parse_json(raw, spec)))
        elif name == "calendar":
            require("FROM quantfeed.trading_calendar" in response["sql"] and response["params"] == {"market": "CN", "trade_date": day})
            if scope.calendar_sql is not None:
                require(response["sql"] == scope.calendar_sql)
            calendar.extend(doc["data"])
    require(scanned == obs["market_rows_read"] and size == obs["market_bytes_read"] and scanned <= 1200000 and size <= 256 * 1024 * 1024)
    require(len(fut) == len(calendar) == 1 and calendar[0]["market"] == "CN" and calendar[0]["trade_date"] == day and calendar[0]["is_open"] is True and calendar[0]["source"] == "JQ")
    require(len(terms) == len(catalog) == len(quotes) == scope.expected_count, "INCOMPLETE_CHAIN_OBSERVATION")
    keys = sorted({r["symbol"] for r in terms})
    require(len(keys) == scope.expected_count, "DUPLICATE_SOURCE_ROW")
    for prefix, page_size, pages in (("catalog", 33, catalog_pages), ("quotes", scope.quote_page_size, quote_pages)):
        for index in range(pages):
            subset = keys[index * page_size:(index + 1) * page_size]
            expected = "[" + ",".join("'" + symbol + "'" for symbol in subset) + "]"
            response = queries[f"{prefix}_{index}"]["response"]
            params = {"exchange": "XSGE", "keys": expected} if prefix == "catalog" else {"keys": expected, "trade_date": day}
            require(response["params"] == params)
            require({r["symbol"] for r in _document(response["body"].encode())["data"]} == set(subset))
    return obs, terms, catalog, quotes, fut[0], calendar[0]


def build_chain_candidate(evidence: Mapping[str, bytes], *, scope: ChainArchiveScope = D08_SCOPE) -> dict[str, Any]:
    """Reproduce the observed candidate for a trusted explicit scope; never approve it."""
    day = scope.day.isoformat()
    require(set(evidence) == set(scope.official) | {OBSERVATION}, "EVIDENCE_SET_MISMATCH")
    evidence = dict(evidence)
    require(all(type(body) is bytes and bool(body) for body in evidence.values()) and sum(map(len, evidence.values())) <= 32 * 1024 * 1024)
    official, _ = _official_catalog(evidence, scope)
    obs, terms, catalog, quotes, fut, calendar = _observed(evidence, scope)
    tmap, cmap, qmap = _unique(terms, "symbol"), _unique(catalog, "symbol"), _unique(quotes, "symbol")
    require(set(tmap) == set(cmap) == set(qmap), "SOURCE_UNIVERSE_MISMATCH")
    exact = [r for r in official if r["underlying_native"] == scope.underlying]
    require(len(exact) == scope.expected_count)
    by_identity = {(r["option_type"], r["strike"]): r for r in exact}
    require(len(by_identity) == len(exact))
    require(fut["symbol"] == scope.futures_symbol and fut["exchange"] == "SHFE" and fut["product_symbol"] == PRODUCT
            and fut["trade_date"] == day and fut["price_source"] == "JQ" and fut["settlement_source"] == SETTLEMENT_SOURCE)
    require(all(_same([fut["close"], fut["settlement"]], [float(r["underlying_close"]), float(r["underlying_settlement"])]) for r in exact))
    observed_at = _datetime(obs["read_end"])
    require(_datetime(fut["ingested_at"]) <= observed_at)
    matched = []
    used: set[str] = set()
    for symbol, t in sorted(tmap.items()):
        c, q = cmap[symbol], qmap[symbol]
        o = by_identity.get((t["option_type"], t["strike"]))
        require(o is not None, "OFFICIAL_CONTRACT_MISSING")
        if o is None:
            raise ChainEvidenceError("OFFICIAL_CONTRACT_MISSING")
        require(o["native_symbol"] not in used, "OFFICIAL_ALIAS_AMBIGUOUS")
        used.add(o["native_symbol"])
        require(t["exchange"] == q["exchange"] == c["exchange"] == "XSGE")
        require(t["symbol"] == q["symbol"] == c["symbol"] == c["vendor_symbol"] == symbol)
        require(t["portfolio_code"] == c["portfolio_code"] == scope.portfolio and t["chain_id"] == scope.chain_id)
        require(t["underlying_symbol"] == c["underlying_symbol"] == scope.source_symbol and c["underlying_asset_class"] == "FUTURE")
        require(t["trade_date"] == q["trade_date"] == day and t["source"] == q["source"] == c["source"] == "JQ")
        require(type(c["is_adjusted"]) is int and c["is_adjusted"] == 0 and t["contract_version"] == c["trading_code"] == "")
        require(t["exercise_style"] == "UNKNOWN" and t["provenance"] == "JQ_UNADJUSTED_CONTRACT" and o["exercise_style"] == "AMERICAN")
        require(t["option_type"] == c["option_type"] == o["option_type"] and t["expiry_date"] == c["expiry_date"] == c["last_trade_date"] == o["expiry"])
        require(c["listed_date"] == o["listed"] and c["listed_date"] <= day < o["expiry"])
        require(t["strike"] == c["initial_strike"] == o["strike"] and t["contract_size"] == c["initial_contract_size"] == o["multiplier"])
        require(q["close"] == o["close"] and q["settlement"] == o["settlement"] and _number(q["settlement"]) and q["settlement"] > 0)
        require(all(_datetime(row["ingested_at"]) <= observed_at for row in (t, q, c)))
        binding = {"official": o, "raw_terms": t, "raw_quote": q, "vendor_catalog": c}
        matched.append({**binding, "effective_revision": "SHFE-REF:" + _hash(_canonical(binding))})
    require(used == {r["native_symbol"] for r in exact})
    counts: dict[str, int] = {}
    for row in official:
        u = row["underlying_native"]
        counts[u] = counts.get(u, 0) + 1
    document = {
        "schema": scope.candidate_schema, "scope": "ONE_DAY_ONE_CHAIN_CANDIDATE_NOT_APPROVED",
        "trade_date": day, "observed_at": obs["read_end"],
        "artifact_sha256": {name: _hash(body) for name, body in sorted(evidence.items())},
        "underlying": fut, "calendar": calendar, "bindings": matched,
        "official_coverage": counts, "official_contract_count": len(official),
        "matched_contract_count": len(matched), "missing_count": 0,
        "ambiguous_count": 0, "mismatch_count": 0,
        "discovery_only_contract_count": len(official) - len(matched),
        "historical_input_admitted": False, "production_valuation_ready": False,
    }
    return {"candidate_packet_sha256": _hash(_canonical(document)), **document}


def discover_chain(evidence: Mapping[str, bytes], *, approval: SliceApproval | None = None) -> dict[str, Any]:
    """List exact official contracts, distinguish candidate vs approved inputs."""
    packet = build_chain_candidate(evidence)
    official, _ = _official_catalog(evidence)
    by_native = {b["official"]["native_symbol"]: b for b in packet["bindings"]}
    admitted = False
    if approval is not None:
        _check_approval(packet, approval, datetime.now(timezone.utc))
        admitted = True
    rows = []
    for o in official:
        binding = by_native.get(o["native_symbol"])
        symbol = binding["raw_terms"]["symbol"] if binding is not None else None
        rows.append({**o, "source_symbol": symbol,
                     "admission_status": "APPROVED_CHAIN_INPUT" if binding is not None and admitted else
                     "D04_APPROVED_EXACT_PAIR_ONLY" if symbol in APPROVED_D04_SYMBOLS else
                     "PENDING_CHAIN_APPROVAL" if binding is not None else "OFFICIAL_DISCOVERY_ONLY"})
    return {"trade_date": DAY.isoformat(), "candidate_packet_sha256": packet["candidate_packet_sha256"],
            "official_coverage": packet["official_coverage"], "contracts": rows,
            "production_valuation_ready": False}


def _check_approval(packet: dict[str, Any], approval: SliceApproval, read_at: datetime) -> None:
    require(isinstance(approval, SliceApproval) and _digest(approval.packet_sha256), "INVALID_SLICE_APPROVAL")
    require(approval.packet_sha256 == packet["candidate_packet_sha256"], "PACKET_DIGEST_MISMATCH")
    require(isinstance(approval.approved_by, str) and bool(approval.approved_by)
            and approval.approved_by.isprintable() and not any(c.isspace() for c in approval.approved_by), "INVALID_SLICE_APPROVAL")
    require(_datetime(packet["observed_at"]) <= _utc(approval.approved_at) <= _utc(read_at), "INVALID_SLICE_APPROVAL")


def selection_for_symbols(symbols: tuple[str, ...]) -> HistoricalReadRequest:
    """Explicit candidate keys from discovery; admission checks membership."""
    require(isinstance(symbols, tuple) and 2 <= len(symbols) <= 8 and all(isinstance(s, str) and s for s in symbols)
            and len(set(symbols)) == len(symbols), "SLICE_SCOPE_MISMATCH")
    return HistoricalReadRequest(DAY, tuple(
        HistoricalFuturesLeg(PORTFOLIO, CHAIN_ID, SourceKey("quantfeed.option/JQ", "XSGE", s), PRODUCT,
                             SourceKey("quantfeed.futures/JQ", "SHFE", FUTURES_SYMBOL), SETTLEMENT_SOURCE)
        for s in symbols
    ))


def admit_chain_selection(request: HistoricalReadRequest, packet: bytes, evidence: Mapping[str, bytes],
                          approval: SliceApproval | None, read_at: datetime) -> SliceResult:
    """Rebuild exact candidate, independently pin it, then use D02/D03/D01."""
    if approval is None:
        return SliceResult(SliceStatus.UNAVAILABLE, "SLICE_NOT_APPROVED")
    try:
        require(type(packet) is bytes and 0 < len(packet) <= 1024 * 1024)
        candidate = build_chain_candidate(evidence)
        require(_same(_document(packet), candidate), "PACKET_DIGEST_MISMATCH")
        _check_approval(candidate, approval, read_at)
        require(isinstance(request, HistoricalReadRequest))
        symbols = tuple(leg.option_key.symbol for leg in request.legs)
        require(request == selection_for_symbols(symbols), "SLICE_SCOPE_MISMATCH")
        bindings = {b["raw_terms"]["symbol"]: b for b in candidate["bindings"]}
        require(all(s in bindings for s in symbols), "SLICE_SCOPE_MISMATCH")
        pin = approval.packet_sha256
        approved_at, admitted_at = _utc(approval.approved_at), _utc(read_at)
        observed_at = _datetime(candidate["observed_at"])
        identity = UnderlyingIdentity("SHFE", PRODUCT, UNDERLYING, date(2022, 11, 1), ResearchAssetClass.FUTURES_OPTION)
        terms_key = SourceKey("quantfeed.option/JQ", "XSGE", SOURCE_SYMBOL)
        quote_key = SourceKey("quantfeed.futures/JQ", "SHFE", FUTURES_SYMBOL)

        def receipt(name: str, record: str, revision: str) -> ContractEvidence:
            digest, uri = OFFICIAL[name]
            return ContractEvidence(EvidenceKind.EXCHANGE_CONTRACT_RECORD, "SHFE", uri, record,
                                    revision, digest, approval.approved_by, approved_at)

        aliases = (
            AliasAttestation(terms_key, identity, DAY, DAY, receipt("option_daily.json", "UNDERLYINGINSTRID=cu2211", pin)),
            AliasAttestation(quote_key, identity, DAY, DAY, receipt("future_daily.json", "DAILY_KEY=cu_f|2211", pin)),
        )
        verified = []
        raw_terms = []
        raw_json = []
        quotes = []
        for s in symbols:
            b = bindings[s]
            t, q, o = b["raw_terms"], b["raw_quote"], b["official"]
            key = SourceKey("quantfeed.option/JQ", "XSGE", s)
            expiry = date.fromisoformat(o["expiry"])
            raw_terms.append(HistoricalTermsRow(key, DAY, terms_key, expiry, t["option_type"], t["exercise_style"],
                                                t["strike"], t["contract_size"], t["contract_version"], _datetime(t["ingested_at"])))
            raw_json.append(_canonical(t))
            quotes.append(HistoricalQuoteRow(s, "XSGE", DAY, q["close"], q["settlement"], "JQ", _datetime(q["ingested_at"])))
            effective = b["effective_revision"]
            verified.append(VerifiedTerms(key, "SHFE", o["native_symbol"], identity, DAY, DAY, expiry, o["option_type"],
                                          ExerciseStyle.AMERICAN, o["strike"], o["multiplier"], effective,
                                          receipt("option_contract_base.json", str(o["base_record_id"]), effective)))
        registry = HistoricalContractRegistry("D08-APPROVED-CHAIN:" + pin, aliases, tuple(verified))
        registry_pin = registry_fingerprint(registry)
        refs = {a.evidence.content_sha256 for a in aliases} | {v.evidence.content_sha256 for v in verified}
        check = admit_registry(registry, RegistryApproval(registry_pin, approval.approved_by, approved_at),
                               {d: evidence[n] for n, (d, _) in OFFICIAL.items() if d in refs}, admitted_at)
        require(check.status is ResearchValuationStatus.OK and check.registry_sha256 == registry_pin, "REGISTRY_UNAVAILABLE")
        reviewed = []
        for raw, term, original, quote in zip(raw_terms, verified, raw_json, quotes, strict=True):
            result = HistoricalContractEngine.evaluate(
                HistoricalContractRequest(DAY, admitted_at, raw.option_key, quote_key, (replace(raw, contract_version=term.contract_version),)), registry)
            require(result.status is ResearchValuationStatus.OK and result.contract is not None, "CONTRACT_UNAVAILABLE")
            if result.contract is None:
                raise ChainEvidenceError("CONTRACT_UNAVAILABLE")
            reviewed.append(ReviewedSliceLeg(raw, original, result.contract, quote))
        f = candidate["underlying"]
        underlying = HistoricalQuoteRow(FUTURES_SYMBOL, "SHFE", DAY, f["close"], f["settlement"], "JQ", _datetime(f["ingested_at"]))
        basis = HistoricalBasisEngine.evaluate(HistoricalBasisRequest(DAY, observed_at, tuple(x.contract.selection for x in reviewed), tuple(quotes), (underlying,)))
        require(basis.status is ResearchValuationStatus.OK and basis.price_basis is HistoricalBasis.SETTLEMENT, "BASIS_UNAVAILABLE")
        return SliceResult(SliceStatus.ADMITTED_REFERENCE, slice=VerifiedHistoricalSlice(
            pin, packet, registry_pin, DAY, observed_at, approval.approved_by, approved_at, admitted_at,
            tuple(sorted(candidate["artifact_sha256"].items())), tuple(reviewed), underlying, basis))
    except ChainEvidenceError as exc:
        return SliceResult(SliceStatus.UNAVAILABLE, str(exc))
    except Exception:
        return SliceResult(SliceStatus.UNAVAILABLE, "CHAIN_EVIDENCE_INVALID")
