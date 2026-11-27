"""D08 actual archived evidence, independent-pin refusal and selection engine.

Positive admission tests use an explicitly TEST_ONLY trust root. No approval
artifact is written and no operator approval of the expanded chain is claimed.
"""
from dataclasses import replace
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo
import json

import pytest

from vnpy_optionmaster.research_archived_valuation import ArchivedValuationPolicy, ArchivedValuationRequest
from vnpy_optionmaster.research_chain_archive import (
    OFFICIAL, OBSERVATION, ChainEvidenceError, admit_chain_selection, build_chain_candidate,
    discover_chain, selection_for_symbols,
)
from vnpy_optionmaster.research_chain_valuation import evaluate_chain_valuation
from vnpy_optionmaster.research_risk import ResearchValuationStatus
from vnpy_optionmaster.research_verified_slice import SliceApproval, SliceStatus

ROOT = Path(__file__).resolve().parents[1] / "docs/evidence"
D04 = ROOT / "m1-d04/shfe-20220907"
D05 = ROOT / "m1-d05/cu2211-20220907"
D08 = ROOT / "m1-d08/cu2211-20220907"
PIN = "9216e5598c11ef2736c353dddd08bd7c2efedf742935a9a7ee9afd8383ee8846"
SYMBOLS = ("CU2211C60000.XSGE", "CU2211C62000.XSGE", "CU2211P60000.XSGE", "CU2211P62000.XSGE")
ZONE = ZoneInfo("Asia/Shanghai")


@pytest.fixture(scope="module")
def source():
    evidence = {name: (D04 / name).read_bytes() for name in OFFICIAL}
    evidence[OBSERVATION] = (D08 / OBSERVATION).read_bytes()
    packet = (D08 / "chain_candidate.json").read_bytes()
    candidate = json.loads(packet)
    observed = datetime.fromisoformat(candidate["observed_at"])
    approval = SliceApproval(PIN, "TEST_ONLY_NOT_OPERATOR", observed + timedelta(seconds=1))
    return evidence, packet, candidate, approval, observed + timedelta(seconds=2)


def test_actual_discovery_counts_and_exact_candidate(source):
    evidence, packet, candidate, _, _ = source
    assert build_chain_candidate(evidence) == candidate == json.loads(packet)
    assert candidate["candidate_packet_sha256"] == PIN
    assert candidate["matched_contract_count"] == 66
    assert candidate["official_contract_count"] == 340
    assert candidate["missing_count"] == candidate["ambiguous_count"] == candidate["mismatch_count"] == 0
    assert not candidate["historical_input_admitted"] and not candidate["production_valuation_ready"]
    catalog = discover_chain(evidence)
    assert catalog == json.loads((D08 / "official_chain_discovery.json").read_bytes())
    assert sum(x["admission_status"] == "PENDING_CHAIN_APPROVAL" for x in catalog["contracts"]) == 64
    assert sum(x["admission_status"] == "D04_APPROVED_EXACT_PAIR_ONLY" for x in catalog["contracts"]) == 2
    assert sum(x["admission_status"] == "OFFICIAL_DISCOVERY_ONLY" for x in catalog["contracts"]) == 274
    assert all(x["source_symbol"] is None for x in catalog["contracts"] if x["underlying_native"] != "cu2211")


def test_actual_candidate_without_operator_pin_is_unavailable(source):
    evidence, packet, _, _, read_at = source
    result = admit_chain_selection(selection_for_symbols(SYMBOLS), packet, evidence, None, read_at)
    assert result.status is SliceStatus.UNAVAILABLE
    assert result.reason_code == "SLICE_NOT_APPROVED" and result.slice is None


def test_original_d04_pin_cannot_approve_new_chain(source):
    evidence, packet, _, _, read_at = source
    cfg = json.loads((D04 / "operator_slice_approval.json").read_bytes())
    approval = SliceApproval(cfg["packet_sha256"], cfg["approved_by"], datetime.fromisoformat(cfg["approved_at"]))
    result = admit_chain_selection(selection_for_symbols(SYMBOLS), packet, evidence, approval, read_at)
    assert result.reason_code == "PACKET_DIGEST_MISMATCH"


@pytest.mark.parametrize("n", [2, 4, 8])
def test_general_selection_test_only_trust_root_preserves_order_and_marks(source, n):
    evidence, packet, candidate, approval, read_at = source
    keys = tuple(b["raw_terms"]["symbol"] for b in candidate["bindings"] if 59000 <= b["official"]["strike"] <= 62000)
    assert len(keys) == 8
    chosen = tuple(reversed(keys[:n]))
    result = admit_chain_selection(selection_for_symbols(chosen), packet, evidence, approval, read_at)
    assert result.status is SliceStatus.ADMITTED_REFERENCE, result.reason_code
    actual = result.slice
    assert actual is not None and not actual.valuation_ready
    assert actual.approved_by == "TEST_ONLY_NOT_OPERATOR"
    assert tuple(x.raw_terms.option_key.symbol for x in actual.legs) == chosen
    assert all(x.raw_terms.contract_version == "" and x.raw_terms.exercise_style == "UNKNOWN" for x in actual.legs)
    assert all(x.contract.terms.exercise_style.value == "AMERICAN" for x in actual.legs)
    by_symbol = {b["raw_terms"]["symbol"]: b for b in candidate["bindings"]}
    assert [r.option_price for r in actual.basis.legs] == [by_symbol[s]["raw_quote"]["settlement"] for s in chosen]
    assert all(r.underlying_price == 60580 for r in actual.basis.legs)


@pytest.mark.parametrize("symbols", [("A",), ("A", "A"), tuple(str(x) for x in range(9))])
def test_selection_bounds(symbols):
    with pytest.raises(ChainEvidenceError):
        selection_for_symbols(symbols)


def test_unknown_exact_key_rejected(source):
    evidence, packet, _, approval, read_at = source
    req = selection_for_symbols(("CU2211C60000.XSGE", "CU2212C60000.XSGE"))
    assert admit_chain_selection(req, packet, evidence, approval, read_at).reason_code == "SLICE_SCOPE_MISMATCH"


def test_wrong_portfolio_and_day_rejected(source):
    evidence, packet, _, approval, read_at = source
    req = selection_for_symbols(SYMBOLS)
    for wrong in (replace(req, trade_date=req.trade_date + timedelta(days=1)),
                  replace(req, legs=(replace(req.legs[0], portfolio_code="wrong"), *req.legs[1:]))):
        assert admit_chain_selection(wrong, packet, evidence, approval, read_at).reason_code == "SLICE_SCOPE_MISMATCH"


def test_tampering_fail_closed(source):
    evidence, packet, _, approval, read_at = source
    req = selection_for_symbols(SYMBOLS)
    modified = dict(evidence)
    modified["option_daily.json"] += b" "
    assert admit_chain_selection(req, packet, modified, approval, read_at).reason_code == "OFFICIAL_DIGEST_MISMATCH"
    forged = json.loads(packet)
    forged["bindings"][0]["raw_quote"]["settlement"] = 1
    assert admit_chain_selection(req, json.dumps(forged).encode(), evidence, approval, read_at).reason_code == "PACKET_DIGEST_MISMATCH"


def test_observation_wire_tampering_fail_closed(source):
    evidence, packet, _, approval, read_at = source
    modified = dict(evidence)
    obs = json.loads(evidence[OBSERVATION])
    obs["queries"]["quotes_0"]["response"]["body"] += " "
    modified[OBSERVATION] = json.dumps(obs).encode()
    assert admit_chain_selection(selection_for_symbols(SYMBOLS), packet, modified, approval, read_at).reason_code == "WIRE_DIGEST_MISMATCH"


def test_approval_chronology(source):
    evidence, packet, _, approval, read_at = source
    req = selection_for_symbols(SYMBOLS)
    for at in (approval.approved_at - timedelta(days=1), read_at + timedelta(seconds=1), approval.approved_at.replace(tzinfo=None)):
        assert admit_chain_selection(req, packet, evidence, replace(approval, approved_at=at), read_at).status is SliceStatus.UNAVAILABLE


def test_four_leg_pricing_only_with_test_fixture_approval(source):
    evidence, packet, candidate, approval, read_at = source
    cfg = json.loads((D05 / "research_valuation_policy.json").read_bytes())
    policy = ArchivedValuationPolicy(cfg["clock_policy"], cfg["day_count_basis"], cfg["rate_policy"],
                                    datetime.fromisoformat(cfg["rate_date"]).date(), cfg["rate_source_uri"],
                                    cfg["rate_artifact_sha256"], 64)
    rate = (D05 / cfg["rate_artifact"]).read_bytes()
    quantities = (1, -1, -1, 1)
    req = ArchivedValuationRequest(selection_for_symbols(SYMBOLS), quantities,
                                   datetime(2022, 9, 7, 15, tzinfo=ZONE),
                                   (-0.05, 0.0, 0.05), (-0.05, 0.0, 0.05), (-0.03, 0.0, 0.03))
    denied = evaluate_chain_valuation(req, packet, evidence, None, rate, policy, read_at)
    assert denied.status is ResearchValuationStatus.UNAVAILABLE and denied.reason_code == "SLICE_NOT_APPROVED"
    allowed = evaluate_chain_valuation(req, packet, evidence, approval, rate, policy, read_at)
    assert allowed.status is ResearchValuationStatus.OK, allowed.reason_code
    a = allowed.valuation
    assert a is not None and not a.production_valuation_ready
    marks = {b["raw_terms"]["symbol"]: b["raw_quote"]["settlement"] for b in candidate["bindings"]}
    expected = sum(q * marks[s] * 5 for q, s in zip(quantities, SYMBOLS, strict=True))
    assert a.grid.baseline_value == pytest.approx(expected, abs=0.001)
    assert a.grid.configured_value_change == 0
    assert len(a.calibrations) == 4
    assert all(len(cell.contributions) == 4 for row in a.grid.heatmap_rows for cell in row)
