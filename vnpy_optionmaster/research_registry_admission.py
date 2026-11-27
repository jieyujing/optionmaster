"""M1-D03: content-bound admission of an independently reviewed D02 registry.

Trust root (approval pin and reviewer authority) belongs to operator config,
not to a valuation request. Hash checks prove byte integrity, NOT authenticity
of an exchange/vendor document or authority of the named reviewer.
No production pins, file discovery, network fetches or default approval.
"""
from collections.abc import Mapping
from dataclasses import asdict, dataclass
from datetime import date, datetime, timezone
from hashlib import sha256
import json

from vnpy_optionmaster.research_contract_terms import (
    AliasAttestation,
    ContractEvidence,
    EvidenceKind,
    HistoricalContractRegistry,
    VerifiedTerms,
)
from vnpy_optionmaster.research_risk import ResearchValuationStatus


# Conservative local processing envelope for a small, reviewed registry.
MAX_REGISTRY_RECORDS = 2048
MAX_EVIDENCE_BYTES = 32 * 1024 * 1024


@dataclass(frozen=True)
class RegistryApproval:
    """Externally approved exact fingerprint; never supplied by read requests."""

    registry_sha256: str
    approved_by: str
    approved_at: datetime


@dataclass(frozen=True)
class RegistryAdmissionResult:
    status: ResearchValuationStatus
    reason_code: str | None = None
    registry: HistoricalContractRegistry | None = None
    registry_sha256: str | None = None


def _date_json(value: object) -> str:
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    raise TypeError("Unsupported registry field")


def registry_fingerprint(registry: HistoricalContractRegistry) -> str:
    """Fingerprint v1: exact field values and tuple order, canonical JSON."""
    data = json.dumps(
        {"schema": "optionmaster-d02-registry-v1", "registry": asdict(registry)},
        default=_date_json, ensure_ascii=True, sort_keys=True,
        separators=(",", ":"), allow_nan=False,
    ).encode("ascii")
    return sha256(data).hexdigest()


def _reject(reason: str) -> RegistryAdmissionResult:
    return RegistryAdmissionResult(ResearchValuationStatus.UNAVAILABLE, reason)


def _token(value: object) -> bool:
    return isinstance(value, str) and bool(value) and value.isprintable() and not any(c.isspace() for c in value)


def _digest(value: object) -> bool:
    return isinstance(value, str) and len(value) == 64 and all(c in "0123456789abcdef" for c in value)


def _utc(value: object) -> datetime | None:
    if not isinstance(value, datetime):
        return None
    try:
        if value.tzinfo is None or value.utcoffset() is None:
            return None
        return value.astimezone(timezone.utc)
    except (OverflowError, ValueError, TypeError):
        return None


def admit_registry(
    registry: HistoricalContractRegistry,
    approval: RegistryApproval | None,
    artifacts: Mapping[str, bytes],
    read_at: datetime,
) -> RegistryAdmissionResult:
    """Admit only the externally pinned manifest and exact reviewed artifacts.

    This is a content-integrity gate. D02 still validates active records and
    their semantics per requested date. Operator review must establish the
    actual source authority and bind each record to its source artifact.
    """
    if approval is None:
        return _reject("REGISTRY_NOT_APPROVED")
    if (
        not isinstance(approval, RegistryApproval)
        or not _digest(approval.registry_sha256)
        or not _token(approval.approved_by)
    ):
        return _reject("INVALID_APPROVAL")
    read_utc, approved_utc = _utc(read_at), _utc(approval.approved_at)
    if read_utc is None or approved_utc is None or approved_utc > read_utc:
        return _reject("INVALID_APPROVAL")
    if (
        not isinstance(registry, HistoricalContractRegistry)
        or not _token(registry.version)
        or not isinstance(registry.aliases, tuple)
        or not isinstance(registry.terms, tuple)
        or not registry.aliases
        or not registry.terms
        or len(registry.aliases) + len(registry.terms) > MAX_REGISTRY_RECORDS
        or not all(isinstance(a, AliasAttestation) for a in registry.aliases)
        or not all(isinstance(t, VerifiedTerms) for t in registry.terms)
    ):
        return _reject("INVALID_REGISTRY")
    try:
        fingerprint = registry_fingerprint(registry)
    except (TypeError, ValueError, OverflowError, RecursionError):
        return _reject("INVALID_REGISTRY")
    if fingerprint != approval.registry_sha256:
        return _reject("REGISTRY_DIGEST_MISMATCH")

    expected: set[str] = set()
    receipts = tuple(a.evidence for a in registry.aliases) + tuple(t.evidence for t in registry.terms)
    for evidence in receipts:
        if (
            not isinstance(evidence, ContractEvidence)
            or not isinstance(evidence.kind, EvidenceKind)
            or not _digest(evidence.content_sha256)
        ):
            return _reject("INVALID_EVIDENCE")
        reviewed = _utc(evidence.reviewed_at)
        if reviewed is None or reviewed > approved_utc:
            return _reject("INVALID_EVIDENCE")
        expected.add(evidence.content_sha256)

    if not isinstance(artifacts, Mapping) or len(artifacts) != len(expected) or set(artifacts) != expected:
        return _reject("EVIDENCE_SET_MISMATCH")
    total = 0
    for digest in expected:
        artifact = artifacts[digest]
        if type(artifact) is not bytes or not artifact:
            return _reject("INVALID_EVIDENCE_BYTES")
        total += len(artifact)
        if total > MAX_EVIDENCE_BYTES:
            return _reject("EVIDENCE_BUDGET_EXCEEDED")
        if sha256(artifact).hexdigest() != digest:
            return _reject("EVIDENCE_DIGEST_MISMATCH")
    return RegistryAdmissionResult(
        ResearchValuationStatus.OK, registry=registry, registry_sha256=fingerprint,
    )
