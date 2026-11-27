"""M1-D02: source-neutral historical contract identity and terms validation.

The registry is a separate, trusted input assembled from independently reviewed
exact contract records. Receipt validation here checks structure and temporal
applicability, not network authenticity. No production registry is bundled.
Raw source labels, symbol transformations and product rules alone cannot create
an attestation. No database access, default terms, trading, or live model choice.
"""
from dataclasses import asdict, dataclass
from datetime import date, datetime, timedelta, timezone
from enum import Enum
from hashlib import sha256
import json
from math import isfinite
from urllib.parse import urlsplit

from vnpy_optionmaster.research_combination import ResearchAssetClass
from vnpy_optionmaster.research_data_basis import HistoricalLegSelection
from vnpy_optionmaster.research_risk import ResearchValuationStatus


class EvidenceKind(str, Enum):
    """Exact, reviewed instrument records only, not a product-level rule."""

    EXCHANGE_CONTRACT_RECORD = "EXCHANGE_CONTRACT_RECORD"
    VENDOR_CONTRACT_MASTER = "VENDOR_CONTRACT_MASTER"


class ExerciseStyle(str, Enum):
    AMERICAN = "AMERICAN"
    EUROPEAN = "EUROPEAN"


@dataclass(frozen=True)
class SourceKey:
    """An exact key in a named source namespace, never a normalized symbol."""

    namespace: str
    exchange: str
    symbol: str


@dataclass(frozen=True)
class UnderlyingIdentity:
    """Exchange identity with explicit family and delivery month (or spot ID)."""

    exchange: str
    product_family_id: str
    contract_code: str
    delivery_month: date | None
    asset_class: ResearchAssetClass


@dataclass(frozen=True)
class ContractEvidence:
    """Review receipt for a specific contract record and source artifact revision."""

    kind: EvidenceKind
    publisher: str
    source_uri: str
    record_id: str
    revision: str
    content_sha256: str
    reviewed_by: str
    reviewed_at: datetime


@dataclass(frozen=True)
class AliasAttestation:
    source_key: SourceKey
    identity: UnderlyingIdentity
    valid_from: date
    valid_through: date
    evidence: ContractEvidence


@dataclass(frozen=True)
class HistoricalTermsRow:
    """Raw source terms for exactly one trade date; not authority by itself."""

    option_key: SourceKey
    trade_date: date
    underlying_key: SourceKey
    expiry_date: date | None
    option_type: str
    exercise_style: str
    strike: float | None
    contract_size: float | None
    contract_version: str
    ingested_at: datetime


@dataclass(frozen=True)
class VerifiedTerms:
    """Reviewed exact option terms, not an extrapolation from current rules."""

    option_key: SourceKey
    option_exchange: str
    option_contract_code: str
    underlying: UnderlyingIdentity
    valid_from: date
    valid_through: date
    expiry_date: date
    option_type: str
    exercise_style: ExerciseStyle
    strike: float
    contract_size: float
    contract_version: str
    evidence: ContractEvidence


@dataclass(frozen=True)
class HistoricalContractRegistry:
    """Operator-reviewed immutable input; never construct from raw quote/terms rows."""

    version: str
    aliases: tuple[AliasAttestation, ...]
    terms: tuple[VerifiedTerms, ...]


@dataclass(frozen=True)
class HistoricalContractRequest:
    trade_date: date
    read_at: datetime
    option_key: SourceKey
    underlying_quote_key: SourceKey
    rows: tuple[HistoricalTermsRow, ...]


@dataclass(frozen=True)
class ValidatedHistoricalContract:
    """Full audit lineage plus the exact selection needed by the D01 boundary."""

    trade_date: date
    read_at: datetime
    registry_version: str
    raw_terms: HistoricalTermsRow
    terms: VerifiedTerms
    terms_alias: AliasAttestation
    quote_alias: AliasAttestation
    selection: HistoricalLegSelection


@dataclass(frozen=True)
class HistoricalContractResult:
    status: ResearchValuationStatus
    reason_code: str | None = None
    contract: ValidatedHistoricalContract | None = None


def _unavailable(reason: str) -> HistoricalContractResult:
    return HistoricalContractResult(ResearchValuationStatus.UNAVAILABLE, reason)


def _token(value: object) -> bool:
    return isinstance(value, str) and bool(value) and value.isprintable() and not any(c.isspace() for c in value)


def _utc(value: object) -> datetime | None:
    if not isinstance(value, datetime):
        return None
    try:
        if value.tzinfo is None or value.utcoffset() is None:
            return None
        return value.astimezone(timezone.utc)
    except (OverflowError, ValueError, TypeError):
        return None


def _positive(value: object) -> bool:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return False
    try:
        return isfinite(value) and value > 0
    except (ValueError, OverflowError):
        return False


def _key(value: object) -> bool:
    return isinstance(value, SourceKey) and all(_token(x) for x in (
        value.namespace, value.exchange, value.symbol,
    ))


def _identity(value: object) -> bool:
    if not isinstance(value, UnderlyingIdentity):
        return False
    if not all(_token(x) for x in (value.exchange, value.product_family_id, value.contract_code)):
        return False
    if not isinstance(value.asset_class, ResearchAssetClass):
        return False
    if value.asset_class is ResearchAssetClass.FUTURES_OPTION:
        return type(value.delivery_month) is date and value.delivery_month.day == 1
    return value.delivery_month is None


def _interval(start: date, end: date) -> bool:
    return type(start) is date and type(end) is date and start <= end


def _valid_evidence(evidence: object, read_at: datetime) -> bool:
    if not isinstance(evidence, ContractEvidence) or not isinstance(evidence.kind, EvidenceKind):
        return False
    if not all(_token(x) for x in (
        evidence.publisher, evidence.source_uri, evidence.record_id, evidence.revision,
        evidence.content_sha256, evidence.reviewed_by,
    )):
        return False
    try:
        uri = urlsplit(evidence.source_uri)
        if uri.scheme != "https" or not uri.hostname or uri.username or uri.password:
            return False
    except ValueError:
        return False
    if len(evidence.content_sha256) != 64 or any(c not in "0123456789abcdef" for c in evidence.content_sha256):
        return False
    reviewed_utc = _utc(evidence.reviewed_at)
    return reviewed_utc is not None and reviewed_utc <= read_at


def _resolve_alias(
    key: SourceKey,
    registry: HistoricalContractRegistry,
    trade_date: date,
    read_at: datetime,
) -> tuple[AliasAttestation | None, str | None]:
    candidates = [a for a in registry.aliases if a.source_key == key]
    if not candidates:
        return None, "MISSING_ALIAS"
    if any(not _interval(a.valid_from, a.valid_through) for a in candidates):
        return None, "INVALID_ALIAS"
    active = [a for a in candidates if a.valid_from <= trade_date <= a.valid_through]
    if not active:
        return None, "ALIAS_OUTSIDE_VALIDITY"
    if len(active) != 1:
        return None, "AMBIGUOUS_ALIAS"
    alias = active[0]
    if not _identity(alias.identity):
        return None, "INVALID_ALIAS"
    if not _valid_evidence(alias.evidence, read_at):
        return None, "INVALID_EVIDENCE"
    return alias, None


def _resolve_terms(
    key: SourceKey,
    registry: HistoricalContractRegistry,
    trade_date: date,
    read_at: datetime,
) -> tuple[VerifiedTerms | None, str | None]:
    candidates = [t for t in registry.terms if t.option_key == key]
    if not candidates:
        return None, "MISSING_TERMS_EVIDENCE"
    if any(not _interval(t.valid_from, t.valid_through) or not _token(t.contract_version) for t in candidates):
        return None, "INVALID_TERMS_EVIDENCE"
    # A version is unique within an exact option source key, even across dates.
    if len({t.contract_version for t in candidates}) != len(candidates):
        return None, "AMBIGUOUS_TERMS_VERSION"
    active = [t for t in candidates if t.valid_from <= trade_date <= t.valid_through]
    if not active:
        return None, "TERMS_OUTSIDE_VALIDITY"
    if len(active) != 1:
        return None, "AMBIGUOUS_TERMS_VERSION"
    terms = active[0]
    if (
        not _identity(terms.underlying)
        or not _token(terms.option_exchange)
        or not _token(terms.option_contract_code)
        or (
            terms.underlying.asset_class is ResearchAssetClass.FUTURES_OPTION
            and terms.option_exchange != terms.underlying.exchange
        )
        or type(terms.expiry_date) is not date
        or terms.valid_through > terms.expiry_date
        or not isinstance(terms.exercise_style, ExerciseStyle)
        or terms.option_type not in ("CALL", "PUT")
        or not _positive(terms.strike)
        or not _positive(terms.contract_size)
        or not _token(terms.contract_version)
    ):
        return None, "INVALID_TERMS_EVIDENCE"
    if not _valid_evidence(terms.evidence, read_at):
        return None, "INVALID_EVIDENCE"
    return terms, None


class HistoricalContractEngine:
    """Resolve one option's terms and exact underlying quote identity, or fail closed."""

    @staticmethod
    def evaluate(
        request: HistoricalContractRequest,
        registry: HistoricalContractRegistry,
    ) -> HistoricalContractResult:
        if (
            not isinstance(request, HistoricalContractRequest)
            or type(request.trade_date) is not date
            or not _key(request.option_key)
            or not _key(request.underlying_quote_key)
            or not isinstance(request.rows, tuple)
            or not all(isinstance(row, HistoricalTermsRow) for row in request.rows)
        ):
            return _unavailable("INVALID_INPUT")
        read_utc = _utc(request.read_at)
        if read_utc is None:
            return _unavailable("INVALID_INPUT")
        try:
            read_date_cn = read_utc.astimezone(timezone(timedelta(hours=8))).date()
        except (OverflowError, ValueError):
            return _unavailable("INVALID_INPUT")
        if request.trade_date > read_date_cn:
            return _unavailable("INVALID_INPUT")
        if (
            not isinstance(registry, HistoricalContractRegistry)
            or not _token(registry.version)
            or not isinstance(registry.aliases, tuple)
            or not isinstance(registry.terms, tuple)
            or not all(isinstance(a, AliasAttestation) and _key(a.source_key) for a in registry.aliases)
            or not all(isinstance(t, VerifiedTerms) and _key(t.option_key) for t in registry.terms)
        ):
            return _unavailable("INVALID_REGISTRY")
        if not request.rows:
            return _unavailable("MISSING_TERMS_ROW")
        if any(row.option_key != request.option_key for row in request.rows):
            return _unavailable("UNEXPECTED_TERMS_ROW")
        if len(request.rows) != 1:
            return _unavailable("DUPLICATE_TERMS_ROW")
        row = request.rows[0]
        if type(row.trade_date) is not date or row.trade_date != request.trade_date:
            return _unavailable("MISMATCHED_TRADE_DATE")
        ingested_utc = _utc(row.ingested_at)
        if ingested_utc is None:
            return _unavailable("INVALID_TERMS_ROW")
        if ingested_utc > read_utc:
            return _unavailable("FUTURE_INGESTED_AT")
        if not _token(row.contract_version):
            return _unavailable("MISSING_CONTRACT_VERSION")
        if (
            not _key(row.underlying_key)
            or type(row.expiry_date) is not date
            or row.trade_date > row.expiry_date
            or row.option_type not in ("CALL", "PUT")
            or row.exercise_style not in ("UNKNOWN", ExerciseStyle.AMERICAN.value, ExerciseStyle.EUROPEAN.value)
            or not _positive(row.strike)
            or not _positive(row.contract_size)
        ):
            return _unavailable("INVALID_TERMS_ROW")

        terms_alias, error = _resolve_alias(row.underlying_key, registry, request.trade_date, read_utc)
        if terms_alias is None:
            return _unavailable(error or "MISSING_ALIAS")
        quote_alias, error = _resolve_alias(request.underlying_quote_key, registry, request.trade_date, read_utc)
        if quote_alias is None:
            return _unavailable(error or "MISSING_ALIAS")
        if terms_alias.identity != quote_alias.identity:
            return _unavailable("UNDERLYING_IDENTITY_CONFLICT")

        terms, error = _resolve_terms(row.option_key, registry, request.trade_date, read_utc)
        if terms is None:
            return _unavailable(error or "MISSING_TERMS_EVIDENCE")
        if terms.underlying != terms_alias.identity:
            return _unavailable("UNDERLYING_IDENTITY_CONFLICT")
        if row.contract_version != terms.contract_version:
            return _unavailable("CONTRACT_VERSION_CONFLICT")
        if (
            row.expiry_date != terms.expiry_date
            or row.option_type != terms.option_type
            or row.strike != terms.strike
            or row.contract_size != terms.contract_size
            or (row.exercise_style != "UNKNOWN" and row.exercise_style != terms.exercise_style.value)
        ):
            return _unavailable("TERMS_CONFLICT")

        # Content-address the exact reviewed records, rather than concatenate
        # unescaped identifiers. This is lineage, not a source signature.
        provenance = sha256(json.dumps(
            [registry.version, request.trade_date, asdict(terms_alias), asdict(quote_alias), asdict(terms)],
            default=str, sort_keys=True, separators=(",", ":"), ensure_ascii=True,
        ).encode("ascii")).hexdigest()
        selection = HistoricalLegSelection(
            option_symbol=row.option_key.symbol,
            option_exchange=row.option_key.exchange,
            underlying_contract_id=row.underlying_key.symbol,
            underlying_quote_symbol=request.underlying_quote_key.symbol,
            underlying_quote_exchange=request.underlying_quote_key.exchange,
            product_family_id=terms.underlying.product_family_id,
            asset_class=terms.underlying.asset_class,
            mapping_provenance=f"D02:{provenance}",
        )
        return HistoricalContractResult(
            ResearchValuationStatus.OK,
            contract=ValidatedHistoricalContract(
                request.trade_date, request.read_at, registry.version, row,
                terms, terms_alias, quote_alias, selection,
            ),
        )
