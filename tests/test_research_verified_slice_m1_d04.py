"""Real archived CU2211 evidence acceptance; no live or synthetic data claim."""
from dataclasses import replace
from datetime import date, datetime, timezone
from hashlib import sha256
import json
from pathlib import Path

import pytest

from vnpy_optionmaster.research_contract_terms import SourceKey, ExerciseStyle
from vnpy_optionmaster.research_data_basis import HistoricalBasis
from vnpy_optionmaster.research_history_repository import HistoricalFuturesLeg, HistoricalReadRequest
from vnpy_optionmaster.research_verified_slice import (
    SliceApproval, SliceStatus, admit_verified_slice,
)

ROOT = Path(__file__).resolve().parents[1] / "docs/evidence/m1-d04/shfe-20220907"
PIN = "85a27c0d97b836cf51d2f8db7ac92c1e9681138ffe477d0f324f4518276ad826"
DAY = date(2022, 9, 7)
APPROVED = datetime(2026, 11, 24, 6, 7, 45, tzinfo=timezone.utc)
READ = datetime(2026, 11, 24, 6, 15, 37, tzinfo=timezone.utc)
QUOTE = SourceKey("quantfeed.futures/JQ", "SHFE", "SHFE.cu2211")
REQUEST = HistoricalReadRequest(DAY, tuple(
    HistoricalFuturesLeg(
        "XSGE.CU2211.XSGE", "XSGE|CU2211.XSGE|2022-10-25",
        SourceKey("quantfeed.option/JQ", "XSGE", symbol), "SHFE.CU",
        QUOTE, "JOINQUANT_GET_EXTRAS:futures_sett_price",
    )
    for symbol in ("CU2211C61000.XSGE", "CU2211P61000.XSGE")
))


def inputs():
    packet = (ROOT / "vendor_binding_candidate.json").read_bytes()
    document = json.loads(packet)
    names = set(document["evidence_files_sha256"]) | set(document["bindings"][0]["official_artifacts"])
    evidence = {name: (ROOT / name).read_bytes() for name in names}
    return packet, evidence, SliceApproval(PIN, "OPERATOR_CHAT_APPROVAL", APPROVED)


def test_approved_real_slice_admits_historical_inputs_without_promoting_live_transport():
    packet, evidence, approval = inputs()
    result = admit_verified_slice(REQUEST, packet, evidence, approval, READ)
    assert result.status is SliceStatus.ADMITTED_REFERENCE
    accepted = result.slice
    assert accepted is not None
    assert accepted.packet_sha256 == PIN
    assert accepted.historical_input_admitted
    assert not accepted.valuation_ready
    assert accepted.consistency == "VERIFIED_OFFICIAL_ARCHIVED_SLICE"
    assert accepted.observed_at < accepted.approved_at <= accepted.admitted_at
    assert accepted.basis.price_basis is HistoricalBasis.SETTLEMENT
    assert [(x.option_price, x.underlying_price) for x in accepted.basis.legs] == [(2230, 60580), (2650, 60580)]
    for leg in accepted.legs:
        assert leg.raw_terms.contract_version == ""
        assert leg.raw_terms.exercise_style == "UNKNOWN"
        assert leg.contract.terms.exercise_style is ExerciseStyle.AMERICAN
        assert leg.contract.terms.contract_version.startswith("SHFE-REF:")
        assert leg.raw_terms is not leg.contract.raw_terms
    assert accepted.packet_bytes == packet
    assert len(accepted.registry_sha256) == 64

def test_actual_operator_approval_manifest_and_raw_source_lineage():
    packet, evidence, _ = inputs()
    cfg = json.loads((ROOT / "operator_slice_approval.json").read_bytes())
    assert cfg["schema"] == "optionmaster.archived_slice_approval.v1"
    assert cfg["packet_sha256"] == PIN
    assert cfg["approval_scope"] == "ARCHIVED_HISTORICAL_INPUTS_ONLY"
    assert cfg["production_tls_approved"] is False
    approval = SliceApproval(cfg["packet_sha256"], cfg["approved_by"], datetime.fromisoformat(cfg["approved_at"]))
    result = admit_verified_slice(REQUEST, packet, evidence, approval, READ)
    accepted = result.slice
    assert accepted is not None
    original = json.loads(packet)
    for leg, binding in zip(accepted.legs, original["bindings"], strict=True):
        assert json.loads(leg.raw_source_json) == binding["raw_terms"]
        assert leg.contract.terms.contract_version == binding["proposed_effective_revision"]
    assert dict(accepted.artifact_digests) == {
        **original["evidence_files_sha256"], **original["bindings"][0]["official_artifacts"],
    }
    later = admit_verified_slice(REQUEST, packet, evidence, approval, READ.replace(minute=16))
    assert later.slice is not None and later.slice.registry_sha256 == accepted.registry_sha256



def reject(request, packet, evidence, approval, read_at, reason):
    result = admit_verified_slice(request, packet, evidence, approval, read_at)
    assert result.status is SliceStatus.UNAVAILABLE
    assert result.reason_code == reason
    assert result.slice is None


def test_no_external_approval_is_fail_closed():
    packet, evidence, _ = inputs()
    reject(REQUEST, packet, evidence, None, READ, "SLICE_NOT_APPROVED")


@pytest.mark.parametrize("field,value", [
    ("contract_version", "fake"), ("exercise_style", "EUROPEAN"),
    ("strike", 62000), ("underlying_symbol", "CU2301.XSGE"),
    ("ingested_at", "2026-10-12T13:39:02.283000+00:00"),
])
def test_rehashing_changed_packet_cannot_reuse_operator_pin(field, value):
    packet, evidence, approval = inputs()
    doc = json.loads(packet)
    doc.pop("candidate_packet_sha256")
    doc["bindings"][0]["raw_terms"][field] = value
    canonical = json.dumps(doc, ensure_ascii=True, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()
    doc["candidate_packet_sha256"] = sha256(canonical).hexdigest()
    reject(REQUEST, json.dumps(doc).encode(), evidence, approval, READ, "PACKET_DIGEST_MISMATCH")


@pytest.mark.parametrize("name", [
    "cu_historical_contract_rule.html", "option_contract_base.json", "future_daily.json",
    "clickhouse_live_wire_observation.json", "clickhouse_catalog_calendar_observation.json",
    "reference_slice_result.json",
])
def test_tampered_artifact_is_never_admitted(name):
    packet, evidence, approval = inputs()
    evidence[name] += b" "
    reject(REQUEST, packet, evidence, approval, READ, "EVIDENCE_DIGEST_MISMATCH")


def test_missing_or_extra_evidence_is_rejected():
    packet, evidence, approval = inputs()
    name = next(iter(evidence))
    missing = {k: v for k, v in evidence.items() if k != name}
    reject(REQUEST, packet, missing, approval, READ, "EVIDENCE_SET_MISMATCH")
    reject(REQUEST, packet, {**evidence, "extra.json": b"{}"}, approval, READ, "EVIDENCE_SET_MISMATCH")


@pytest.mark.parametrize("read_request", [
    replace(REQUEST, trade_date=date(2022, 9, 8)),
    replace(REQUEST, legs=REQUEST.legs[:1]),
    replace(REQUEST, legs=REQUEST.legs[::-1]),
    replace(REQUEST, legs=(replace(REQUEST.legs[0], settlement_source="other"), REQUEST.legs[1])),
    replace(REQUEST, legs=(replace(REQUEST.legs[0], option_key=SourceKey("quantfeed.option/GM", "XSGE", "CU2211C61000.XSGE")), REQUEST.legs[1])),
])
def test_scope_cannot_be_reused_for_another_request(read_request):
    packet, evidence, approval = inputs()
    reject(read_request, packet, evidence, approval, READ, "SLICE_SCOPE_MISMATCH")


def test_approval_cannot_be_used_before_approval_or_before_observation():
    packet, evidence, approval = inputs()
    reject(REQUEST, packet, evidence, approval, APPROVED.replace(second=0), "INVALID_SLICE_APPROVAL")
    reject(REQUEST, packet, evidence, replace(approval, approved_at=APPROVED.replace(hour=5)), READ, "INVALID_SLICE_APPROVAL")


def test_malformed_json_and_duplicate_keys_fail_closed():
    packet, evidence, approval = inputs()
    reject(REQUEST, b'{"a":1,"a":2}', evidence, approval, READ, "INVALID_SLICE_EVIDENCE")
    reject(REQUEST, b"not json", evidence, approval, READ, "INVALID_SLICE_EVIDENCE")
