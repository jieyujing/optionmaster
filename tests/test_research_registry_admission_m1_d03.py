"""M1-D03 admission tests: fictional reviewed receipts, never a live registry."""
from dataclasses import replace
from datetime import date, datetime, timedelta, timezone
from hashlib import sha256

import pytest

from vnpy_optionmaster.research_combination import ResearchAssetClass
from vnpy_optionmaster.research_contract_terms import (
    AliasAttestation,
    ContractEvidence,
    EvidenceKind,
    ExerciseStyle,
    HistoricalContractRegistry,
    SourceKey,
    UnderlyingIdentity,
    VerifiedTerms,
)
from vnpy_optionmaster.research_registry_admission import (
    RegistryApproval,
    admit_registry,
    registry_fingerprint,
)
from vnpy_optionmaster.research_risk import ResearchValuationStatus

DAY = date(2022, 9, 7)
START = date(2022, 9, 7)
END = date(2022, 10, 14)
READ_AT = datetime(2026, 11, 24, 2, tzinfo=timezone.utc)
REVIEW_AT = datetime(2026, 10, 12, 2, tzinfo=timezone.utc)
APPROVED_AT = datetime(2026, 10, 13, 2, tzinfo=timezone.utc)
OPT = SourceKey("quantfeed.option/JQ", "XDCE", "A2211-C-5000.XDCE")
PUT = SourceKey("quantfeed.option/JQ", "XDCE", "A2211-P-5000.XDCE")
UND = SourceKey("quantfeed.option/JQ", "XDCE", "A2211.XDCE")
QUOTE = SourceKey("quantfeed.futures/JQ", "DCE", "DCE.a2211")
IDENTITY = UnderlyingIdentity("DCE", "DCE.A", "a2211", date(2022, 11, 1), ResearchAssetClass.FUTURES_OPTION)


def fixture_registry() -> tuple[HistoricalContractRegistry, RegistryApproval, dict[str, bytes]]:
    artifacts: dict[str, bytes] = {}

    def receipt(record_id: str) -> ContractEvidence:
        artifact = f"TEST ONLY synthetic reviewed artifact {record_id}".encode()
        digest = sha256(artifact).hexdigest()
        artifacts[digest] = artifact
        return ContractEvidence(
            EvidenceKind.VENDOR_CONTRACT_MASTER, "TEST_ONLY", "https://example.invalid/fixture",
            record_id, "fixture-r1", digest, "TEST_REVIEWER", REVIEW_AT,
        )

    registry = HistoricalContractRegistry(
        "fixture-registry-d03",
        (
            AliasAttestation(UND, IDENTITY, START, END, receipt("fixture-left")),
            AliasAttestation(QUOTE, IDENTITY, START, END, receipt("fixture-right")),
        ),
        (
            VerifiedTerms(OPT, "DCE", "a2211-c-5000", IDENTITY, START, END, END, "CALL",
                          ExerciseStyle.AMERICAN, 5000, 10, "fixture-call-v1", receipt("fixture-call")),
            VerifiedTerms(PUT, "DCE", "a2211-p-5000", IDENTITY, START, END, END, "PUT",
                          ExerciseStyle.AMERICAN, 5000, 10, "fixture-put-v1", receipt("fixture-put")),
        ),
    )
    # The pin is generated from fixtures ONLY; real pins must be external reviewed config.
    approval = RegistryApproval(registry_fingerprint(registry), "TEST_APPROVER", APPROVED_AT)
    return registry, approval, artifacts


def test_exact_pinned_registry_and_artifact_bytes_admit() -> None:
    registry, approval, artifacts = fixture_registry()
    result = admit_registry(registry, approval, artifacts, READ_AT)
    assert result.status is ResearchValuationStatus.OK
    assert result.registry == registry
    assert result.registry_sha256 == approval.registry_sha256


def test_no_approval_is_not_a_default_allow() -> None:
    registry, _, artifacts = fixture_registry()
    result = admit_registry(registry, None, artifacts, READ_AT)
    assert result.status is ResearchValuationStatus.UNAVAILABLE
    assert result.reason_code == "REGISTRY_NOT_APPROVED"
    assert result.registry is None


def test_same_version_but_altered_terms_cannot_reuse_approved_pin() -> None:
    registry, approval, artifacts = fixture_registry()
    changed = replace(registry, terms=(replace(registry.terms[0], strike=5050), registry.terms[1]))
    result = admit_registry(changed, approval, artifacts, READ_AT)
    assert result.status is ResearchValuationStatus.UNAVAILABLE
    assert result.reason_code == "REGISTRY_DIGEST_MISMATCH"
    assert result.registry is None


@pytest.mark.parametrize("mode", ["missing", "tampered", "extra"])
def test_exact_evidence_bytes_required(mode: str) -> None:
    registry, approval, artifacts = fixture_registry()
    first = next(iter(artifacts))
    if mode == "missing":
        del artifacts[first]
        reason = "EVIDENCE_SET_MISMATCH"
    elif mode == "tampered":
        artifacts[first] = b"unreviewed bytes"
        reason = "EVIDENCE_DIGEST_MISMATCH"
    else:
        artifacts["0" * 64] = b"unreviewed bytes"
        reason = "EVIDENCE_SET_MISMATCH"
    result = admit_registry(registry, approval, artifacts, READ_AT)
    assert result.status is ResearchValuationStatus.UNAVAILABLE
    assert result.reason_code == reason
    assert result.registry is None


def test_review_and_approval_temporal_order() -> None:
    registry, approval, artifacts = fixture_registry()
    future = replace(approval, approved_at=READ_AT + timedelta(seconds=1))
    early = replace(approval, approved_at=REVIEW_AT - timedelta(seconds=1))
    for invalid in (future, early, replace(approval, approved_at=APPROVED_AT.replace(tzinfo=None))):
        assert admit_registry(registry, invalid, artifacts, READ_AT).status is ResearchValuationStatus.UNAVAILABLE

def test_local_evidence_byte_budget_is_enforced(monkeypatch: pytest.MonkeyPatch) -> None:
    registry, approval, artifacts = fixture_registry()
    monkeypatch.setattr("vnpy_optionmaster.research_registry_admission.MAX_EVIDENCE_BYTES", 1)
    result = admit_registry(registry, approval, artifacts, READ_AT)
    assert result.reason_code == "EVIDENCE_BUDGET_EXCEEDED"
    assert result.registry is None
