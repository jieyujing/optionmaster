"""D09 cross-expiry research uses explicit observed source scope.

No test fixture approval is an operator approval. Actual archived inputs
are compared against the original exchange bytes before selection.
"""
from dataclasses import replace
from datetime import date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo
import json

import pytest

from vnpy_optionmaster.research_chain_archive import build_chain_candidate, ChainEvidenceError
from vnpy_optionmaster.research_multi_chain_archive import (
    CatalogSelection, admit_catalog_selection, build_catalog_candidate, load_pilot_sources,
)
from vnpy_optionmaster.research_catalog_valuation import (
    CatalogValuationRequest, evaluate_catalog_valuation, load_pilot_rate,
)
from vnpy_optionmaster.research_verified_slice import SliceApproval, SliceStatus
from vnpy_optionmaster.research_risk import ResearchValuationStatus

ROOT = Path(__file__).resolve().parents[1] / "docs/evidence"


def test_two_new_actual_chain_scopes_and_old_exact_candidate():
    sources = load_pilot_sources(ROOT)
    assert len(sources) == 3
    old = sources[0]
    assert build_chain_candidate(old.evidence) == json.loads(old.packet)
    candidate = build_catalog_candidate(sources)
    assert candidate["matched_contract_dates"] == 198
    assert candidate["new_contract_dates"] == 132
    assert candidate["missing_count"] == candidate["ambiguous_count"] == candidate["mismatch_count"] == 0
    assert candidate["historical_input_admitted"] is False
    assert candidate["production_valuation_ready"] is False
    assert [(s["trade_date"], s["underlying"], s["matched_count"]) for s in candidate["chains"]] == [
        ("2022-09-07", "cu2211", 66), ("2022-09-07", "cu2212", 66), ("2022-09-08", "cu2211", 66),
    ]
    assert date.fromisoformat(candidate["chains"][1]["expiry_date"]) == date(2022, 11, 24)

# This fixture uses real archived inputs but a TEST-ONLY trust root.
# It must never be used by the local launcher or written as operator approval.
PIN = "2c57d7df89e87db37749d4217c68117bd1d3ccc4c332bf1cfa41b4fb28264fea"
DAY = date(2022, 9, 7)
NEXT_DAY = date(2022, 9, 8)
NEAR = "CU2211C61000.XSGE"
FAR = "CU2212C61000.XSGE"
ZONE = ZoneInfo("Asia/Shanghai")


@pytest.fixture(scope="module")
def catalog():
    sources = load_pilot_sources(ROOT)
    packet = (ROOT / "m1-d09/catalog_candidate.json").read_bytes()
    candidate = build_catalog_candidate(sources)
    assert candidate == json.loads(packet) and candidate["candidate_packet_sha256"] == PIN
    observed = datetime.fromisoformat(candidate["observed_at"])
    approval = SliceApproval(PIN, "TEST_ONLY_NOT_OPERATOR", observed + timedelta(seconds=1))
    return sources, packet, approval, observed + timedelta(seconds=2)


def test_no_new_operator_approval_no_admission(catalog):
    sources, packet, _, read_at = catalog
    result = admit_catalog_selection(CatalogSelection(DAY, (NEAR, FAR)), packet, sources, None, read_at)
    assert result.status is SliceStatus.UNAVAILABLE and result.reason_code == "SLICE_NOT_APPROVED"
    assert result.portfolio is None


def test_actual_two_month_marks_and_terms_test_only(catalog):
    sources, packet, approval, read_at = catalog
    result = admit_catalog_selection(CatalogSelection(DAY, (FAR, NEAR)), packet, sources, approval, read_at)
    assert result.status is SliceStatus.ADMITTED_REFERENCE, result.reason_code
    a = result.portfolio
    assert a is not None and a.approved_by == "TEST_ONLY_NOT_OPERATOR" and not a.production_valuation_ready
    assert [leg.raw_terms.option_key.symbol for leg in a.legs] == [FAR, NEAR]
    assert [leg.contract.terms.expiry_date for leg in a.legs] == [date(2022, 11, 24), date(2022, 10, 25)]
    assert [(leg.option_price, leg.underlying_price) for leg in a.basis.legs] == [(2600, 60240), (2230, 60580)]
    assert {r.symbol for r in a.underlying_quotes} == {"SHFE.cu2211", "SHFE.cu2212"}
    assert len(a.child_packet_sha256) == 2


@pytest.mark.parametrize("selection", [
    CatalogSelection(NEXT_DAY, (NEAR, FAR)),
    CatalogSelection(date(2022, 9, 9), (NEAR, FAR)),
    CatalogSelection(DAY, (NEAR, NEAR)),
    CatalogSelection(DAY, (NEAR,)),
    CatalogSelection(DAY, tuple(str(n) for n in range(9))),
])
def test_invalid_selection_rejected(catalog, selection):
    sources, packet, approval, read_at = catalog
    r = admit_catalog_selection(selection, packet, sources, approval, read_at)
    assert r.status is SliceStatus.UNAVAILABLE and r.reason_code == "SLICE_SCOPE_MISMATCH"
    assert r.portfolio is None


def test_old_pin_does_not_approve_new_catalog(catalog):
    sources, packet, approval, read_at = catalog
    old = replace(approval, packet_sha256="9216e5598c11ef2736c353dddd08bd7c2efedf742935a9a7ee9afd8383ee8846")
    result = admit_catalog_selection(CatalogSelection(DAY, (NEAR, FAR)), packet, sources, old, read_at)
    assert result.reason_code == "PACKET_DIGEST_MISMATCH"


def test_actual_calendar_spread_value_and_distinct_greeks_test_only(catalog):
    sources, packet, approval, read_at = catalog
    policy, rate = load_pilot_rate(ROOT, DAY)
    request = CatalogValuationRequest(
        CatalogSelection(DAY, (NEAR, FAR)), (-1, 1),
        datetime(2022, 9, 8, 15, tzinfo=ZONE),
        (-0.05, 0.0, 0.05), (-0.05, 0.0, 0.05), (-0.03, 0.0, 0.03),
    )
    result = evaluate_catalog_valuation(request, packet, sources, approval, rate, policy, read_at)
    assert result.status is ResearchValuationStatus.OK, result.reason_code
    a = result.valuation
    assert a is not None and not a.production_valuation_ready
    assert a.portfolio.approved_by == "TEST_ONLY_NOT_OPERATOR"
    assert a.grid.baseline_value == pytest.approx(1850, abs=0.001)
    assert a.rate.quoted_percent == 1.4442
    assert [r.baseline_time_to_expiry * 365 for r in a.grid.configured_contributions] == pytest.approx([48, 78])
    assert [r.scenario_time_to_expiry * 365 for r in a.grid.configured_contributions] == pytest.approx([47, 77])
    assert {e.underlying_id for e in a.exposures} == {"SHFE.cu2211", "SHFE.cu2212"}
    assert len(a.exposures) == 2
    assert a.grid.heatmap_rows[1][1].value_change == a.grid.configured_value_change
    assert a.grid.configured_value_change != 0
    assert all(len(c.contributions) == 2 for row in a.grid.heatmap_rows for c in row)

def base_request(day=DAY, symbols=(NEAR, FAR), quantities=(-1, 1)):
    return CatalogValuationRequest(
        CatalogSelection(day, symbols), quantities,
        datetime.combine(day, datetime(2022, 9, 7, 15).time(), tzinfo=ZONE),
        (-0.05, 0.0, 0.05), (-0.05, 0.0, 0.05), (-0.03, 0.0, 0.03),
    )


@pytest.mark.parametrize("n,expected", [(4, 5390), (8, 3700)])
def test_four_and_eight_leg_multiple_months(catalog, n, expected):
    sources, packet, approval, read_at = catalog
    policy, rate = load_pilot_rate(ROOT, DAY)
    # Independently observed SHFE marks; never derive expected value from model output.
    symbols = (NEAR, FAR, "CU2211P61000.XSGE", "CU2212P61000.XSGE",
               "CU2211C60000.XSGE", "CU2212C60000.XSGE",
               "CU2211P60000.XSGE", "CU2212P60000.XSGE")[:n]
    quantities = (-1, 1, -1, 1, -1, 1, 1, -1)[:n]
    marks = (2230, 2600, 2650, 3358, 2714, 3060, 2136, 2820)[:n]
    # 4-leg calendar: (370+708)*5=5390; 8-leg adds (346-684)*5.
    assert expected == sum(q * mark * 5 for q, mark in zip(quantities, marks, strict=True))
    req = base_request(symbols=symbols, quantities=quantities)
    result = evaluate_catalog_valuation(req, packet, sources, approval, rate, policy, read_at)
    assert result.status is ResearchValuationStatus.OK, result.reason_code
    a = result.valuation
    assert a is not None and a.grid.baseline_value == pytest.approx(expected, abs=0.001)
    assert len(a.exposures) == 2 and len(a.calibrations) == n
    assert all(len(c.contributions) == n for row in a.grid.heatmap_rows for c in row)


def test_day_specific_rate_and_next_day_archive_marks(catalog):
    sources, packet, approval, read_at = catalog
    policy, rate = load_pilot_rate(ROOT, NEXT_DAY)
    req = base_request(NEXT_DAY, (NEAR, "CU2211P61000.XSGE"), (1, 1))
    r = evaluate_catalog_valuation(req, packet, sources, approval, rate, policy, read_at)
    assert r.status is ResearchValuationStatus.OK, r.reason_code
    a = r.valuation
    assert a is not None and a.rate.quote_date == DAY and a.rate.quoted_percent == 1.4628
    assert a.grid.baseline_value == pytest.approx(23280, abs=0.001)
    assert all(c.baseline_time_to_expiry * 365 == pytest.approx(47) for c in a.grid.configured_contributions)
    r = evaluate_catalog_valuation(req, packet, sources, approval, rate, replace(policy, rate_date=date(2022, 9, 6)), read_at)
    assert r.reason_code == "RATE_DATE_UNAVAILABLE" and r.valuation is None


def test_independent_underlying_and_expiry_shocks(catalog):
    sources, packet, approval, read_at = catalog
    policy, rate = load_pilot_rate(ROOT, DAY)
    req = replace(base_request(), price_shocks_by_underlying=(("SHFE.cu2211", 0.02),),
                  iv_shocks_by_expiry=(("SHFE|cu2212|2022-11-24", 0.03),))
    r = evaluate_catalog_valuation(req, packet, sources, approval, rate, policy, read_at)
    assert r.status is ResearchValuationStatus.OK, r.reason_code
    a = r.valuation
    assert a is not None
    near, far = a.grid.configured_contributions
    assert near.scenario_iv == pytest.approx(near.baseline_iv)
    assert far.scenario_iv == pytest.approx(far.baseline_iv + 0.03)
    assert near.scenario_value != near.baseline_value and far.scenario_value != far.baseline_value
    assert a.grid.heatmap_rows[1][1].value_change == a.grid.configured_value_change
    unknown = replace(req, price_shocks_by_underlying=(("SHFE.cu2210", 0.02),))
    r = evaluate_catalog_valuation(unknown, packet, sources, approval, rate, policy, read_at)
    assert r.reason_code == "UNKNOWN_SHOCK_TARGET" and r.valuation is None


@pytest.mark.parametrize("changes,reason", [
    ({"scenario_at": datetime(2022, 10, 25, 0, tzinfo=ZONE)}, "EXPIRED_SCENARIO"),
    ({"scenario_at": datetime(2022, 9, 7, 14, 59, tzinfo=ZONE)}, "INVALID_SCENARIO_TIME"),
    ({"quantities": (1, 0)}, "INVALID_INPUT"),
    ({"quantities": (True, 1)}, "INVALID_INPUT"),
    ({"heatmap_iv_shocks": (0.0, 0.0)}, "INVALID_AXES"),
    ({"heatmap_iv_shocks": tuple(float(i) / 100 for i in range(32))}, "GRID_LIMIT_EXCEEDED"),
])
def test_atomic_cross_expiry_rejection(catalog, changes, reason):
    sources, packet, approval, read_at = catalog
    policy, rate = load_pilot_rate(ROOT, DAY)
    result = evaluate_catalog_valuation(replace(base_request(), **changes), packet, sources, approval, rate, policy, read_at)
    assert result.status is ResearchValuationStatus.UNAVAILABLE and result.reason_code == reason
    assert result.valuation is None


def test_intrinsic_only_refusal_and_no_approval(catalog):
    sources, packet, approval, read_at = catalog
    policy, rate = load_pilot_rate(ROOT, DAY)
    result = evaluate_catalog_valuation(base_request(), packet, sources, None, rate, policy, read_at)
    assert result.reason_code == "SLICE_NOT_APPROVED" and result.valuation is None
    req = base_request(symbols=("CU2211P79000.XSGE", FAR))
    result = evaluate_catalog_valuation(req, packet, sources, approval, rate, policy, read_at)
    assert result.reason_code == "IV_UNAVAILABLE" and result.valuation is None


def test_catalog_source_tampering_rejected(catalog):
    sources, packet, approval, read_at = catalog
    tampered = dict(sources[1].evidence)
    tampered["option_daily.json"] += b" "
    bad = (sources[0], replace(sources[1], evidence=tampered), sources[2])
    with pytest.raises(ChainEvidenceError, match="OFFICIAL_DIGEST_MISMATCH"):
        build_catalog_candidate(bad)
    result = admit_catalog_selection(CatalogSelection(DAY, (NEAR, FAR)), packet, bad, approval, read_at)
    assert result.status is SliceStatus.UNAVAILABLE and result.reason_code == "OFFICIAL_DIGEST_MISMATCH"
    changed = json.loads(packet)
    changed["chains"][1]["matched_count"] = 1
    result = admit_catalog_selection(CatalogSelection(DAY, (NEAR, FAR)), json.dumps(changed).encode(), sources, approval, read_at)
    assert result.reason_code == "PACKET_DIGEST_MISMATCH"


def test_old_pair_pricing_parity_with_actual_d08_browser_result(catalog):
    sources, packet, approval, read_at = catalog
    policy, rate = load_pilot_rate(ROOT, DAY)
    old = json.loads((ROOT / "m1-d08/cu2211-20220907/actual_chain_api_result.json").read_bytes())["two_leg"]["doc"]["valuation"]
    g = old["grid"]
    req = CatalogValuationRequest(
        CatalogSelection(DAY, (NEAR, "CU2211P61000.XSGE")), (1, 1),
        datetime(2022, 9, 7, 15, tzinfo=ZONE), tuple(g["curve_price_axis"]),
        tuple(g["heatmap_price_axis"]), tuple(g["heatmap_iv_axis"]),
    )
    result = evaluate_catalog_valuation(req, packet, sources, approval, rate, policy, read_at)
    assert result.status is ResearchValuationStatus.OK, result.reason_code
    a = result.valuation
    assert a is not None
    assert a.grid.baseline_value == g["baseline_value"]
    assert [r.implied_volatility for r in a.calibrations] == [leg["implied_volatility"] for leg in old["legs"]]
    assert [p.value_change for p in a.grid.price_curve] == [p["value_change"] for p in g["price_curve"]]
    assert [[p.value_change for p in row] for row in a.grid.heatmap_rows] == [
        [p["value_change"] for p in row] for row in g["heatmap_rows"]
    ]
