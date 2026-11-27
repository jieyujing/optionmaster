"""D04: admit an independently approved archived real historical slice.

Only the exact content-pinned, one-day SHFE/JQ evidence contract is accepted.
This is a local historical input boundary, not a transport, a live consistency
waiver, a generic missing-version repair, or permission to run valuation/trades.
The trusted operator composition layer supplies the approval; read requests
cannot approve their own evidence. No network, filesystem or DB operations.
"""
from collections.abc import Mapping
from dataclasses import dataclass, replace
from datetime import date, datetime, timezone
from enum import Enum
from hashlib import sha256
import json
from typing import Any, cast

from vnpy_optionmaster.research_combination import ResearchAssetClass
from vnpy_optionmaster.research_contract_terms import (
    AliasAttestation,
    ContractEvidence,
    EvidenceKind,
    ExerciseStyle,
    HistoricalContractEngine,
    HistoricalContractRegistry,
    HistoricalContractRequest,
    HistoricalTermsRow,
    SourceKey,
    UnderlyingIdentity,
    ValidatedHistoricalContract,
    VerifiedTerms,
)
from vnpy_optionmaster.research_data_basis import (
    HistoricalBasisEngine,
    HistoricalBasisRequest,
    HistoricalBasisResult,
    HistoricalQuoteRow,
)
from vnpy_optionmaster.research_history_repository import HistoricalFuturesLeg, HistoricalReadRequest
from vnpy_optionmaster.research_registry_admission import RegistryApproval, admit_registry, registry_fingerprint
from vnpy_optionmaster.research_risk import ResearchValuationStatus

# Only this reviewed packet schema is implemented. The pin is supplied by
# the operator, not inferred from the packet or a caller's read request.
_MAX_PACKET = 1024 * 1024
_MAX_EVIDENCE = 32 * 1024 * 1024
_SCOPE = "EXACT_OFFICIAL_COMPARISON_ONE_DAY_CANDIDATE_NOT_APPROVED"
_OFFICIAL = frozenset({
    "option_contract_base.json", "option_daily.json", "future_contract_base.json",
    "future_daily.json", "cu_historical_contract_rule.html",
})
_OBSERVATIONS = frozenset({
    "reference_slice_result.json", "clickhouse_live_wire_observation.json",
    "clickhouse_catalog_calendar_observation.json",
})
_PROFILE = {
    "readonly": "1", "max_rows_to_read": "100000", "max_bytes_to_read": "67108864",
    "max_execution_time": "5", "max_result_rows": "64", "max_result_bytes": "1048576",
    "read_overflow_mode": "throw", "result_overflow_mode": "throw", "timeout_overflow_mode": "throw",
}


class SliceStatus(str, Enum):
    ADMITTED_REFERENCE = "ADMITTED_REFERENCE"
    UNAVAILABLE = "UNAVAILABLE"


@dataclass(frozen=True)
class SliceApproval:
    """Trusted operator input, independent of candidate packet contents."""

    packet_sha256: str
    approved_by: str
    approved_at: datetime


@dataclass(frozen=True)
class ReviewedSliceLeg:
    """Original source row and separate D02 effective version result."""

    raw_terms: HistoricalTermsRow
    raw_source_json: bytes
    contract: ValidatedHistoricalContract
    quote: HistoricalQuoteRow


@dataclass(frozen=True)
class VerifiedHistoricalSlice:
    """Admitted immutable observed inputs; NOT a fresh/live repository read."""

    packet_sha256: str
    packet_bytes: bytes
    registry_sha256: str
    trade_date: date
    observed_at: datetime
    approved_by: str
    approved_at: datetime
    admitted_at: datetime
    artifact_digests: tuple[tuple[str, str], ...]
    legs: tuple[ReviewedSliceLeg, ...]
    underlying_quote: HistoricalQuoteRow
    basis: HistoricalBasisResult

    @property
    def historical_input_admitted(self) -> bool:
        return True

    @property
    def valuation_ready(self) -> bool:
        # Other pricing inputs and production transport gates are independent.
        return False

    @property
    def consistency(self) -> str:
        return "VERIFIED_OFFICIAL_ARCHIVED_SLICE"


@dataclass(frozen=True)
class SliceResult:
    status: SliceStatus
    reason_code: str | None = None
    slice: VerifiedHistoricalSlice | None = None


class _Rejected(Exception):
    pass


def _require(condition: bool, reason: str = "INVALID_SLICE_EVIDENCE") -> None:
    if not condition:
        raise _Rejected(reason)


def _canonical(value: object) -> bytes:
    return json.dumps(value, ensure_ascii=True, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()


def _hash(value: bytes) -> str:
    return sha256(value).hexdigest()


def _no_duplicates(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        _require(key not in result)
        result[key] = value
    return result


def _invalid_constant(value: str) -> object:
    raise _Rejected("INVALID_SLICE_EVIDENCE")


def _document(value: bytes) -> dict[str, Any]:
    data = json.loads(value, object_pairs_hook=_no_duplicates, parse_constant=_invalid_constant)
    _require(isinstance(data, dict))
    return cast(dict[str, Any], data)


def _utc(value: datetime) -> datetime:
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise _Rejected("INVALID_SLICE_APPROVAL")
    return value.astimezone(timezone.utc)


def _datetime(value: str) -> datetime:
    return _utc(datetime.fromisoformat(value))


def _digest(value: object) -> bool:
    return isinstance(value, str) and len(value) == 64 and all(c in "0123456789abcdef" for c in value)


def _same(left: object, right: object) -> bool:
    # Do not let Python's True == 1 or a lossy conversion hide raw row drift.
    return _canonical(left) == _canonical(right)


def _one_observed(live: dict[str, Any], key: str) -> dict[str, Any]:
    item = live["rows"][key]
    _require(item["wire_validation"] == "PASS")
    _require(len(item["raw_rows"]) == len(item["decoded_rows"]) == 1)
    statistics = item["statistics"]
    _require(0 <= statistics["rows_read"] <= 100000 and 0 <= statistics["bytes_read"] <= 67108864)
    _require(0 <= statistics["elapsed"] <= 5)
    row = item["decoded_rows"][0]
    _require(isinstance(row, dict))
    return cast(dict[str, Any], row)


def _quote(key: SourceKey, day: date, row: dict[str, Any], source: str) -> HistoricalQuoteRow:
    return HistoricalQuoteRow(
        key.symbol, key.exchange, day, row["close"], row["settlement"], source, _datetime(row["ingested_at"]),
    )


def admit_verified_slice(
    request: HistoricalReadRequest,
    packet: bytes,
    evidence: Mapping[str, bytes],
    approval: SliceApproval | None,
    read_at: datetime,
) -> SliceResult:
    """Verify the approved archive and materialize only its exact requested legs.

    The original source rows are retained separately from D02's derived
    effective rows. D03 registry admission is delegated by the independently
    pinned packet: deterministic registry derivation occurs ONLY after all
    packet and artifact digests match that external approval. A packet cannot
    grant itself trust, even if it contains a recomputed digest. D01 rechecks
    the actual observed marks at the recorded original read time.
    """
    try:
        return _admit(request, packet, evidence, approval, read_at)
    except _Rejected as error:
        return SliceResult(SliceStatus.UNAVAILABLE, str(error))
    except (ValueError, TypeError, KeyError, IndexError, AttributeError, OverflowError, RecursionError):
        return SliceResult(SliceStatus.UNAVAILABLE, "INVALID_SLICE_EVIDENCE")


def _admit(
    request: HistoricalReadRequest,
    packet: bytes,
    evidence: Mapping[str, bytes],
    approval: SliceApproval | None,
    read_at: datetime,
) -> SliceResult:
    if approval is None:
        raise _Rejected("SLICE_NOT_APPROVED")
    _require(
        isinstance(approval, SliceApproval) and _digest(approval.packet_sha256)
        and isinstance(approval.approved_by, str) and bool(approval.approved_by)
        and approval.approved_by.isprintable() and not any(c.isspace() for c in approval.approved_by),
        "INVALID_SLICE_APPROVAL",
    )
    admitted_at, approved_at = _utc(read_at), _utc(approval.approved_at)
    _require(approved_at <= admitted_at, "INVALID_SLICE_APPROVAL")
    _require(type(packet) is bytes and 0 < len(packet) <= _MAX_PACKET)
    document = _document(packet)
    claimed = document.pop("candidate_packet_sha256", None)
    pin = _hash(_canonical(document))
    _require(claimed == pin == approval.packet_sha256, "PACKET_DIGEST_MISMATCH")
    _require(
        document["scope"] == _SCOPE and document["comparison"] == "PASS"
        and document["valuation_ready"] is False and document["d03"] == "REGISTRY_NOT_APPROVED"
        and document["raw_d02"] == "MISSING_CONTRACT_VERSION"
        and document["effective_version_d02_preview"] == "PASS"
        and document["official_date_and_calendar"] == "MATCH"
        and document["consumed_fields_agreement"] == "PASS_ONE_DAY_OBSERVED_VALUES_ONLY"
        and document["tls_transport"] == "NOT_VERIFIED_LAN_HTTP_DIAGNOSTIC"
    )
    day = date.fromisoformat(document["trade_date"])
    bindings = document["bindings"]
    _require(isinstance(bindings, list) and len(bindings) == 2)
    _require(isinstance(request, HistoricalReadRequest) and request.trade_date == day, "SLICE_SCOPE_MISMATCH")
    hashes = document["evidence_files_sha256"]
    official_hashes = bindings[0]["official_artifacts"]
    _require(set(hashes) == _OBSERVATIONS and set(official_hashes) == _OFFICIAL)
    _require(all(_same(b["official_artifacts"], official_hashes) for b in bindings))
    all_hashes = {**hashes, **official_hashes}
    _require(isinstance(evidence, Mapping) and set(evidence) == set(all_hashes), "EVIDENCE_SET_MISMATCH")
    # Freeze the byte references once, so later lookups cannot see a
    # concurrently replaced entry after its integrity check.
    evidence = dict(evidence)
    total = 0
    for name, expected in all_hashes.items():
        body = evidence[name]
        _require(type(body) is bytes and bool(body) and _digest(expected))
        total += len(body)
        _require(total <= _MAX_EVIDENCE, "EVIDENCE_BUDGET_EXCEEDED")
        _require(_hash(body) == expected, "EVIDENCE_DIGEST_MISMATCH")

    ref = _document(evidence["reference_slice_result.json"])
    live = _document(evidence["clickhouse_live_wire_observation.json"])
    catalog = _document(evidence["clickhouse_catalog_calendar_observation.json"])
    observed_at = _datetime(live["read_end"])
    _require(observed_at <= approved_at, "INVALID_SLICE_APPROVAL")
    _require(
        ref["source"] == "SHFE" and ref["trade_date"] == catalog["trade_date"] == day.isoformat()
        and ref["d02_reference_preview"] == "PASS"
        and live["scope"] == "SHFE_REFERENCE_20220907_LAN_HTTP_DIAGNOSTIC_NOT_TLS_PRODUCTION"
        and live["query_count"] == 6 and live["profile"] == document["actual_reader_profile"] == _PROFILE
        and set(live["rows"]) == {"terms_call", "terms_put", "option_call", "option_put", "futures"}
        and len(catalog["catalog_rows"]) == len(ref["options"]) == 2
        and len(catalog["calendar_rows"]) == 1
        and catalog["calendar_rows"][0]["market"] == "CN"
        and catalog["calendar_rows"][0]["trade_date"] == day.isoformat()
        and catalog["calendar_rows"][0]["is_open"] is True
    )
    manifest = ref["artifact_manifest"]
    _require(set(manifest) == _OFFICIAL)
    for name, meta in manifest.items():
        _require(meta["sha256"] == all_hashes[name] and meta["bytes"] == len(evidence[name]))
    fut = _one_observed(live, "futures")
    underlying = ref["underlying"]
    _require(
        fut["exchange"] == "SHFE" and fut["price_source"] == "JQ"
        and fut["settlement_source"] == "JOINQUANT_GET_EXTRAS:futures_sett_price"
        and fut["trade_date"] == day.isoformat()
        and (fut["close"], fut["settlement"]) == (underlying["close"], underlying["settlement"])
    )
    quote_key = SourceKey("quantfeed.futures/JQ", "SHFE", fut["symbol"])
    terms_key = SourceKey("quantfeed.option/JQ", "XSGE", bindings[0]["raw_terms"]["underlying_symbol"])
    identity = UnderlyingIdentity(
        "SHFE", fut["product_symbol"], underlying["native_symbol"],
        date.fromisoformat(underlying["delivery_month"] + "-01"), ResearchAssetClass.FUTURES_OPTION,
    )
    underlying_quote = _quote(quote_key, day, fut, "JQ")

    # The independent packet approval covers the exact derivation below.
    # D03's receipt-level digest check additionally authenticates integrity
    # of the actual referenced artifact bytes, not the origin's authority.
    def receipt(filename: str, record_id: str, revision: str) -> ContractEvidence:
        item = manifest[filename]
        return ContractEvidence(
            EvidenceKind.EXCHANGE_CONTRACT_RECORD, "SHFE", item["url"], record_id,
            revision, item["sha256"], approval.approved_by, approved_at,
        )

    aliases = (
        AliasAttestation(terms_key, identity, day, day, receipt(
            "option_daily.json", "UNDERLYINGINSTRID=" + identity.contract_code, pin,
        )),
        AliasAttestation(quote_key, identity, day, day, receipt(
            "future_daily.json", "DAILY_KEY=" + underlying["official_daily_key"], pin,
        )),
    )
    terms: list[VerifiedTerms] = []
    raw_terms: list[HistoricalTermsRow] = []
    source_json: list[bytes] = []
    quotes: list[HistoricalQuoteRow] = []
    requested: list[HistoricalFuturesLeg] = []
    for suffix, right, binding in zip(("call", "put"), ("CALL", "PUT"), bindings, strict=True):
        t = _one_observed(live, "terms_" + suffix)
        q = _one_observed(live, "option_" + suffix)
        c = binding["vendor_catalog"]
        official = binding["official_record"]
        _require(
            binding["scope"] == "ONE_DAY_EFFECTIVE_TERMS_BINDING_NOT_VENDOR_VERSION"
            and binding["trade_date"] == day.isoformat()
            and _same(binding["raw_terms"], t) and _same(binding["raw_quote"], q)
            and _same(binding["raw_underlying"], fut)
            and sum(_same(c, row) for row in catalog["catalog_rows"]) == 1
            and sum(_same(official, row) for row in ref["options"]) == 1
            and binding["source_codec_head"] == catalog["producer_source_review"]["observed_head"]
        )
        effective = binding["proposed_effective_revision"]
        revision_payload = {k: v for k, v in binding.items() if k != "proposed_effective_revision"}
        _require(effective == "SHFE-REF:" + _hash(_canonical(revision_payload)))
        _require(
            t["source"] == q["source"] == c["source"] == "JQ"
            and t["exchange"] == q["exchange"] == c["exchange"] == "XSGE"
            and t["symbol"] == q["symbol"] == c["symbol"] == c["vendor_symbol"]
            and t["underlying_symbol"] == c["underlying_symbol"] == terms_key.symbol
            and c["underlying_asset_class"] == "FUTURE" and c["is_adjusted"] == 0
            and t["portfolio_code"] == c["portfolio_code"]
            and t["trade_date"] == q["trade_date"] == day.isoformat()
            and t["option_type"] == c["option_type"] == official["option_type"] == right
            and t["expiry_date"] == c["expiry_date"] == c["last_trade_date"] == official["expiry"]
            and c["listed_date"] == official["listed"]
            and t["strike"] == c["initial_strike"] == official["strike"]
            and t["contract_size"] == c["initial_contract_size"] == official["multiplier"]
            and (q["close"], q["settlement"]) == (official["close"], official["settlement"])
            and t["provenance"] == "JQ_UNADJUSTED_CONTRACT"
            and t["exercise_style"] == "UNKNOWN" and official["exercise_style"] == "AMERICAN"
            and t["contract_version"] == c["trading_code"] == ""
            and official["underlying_native"] == identity.contract_code
            and official["valid_from"] == official["valid_through"] == day.isoformat()
        )
        key = SourceKey("quantfeed.option/JQ", "XSGE", t["symbol"])
        expiry = date.fromisoformat(t["expiry_date"])
        raw = HistoricalTermsRow(
            key, day, terms_key, expiry, right, t["exercise_style"],
            t["strike"], t["contract_size"], t["contract_version"], _datetime(t["ingested_at"]),
        )
        _require(raw.ingested_at <= observed_at)
        term = VerifiedTerms(
            key, "SHFE", official["native_symbol"], identity, day, day, expiry, right,
            ExerciseStyle.AMERICAN, official["strike"], official["multiplier"], effective,
            receipt("option_contract_base.json", str(official["base_record_id"]), effective),
        )
        terms.append(term)
        raw_terms.append(raw)
        source_json.append(_canonical(t))
        quotes.append(_quote(key, day, q, "JQ"))
        requested.append(HistoricalFuturesLeg(
            t["portfolio_code"], t["chain_id"], key, identity.product_family_id,
            quote_key, fut["settlement_source"],
        ))
    _require(request.legs == tuple(requested), "SLICE_SCOPE_MISMATCH")
    registry = HistoricalContractRegistry("D04-APPROVED-SLICE:" + pin, aliases, tuple(terms))
    registry_pin = registry_fingerprint(registry)
    referenced = {record.evidence.content_sha256 for record in aliases} | {
        record.evidence.content_sha256 for record in terms
    }
    d03 = admit_registry(
        registry, RegistryApproval(registry_pin, approval.approved_by, approved_at),
        {digest: evidence[name] for name, digest in all_hashes.items() if digest in referenced},
        admitted_at,
    )
    _require(d03.status is ResearchValuationStatus.OK and d03.registry_sha256 == registry_pin, "REGISTRY_UNAVAILABLE")
    reviewed: list[ReviewedSliceLeg] = []
    for raw, term, original, quote in zip(raw_terms, terms, source_json, quotes, strict=True):
        # Do not modify raw. Only the effective projection of this pinned,
        # independently approved observation receives the reviewed revision.
        effective_row = replace(raw, contract_version=term.contract_version)
        result = HistoricalContractEngine.evaluate(
            HistoricalContractRequest(day, admitted_at, raw.option_key, quote_key, (effective_row,)), registry,
        )
        _require(result.status is ResearchValuationStatus.OK and result.contract is not None, "CONTRACT_UNAVAILABLE")
        if result.contract is None:
            raise _Rejected("CONTRACT_UNAVAILABLE")
        reviewed.append(ReviewedSliceLeg(raw, original, result.contract, quote))
    basis = HistoricalBasisEngine.evaluate(HistoricalBasisRequest(
        day, observed_at, tuple(leg.contract.selection for leg in reviewed),
        tuple(quotes), (underlying_quote,),
    ))
    _require(basis.status is ResearchValuationStatus.OK, "BASIS_UNAVAILABLE")
    _require(
        basis.price_basis is not None
        and document["actual_marks_d01_preview"]["basis"] == basis.price_basis.value
        and document["actual_marks_d01_preview"]["pairs"] == [
            [leg.option_price, leg.underlying_price] for leg in basis.legs
        ],
        "BASIS_EVIDENCE_CONFLICT",
    )
    return SliceResult(SliceStatus.ADMITTED_REFERENCE, slice=VerifiedHistoricalSlice(
        pin, packet, registry_pin, day, observed_at, approval.approved_by, approved_at, admitted_at,
        tuple(sorted(all_hashes.items())), tuple(reviewed), underlying_quote, basis,
    ))
