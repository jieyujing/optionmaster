"""D09 independently reviewed date/month catalog and portfolio admission.

A contract key is qualified by its trade date. One catalog pin can approve
many exact archived scopes. No HTTP request may supply a trust root.
The original D08 packet remains independently reproducible and unmodified.
"""
from collections.abc import Mapping
from dataclasses import dataclass, replace
from datetime import date, datetime
from pathlib import Path
from types import MappingProxyType
from typing import Any

from vnpy_optionmaster.research_chain_archive import (
    ChainArchiveScope, ChainEvidenceError, D08_SCOPE, OBSERVATION, OFFICIAL, PRODUCT,
    build_chain_candidate, require, _check_approval,
)
from vnpy_optionmaster.research_combination import ResearchAssetClass
from vnpy_optionmaster.research_contract_terms import (
    AliasAttestation, ContractEvidence, EvidenceKind, ExerciseStyle,
    HistoricalContractEngine, HistoricalContractRegistry, HistoricalContractRequest,
    HistoricalTermsRow, SourceKey, UnderlyingIdentity, VerifiedTerms,
)
from vnpy_optionmaster.research_data_basis import (
    HistoricalBasis, HistoricalBasisEngine, HistoricalBasisRequest, HistoricalBasisResult, HistoricalQuoteRow,
)
from vnpy_optionmaster.research_registry_admission import RegistryApproval, admit_registry, registry_fingerprint
from vnpy_optionmaster.research_risk import ResearchValuationStatus
from vnpy_optionmaster.research_verified_slice import (
    ReviewedSliceLeg, SliceApproval, SliceStatus, _canonical, _datetime, _document, _hash, _same, _utc,
)

OLD_PIN = "9216e5598c11ef2736c353dddd08bd7c2efedf742935a9a7ee9afd8383ee8846"
CATALOG_SQL = """SELECT symbol, vendor_symbol, exchange, portfolio_code, underlying_symbol,
    underlying_asset_class, option_type, listed_date, expiry_date, last_trade_date,
    is_adjusted, toString(reinterpretAsUInt64(initial_strike)) AS strike_bits,
    toString(reinterpretAsUInt64(initial_contract_size)) AS size_bits,
    toTypeName(initial_strike) AS strike_type, toTypeName(initial_contract_size) AS size_type,
    trading_code, source, toString(toUnixTimestamp64Milli(ingested_at)) AS ingested_epoch_ms,
    toTypeName(ingested_at) AS ingested_type
    FROM quantfeed.option_contracts
    WHERE exchange = {exchange:String} AND symbol IN {keys:Array(String)}
    LIMIT 64"""
CALENDAR_SQL = """SELECT market, trade_date, is_open, source,
    toString(toUnixTimestamp64Milli(updated_at)) AS updated_epoch_ms
    FROM quantfeed.trading_calendar
    WHERE market = {market:String} AND trade_date = {trade_date:Date}
    LIMIT 2"""
OFFICIAL_SEPT8 = MappingProxyType({
    "option_contract_base.json": (
        "dac82422f3cdc6fa2798c906a43b9e52de082ef61ae42cb0259ac748646e5602",
        "https://www.shfe.com.cn/data/busiparamdata/option/ContractBaseInfo20220908.dat",
    ),
    "option_daily.json": (
        "fb78dc55ea7c03231c5ed1fd5994daba66953c1f3322a877170eff6caa81c674",
        "https://www.shfe.com.cn/data/tradedata/option/dailydata/kx20220908.dat",
    ),
    "future_contract_base.json": (
        "30f2ada34d599cc86380dcbf3f764cf50a667b31f6533a5226b6377d082a920c",
        "https://www.shfe.com.cn/data/busiparamdata/future/ContractBaseInfo20220908.dat",
    ),
    "future_daily.json": (
        "6062e004ae5bb78b2e24ac1749eaeb56c9e97c9c7c510a07ea17d5a4528ff300",
        "https://www.shfe.com.cn/data/tradedata/future/dailydata/kx20220908.dat",
    ),
    "cu_historical_contract_rule.html": OFFICIAL["cu_historical_contract_rule.html"],
})
D09_DEC_SCOPE = ChainArchiveScope(
    date(2022, 9, 7), "cu2212", "XSGE.CU2212.XSGE",
    "XSGE|CU2212.XSGE|2022-11-24", "CU2212.XSGE", "SHFE.cu2212", "CU2212P61000.XSGE",
    66, OFFICIAL, "optionmaster.d09.bounded_chain_observation.v1",
    "optionmaster.d09.chain_candidate.v1", 33, CATALOG_SQL, CALENDAR_SQL,
)
D09_NEXT_SCOPE = ChainArchiveScope(
    date(2022, 9, 8), "cu2211", "XSGE.CU2211.XSGE",
    "XSGE|CU2211.XSGE|2022-10-25", "CU2211.XSGE", "SHFE.cu2211", "CU2211P61000.XSGE",
    66, OFFICIAL_SEPT8, "optionmaster.d09.bounded_chain_observation.v1",
    "optionmaster.d09.chain_candidate.v1", 33, CATALOG_SQL, CALENDAR_SQL,
)


@dataclass(frozen=True)
class CatalogChainSource:
    scope: ChainArchiveScope
    evidence: Mapping[str, bytes]
    # Optional previous independent packet, which must still reproduce.
    packet: bytes = b""

    def __post_init__(self) -> None:
        object.__setattr__(self, "evidence", MappingProxyType(dict(self.evidence)))


def _read(path: Path, limit: int = 4 * 1024 * 1024) -> bytes:
    with path.open("rb") as stream:
        raw = stream.read(limit + 1)
    require(0 < len(raw) <= limit, "EVIDENCE_SIZE_LIMIT")
    return raw


def load_pilot_sources(root: Path) -> tuple[CatalogChainSource, ...]:
    """Local composition for exact pilot scopes; no arbitrary client paths."""
    d04 = root / "m1-d04/shfe-20220907"
    d08 = root / "m1-d08/cu2211-20220907"
    d09 = root / "m1-d09"
    entries = (
        (D08_SCOPE, d04, d08, _read(d08 / "chain_candidate.json")),
        (D09_DEC_SCOPE, d04, d09 / "cu2212-20220907", b""),
        (D09_NEXT_SCOPE, d09 / "shfe-20220908", d09 / "cu2211-20220908", b""),
    )
    sources = []
    for scope, official_root, observation_root, packet in entries:
        evidence = {name: _read((d04 if name.endswith(".html") else official_root) / name)
                    for name in scope.official}
        evidence[OBSERVATION] = _read(observation_root / OBSERVATION)
        sources.append(CatalogChainSource(scope, evidence, packet))
    return tuple(sources)


def _children(sources: tuple[CatalogChainSource, ...]) -> tuple[tuple[CatalogChainSource, dict[str, Any]], ...]:
    require(isinstance(sources, tuple) and 1 <= len(sources) <= 12, "INVALID_CATALOG_SCOPE")
    entries = []
    keys: set[tuple[date, str]] = set()
    symbols: set[tuple[date, str]] = set()
    for source in sources:
        scope = source.scope
        key = scope.day, scope.underlying
        require(key not in keys, "DUPLICATE_CHAIN_SCOPE")
        keys.add(key)
        candidate = build_chain_candidate(source.evidence, scope=scope)
        if source.packet:
            require(_same(_document(source.packet), candidate), "PACKET_DIGEST_MISMATCH")
        for b in candidate["bindings"]:
            k = scope.day, b["raw_terms"]["symbol"]
            require(k not in symbols, "DUPLICATE_CONTRACT_DATE")
            symbols.add(k)
        entries.append((source, candidate))
    return tuple(sorted(entries, key=lambda x: (x[0].scope.day, x[0].scope.underlying)))


def _catalog(entries: tuple[tuple[CatalogChainSource, dict[str, Any]], ...]) -> dict[str, Any]:
    chains = []
    days: dict[str, int] = {}
    official_by_day: dict[str, dict[str, str]] = {}
    old_count = 0
    for source, candidate in entries:
        scope = source.scope
        day = scope.day.isoformat()
        count = candidate["matched_contract_count"]
        expiries = {b["official"]["expiry"] for b in candidate["bindings"]}
        require(len(expiries) == 1, "CHAIN_EXPIRY_CONFLICT")
        official_count = candidate["official_contract_count"]
        require(days.setdefault(day, official_count) == official_count, "OFFICIAL_DATE_CONFLICT")
        digests = {name: candidate["artifact_sha256"][name] for name in source.scope.official}
        require(official_by_day.setdefault(day, digests) == digests, "OFFICIAL_DATE_CONFLICT")
        prior = candidate["candidate_packet_sha256"] == OLD_PIN
        old_count += count if prior else 0
        chains.append({
            "trade_date": day, "underlying": scope.underlying,
            "expiry_date": next(iter(expiries)), "matched_count": count,
            "child_packet_sha256": candidate["candidate_packet_sha256"],
            "artifact_sha256": candidate["artifact_sha256"],
            "observed_at": candidate["observed_at"],
            "prior_d08_pin": prior,
            "underlying_settlement": candidate["underlying"]["settlement"],
            "intrinsic_only_count": sum(
                b["official"]["settlement"] <= max(
                    0.0, (1 if b["official"]["option_type"] == "CALL" else -1)
                    * (b["official"]["underlying_settlement"] - b["official"]["strike"]))
                for b in candidate["bindings"]
            ),
        })
    matched = sum(c["matched_count"] for c in chains)
    doc = {
        "schema": "optionmaster.d09.catalog_candidate.v1",
        "scope": "EXACT_DATED_CHAIN_CANDIDATES_NOT_APPROVED",
        "observed_at": max(c["observed_at"] for c in chains),
        "chains": chains, "official_contract_dates": sum(days.values()),
        "matched_contract_dates": matched, "new_contract_dates": matched - old_count,
        "discovery_only_contract_dates": sum(days.values()) - matched,
        "missing_count": sum(c["missing_count"] for _, c in entries),
        "ambiguous_count": sum(c["ambiguous_count"] for _, c in entries),
        "mismatch_count": sum(c["mismatch_count"] for _, c in entries),
        "historical_input_admitted": False, "production_valuation_ready": False,
    }
    return {"candidate_packet_sha256": _hash(_canonical(doc)), **doc}


def build_catalog_candidate(sources: tuple[CatalogChainSource, ...]) -> dict[str, Any]:
    """One reproducible review pin for all exact dated chain packets."""
    return _catalog(_children(sources))


@dataclass(frozen=True)
class CatalogSelection:
    trade_date: date
    symbols: tuple[str, ...]


@dataclass(frozen=True)
class VerifiedCatalogPortfolio:
    packet_sha256: str
    registry_sha256: str
    trade_date: date
    observed_at: datetime
    approved_at: datetime
    approved_by: str
    admitted_at: datetime
    child_packet_sha256: tuple[str, ...]
    legs: tuple[ReviewedSliceLeg, ...]
    underlying_quotes: tuple[HistoricalQuoteRow, ...]
    basis: HistoricalBasisResult

    @property
    def production_valuation_ready(self) -> bool:
        return False


@dataclass(frozen=True)
class CatalogAdmission:
    status: SliceStatus
    reason_code: str | None = None
    portfolio: VerifiedCatalogPortfolio | None = None


def admit_catalog_selection(
    selection: CatalogSelection, packet: bytes, sources: tuple[CatalogChainSource, ...],
    approval: SliceApproval | None, read_at: datetime,
) -> CatalogAdmission:
    """Rebuild catalog, independently pin it, resolve every selected leg atomically.

    No fabricated mixed-date combination or cross-month Greek aggregation.
    Separate same-date observations are coherent only for the fields checked
    against the exact dated official reports; not a fresh database snapshot.
    """
    if approval is None:
        return CatalogAdmission(SliceStatus.UNAVAILABLE, "SLICE_NOT_APPROVED")
    try:
        require(type(packet) is bytes and 0 < len(packet) <= 1024 * 1024)
        entries = _children(sources)
        candidate = _catalog(entries)
        require(_same(_document(packet), candidate), "PACKET_DIGEST_MISMATCH")
        _check_approval(candidate, approval, read_at)
        require(isinstance(selection, CatalogSelection) and type(selection.trade_date) is date
                and isinstance(selection.symbols, tuple) and 2 <= len(selection.symbols) <= 8
                and all(isinstance(s, str) and s for s in selection.symbols)
                and len(set(selection.symbols)) == len(selection.symbols), "SLICE_SCOPE_MISMATCH")
        by_symbol = {
            b["raw_terms"]["symbol"]: (source, child, b)
            for source, child in entries if source.scope.day == selection.trade_date
            for b in child["bindings"]
        }
        require(all(s in by_symbol for s in selection.symbols), "SLICE_SCOPE_MISMATCH")
        day = selection.trade_date
        pin = approval.packet_sha256
        approved_at, admitted_at = _utc(approval.approved_at), _utc(read_at)
        aliases: dict[SourceKey, AliasAttestation] = {}
        underlying_rows: dict[SourceKey, HistoricalQuoteRow] = {}
        verified: list[VerifiedTerms] = []
        raw_terms: list[HistoricalTermsRow] = []
        raw_json: list[bytes] = []
        quotes: list[HistoricalQuoteRow] = []
        quote_keys: list[SourceKey] = []
        artifacts: dict[str, bytes] = {}
        observations: list[datetime] = []
        child_pins: set[str] = set()
        for symbol in selection.symbols:
            source, child, binding = by_symbol[symbol]
            scope = source.scope
            t, q, o = binding["raw_terms"], binding["raw_quote"], binding["official"]
            f = child["underlying"]
            child_pin = child["candidate_packet_sha256"]
            child_pins.add(child_pin)
            observations.append(_datetime(child["observed_at"]))
            identity = UnderlyingIdentity(
                "SHFE", PRODUCT, o["underlying_native"], date.fromisoformat(o["delivery_month"] + "-01"),
                ResearchAssetClass.FUTURES_OPTION,
            )
            terms_key = SourceKey("quantfeed.option/JQ", "XSGE", scope.source_symbol)
            quote_key = SourceKey("quantfeed.futures/JQ", "SHFE", scope.futures_symbol)

            def receipt(name: str, record: str, revision: str, selected_source: CatalogChainSource = source) -> ContractEvidence:
                digest, uri = selected_source.scope.official[name]
                artifacts[digest] = selected_source.evidence[name]
                return ContractEvidence(EvidenceKind.EXCHANGE_CONTRACT_RECORD, "SHFE", uri, record,
                                        revision, digest, approval.approved_by, approved_at)

            if terms_key not in aliases:
                aliases[terms_key] = AliasAttestation(
                    terms_key, identity, day, day,
                    receipt("option_daily.json", "UNDERLYINGINSTRID=" + scope.underlying, child_pin),
                )
                aliases[quote_key] = AliasAttestation(
                    quote_key, identity, day, day,
                    receipt("future_daily.json", "DAILY_KEY=cu_f|" + scope.underlying.removeprefix("cu"), child_pin),
                )
                underlying_rows[quote_key] = HistoricalQuoteRow(
                    f["symbol"], "SHFE", day, f["close"], f["settlement"], "JQ", _datetime(f["ingested_at"]),
                )
            require(aliases[terms_key].identity == aliases[quote_key].identity == identity, "UNDERLYING_ALIAS_CONFLICT")
            key = SourceKey("quantfeed.option/JQ", "XSGE", symbol)
            expiry = date.fromisoformat(o["expiry"])
            effective = binding["effective_revision"]
            raw_terms.append(HistoricalTermsRow(key, day, terms_key, expiry, t["option_type"], t["exercise_style"],
                                                t["strike"], t["contract_size"], t["contract_version"], _datetime(t["ingested_at"])))
            raw_json.append(_canonical(t))
            quotes.append(HistoricalQuoteRow(symbol, "XSGE", day, q["close"], q["settlement"], "JQ", _datetime(q["ingested_at"])))
            quote_keys.append(quote_key)
            verified.append(VerifiedTerms(
                key, "SHFE", o["native_symbol"], identity, day, day, expiry, o["option_type"],
                ExerciseStyle.AMERICAN, o["strike"], o["multiplier"], effective,
                receipt("option_contract_base.json", str(o["base_record_id"]), effective),
            ))
        registry = HistoricalContractRegistry("D09-APPROVED-CATALOG:" + pin, tuple(aliases.values()), tuple(verified))
        registry_pin = registry_fingerprint(registry)
        checked = admit_registry(registry, RegistryApproval(registry_pin, approval.approved_by, approved_at), artifacts, admitted_at)
        require(checked.status is ResearchValuationStatus.OK and checked.registry_sha256 == registry_pin, "REGISTRY_UNAVAILABLE")
        reviewed = []
        for raw, term, original, quote, quote_key in zip(raw_terms, verified, raw_json, quotes, quote_keys, strict=True):
            result = HistoricalContractEngine.evaluate(
                HistoricalContractRequest(day, admitted_at, raw.option_key, quote_key,
                                          (replace(raw, contract_version=term.contract_version),)), registry)
            if result.status is not ResearchValuationStatus.OK or result.contract is None:
                raise ChainEvidenceError(result.reason_code or "CONTRACT_UNAVAILABLE")
            reviewed.append(ReviewedSliceLeg(raw, original, result.contract, quote))
        observed_at = max(observations)
        underlyings = tuple(underlying_rows.values())
        basis = HistoricalBasisEngine.evaluate(HistoricalBasisRequest(
            day, observed_at, tuple(x.contract.selection for x in reviewed), tuple(quotes), underlyings,
        ))
        require(basis.status is ResearchValuationStatus.OK and basis.price_basis is HistoricalBasis.SETTLEMENT, "BASIS_UNAVAILABLE")
        return CatalogAdmission(SliceStatus.ADMITTED_REFERENCE, portfolio=VerifiedCatalogPortfolio(
            pin, registry_pin, day, observed_at, approved_at, approval.approved_by, admitted_at,
            tuple(sorted(child_pins)), tuple(reviewed), underlyings, basis,
        ))
    except ChainEvidenceError as exc:
        return CatalogAdmission(SliceStatus.UNAVAILABLE, str(exc))
    except (TypeError, ValueError, KeyError, AttributeError, IndexError, OverflowError, RecursionError):
        return CatalogAdmission(SliceStatus.UNAVAILABLE, "CHAIN_EVIDENCE_INVALID")
