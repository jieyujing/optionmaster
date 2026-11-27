"""D05: real, approved archive -> model, independent lattice and scenarios."""
from dataclasses import replace
from datetime import date, datetime, timedelta, timezone
from math import exp, log1p, sqrt
from pathlib import Path
from zoneinfo import ZoneInfo
import json

import numpy as np
import pytest
from scipy.optimize import brentq

from vnpy_optionmaster.research_contract_terms import SourceKey
from vnpy_optionmaster.research_history_repository import HistoricalFuturesLeg, HistoricalReadRequest
from vnpy_optionmaster.research_pricing import ResearchPricingModel
from vnpy_optionmaster.research_risk import ResearchValuationStatus
from vnpy_optionmaster.research_verified_slice import SliceApproval
from vnpy_optionmaster.research_archived_valuation import (
    ArchivedValuationPolicy,
    ArchivedValuationRequest,
    evaluate_archived_valuation,
)

ROOT = Path(__file__).resolve().parents[1] / "docs/evidence"
D04 = ROOT / "m1-d04/shfe-20220907"
D05 = ROOT / "m1-d05/cu2211-20220907"
ZONE = ZoneInfo("Asia/Shanghai")
DAY = date(2022, 9, 7)
CLOCK = datetime(2022, 9, 7, 15, tzinfo=ZONE)
ADMITTED = datetime(2026, 11, 24, 6, 34, 11, tzinfo=timezone.utc)
RATE_PIN = "8dabc429bb9cccbcc479813ae7743813602e1a6d5c67c8e8b40ea12b5971b0f1"
RATE_URL = (
    "https://yield.chinabond.com.cn/cbweb-pbc-web/pbc/historyQuery?"
    "endDate=2022-09-07&gjqx=0.25&locale=en_US&qxId=hzsylqx&startDate=2022-09-06"
)
RATE = log1p(0.014442 * 0.25) / 0.25
T = 48 / 365
F, K, MULTIPLIER = 60580, 61000, 5
READ = HistoricalReadRequest(DAY, tuple(
    HistoricalFuturesLeg(
        "XSGE.CU2211.XSGE", "XSGE|CU2211.XSGE|2022-10-25",
        SourceKey("quantfeed.option/JQ", "XSGE", symbol),
        "SHFE.CU", SourceKey("quantfeed.futures/JQ", "SHFE", "SHFE.cu2211"),
        "JOINQUANT_GET_EXTRAS:futures_sett_price",
    ) for symbol in ("CU2211C61000.XSGE", "CU2211P61000.XSGE")
))
POLICY = ArchivedValuationPolicy(
    clock_policy="EOD_NOMINAL_1500_DATE_ONLY", day_count_basis="ACT/365F",
    rate_policy="PRIOR_DAY_CGB_3M_YTM_FLAT_SIMPLE_PROXY",
    rate_date=date(2022, 9, 6), rate_source_uri=RATE_URL,
    rate_artifact_sha256=RATE_PIN, tree_steps=128,
)
REQUEST = ArchivedValuationRequest(
    READ, (1, 1), CLOCK, (-0.05, 0.0, 0.05),
    (-0.05, 0.0, 0.05), (-0.03, 0.0, 0.03),
)


def source_inputs():
    cfg = json.loads((D04 / "operator_slice_approval.json").read_bytes())
    packet = (D04 / "vendor_binding_candidate.json").read_bytes()
    doc = json.loads(packet)
    names = set(doc["evidence_files_sha256"]) | set(doc["bindings"][0]["official_artifacts"])
    return (
        packet, {name: (D04 / name).read_bytes() for name in names},
        SliceApproval(cfg["packet_sha256"], cfg["approved_by"], datetime.fromisoformat(cfg["approved_at"])),
        (D05 / "chinabond_3m_20220906_07.html").read_bytes(),
    )


def evaluate(request=REQUEST, policy=POLICY):
    packet, evidence, approval, rate = source_inputs()
    return evaluate_archived_valuation(request, packet, evidence, approval, rate, policy, ADMITTED)


def reference_lattice(f, t, sigma, cp, steps):
    """Independent vectorized CRR futures recursion; retain early node values."""
    h = t / steps
    u = exp(sigma * sqrt(h))
    d = 1 / u
    p = (1 - d) / (u - d)
    discount = exp(-RATE * h)
    terminal = f * np.exp((steps - 2 * np.arange(steps + 1)) * sigma * sqrt(h))
    values = np.maximum(cp * (terminal - K), 0)
    nodes = {}
    for level in range(steps - 1, -1, -1):
        under = f * np.exp((level - 2 * np.arange(level + 1)) * sigma * sqrt(h))
        values = np.maximum(discount * (p * values[:-1] + (1 - p) * values[1:]), np.maximum(cp * (under - K), 0))
        if level <= 2:
            nodes[level] = (under, values)
    return float(values[0]), nodes


def reference_iv(mark, cp, n):
    return brentq(lambda sigma: reference_lattice(F, T, sigma, cp, n)[0] - mark, 0.01, 1.0)


def test_real_sample_model_iv_and_grid_independent_reference():
    result = evaluate()
    assert result.status is ResearchValuationStatus.OK, result.reason_code
    assert result.valuation is not None
    actual = result.valuation
    assert actual.historical_slice.historical_input_admitted
    assert not actual.production_valuation_ready
    assert actual.rate.quoted_percent == 1.4442
    assert actual.rate.continuous_rate == pytest.approx(RATE, abs=1e-14)
    assert actual.reference_at == CLOCK
    assert actual.nominal_expiry_at == datetime(2022, 10, 25, 15, tzinfo=ZONE)
    assert actual.time_to_expiry == pytest.approx(T, abs=1e-14)
    assert actual.model is ResearchPricingModel.BINOMIAL_TREE
    assert actual.grid.baseline_value == pytest.approx(24400, abs=0.001)
    assert actual.grid.configured_value_change == pytest.approx(0, abs=1e-9)
    sigmas = []
    for leaf, cp, mark in zip(actual.calibrations, (1, -1), (2230, 2650), strict=True):
        expected_iv = reference_iv(mark, cp, 128)
        sigmas.append(expected_iv)
        assert leaf.implied_volatility == pytest.approx(expected_iv, abs=1e-8)
        assert leaf.iv_result is not None and leaf.iv_result.valid
        assert leaf.iv_result.residual < 0.001
        price, nodes = reference_lattice(F, T, expected_iv, cp, 128)
        assert leaf.baseline_value == pytest.approx(price * MULTIPLIER, abs=1e-5)
        x1, y1 = nodes[1]
        x2, y2 = nodes[2]
        delta = (y1[0] - y1[1]) / (x1[0] - x1[1])
        gamma = ((y2[0] - y2[1]) / (x2[0] - x2[1]) - (y2[1] - y2[2]) / (x2[1] - x2[2])) / ((x2[0] - x2[2]) / 2)
        theta = (y2[1] - price) / (2 * T / 128)
        vega = (reference_lattice(F, T, expected_iv * 1.001, cp, 128)[0] - price) / (expected_iv * 0.001)
        greeks = leaf.baseline_greeks
        assert greeks is not None
        assert (greeks.delta, greeks.gamma, greeks.theta, greeks.vega) == pytest.approx(
            tuple(float(v) * MULTIPLIER for v in (delta, gamma, theta, vega)), rel=1e-7, abs=1e-7,
        )
        assert abs(expected_iv - reference_iv(mark, cp, 512)) < 0.001
    for row, iv_shock in zip(actual.grid.heatmap_rows, REQUEST.heatmap_iv_shocks, strict=True):
        for cell, x in zip(row, REQUEST.heatmap_price_shocks, strict=True):
            expected = sum(reference_lattice(F * (1 + x), T, iv + iv_shock, cp, 128)[0] * MULTIPLIER
                           for iv, cp in zip(sigmas, (1, -1), strict=True))
            assert cell.status is ResearchValuationStatus.OK
            assert cell.baseline_value == actual.grid.baseline_value
            assert cell.scenario_value == pytest.approx(expected, abs=1e-4)
            assert cell.value_change == pytest.approx(expected - 24400, abs=1e-4)


def test_signed_quantity_elapsed_time_and_unavailable_cells():
    result = evaluate(replace(
        REQUEST, quantities=(2, -1), scenario_at=CLOCK + timedelta(days=1),
        curve_price_shocks=(0.0,), heatmap_price_shocks=(-0.05, 0.0),
        heatmap_iv_shocks=(-0.5, 0.0),
    ), replace(POLICY, tree_steps=64))
    assert result.status is ResearchValuationStatus.OK
    actual = result.valuation
    assert actual is not None
    assert actual.grid.baseline_value == pytest.approx(9050, abs=0.001)
    assert actual.calibrations[1].baseline_greeks.delta > 0
    assert actual.calibrations[1].baseline_greeks.vega < 0
    assert actual.grid.configured_contributions[0].scenario_time_to_expiry == pytest.approx(47 / 365)
    assert all(x.status is ResearchValuationStatus.UNAVAILABLE and x.value_change is None for x in actual.grid.heatmap_rows[0])
    origin = actual.grid.heatmap_rows[1][1]
    assert origin.value_change == actual.grid.configured_value_change
    assert origin.value_change != 0
    for item in actual.grid.configured_contributions:
        assert item.baseline_iv == item.scenario_iv


def test_raw_archive_is_rechecked_not_a_caller_status_flag():
    packet, evidence, approval, rate = source_inputs()
    evidence["option_contract_base.json"] += b" "
    result = evaluate_archived_valuation(REQUEST, packet, evidence, approval, rate, POLICY, ADMITTED)
    assert result.status is ResearchValuationStatus.UNAVAILABLE
    assert result.reason_code == "EVIDENCE_DIGEST_MISMATCH"
    assert result.valuation is None


def test_rate_artifact_is_independently_pinned():
    packet, evidence, approval, rate = source_inputs()
    result = evaluate_archived_valuation(REQUEST, packet, evidence, approval, rate + b" ", POLICY, ADMITTED)
    assert result.reason_code == "RATE_EVIDENCE_DIGEST_MISMATCH"
    assert result.valuation is None


@pytest.mark.parametrize("policy,reason", [
    (replace(POLICY, rate_date=DAY), "RATE_DATE_UNAVAILABLE"),
    (replace(POLICY, rate_date=date(2022, 9, 5)), "RATE_DATE_UNAVAILABLE"),
    (replace(POLICY, rate_policy="DEFAULT_ZERO"), "UNSUPPORTED_VALUATION_POLICY"),
    (replace(POLICY, rate_source_uri="https://example.invalid"), "UNSUPPORTED_VALUATION_POLICY"),
    (replace(POLICY, clock_policy="EXERCISE_CUTOFF"), "UNSUPPORTED_VALUATION_POLICY"),
    (replace(POLICY, day_count_basis="ACT/360"), "UNSUPPORTED_VALUATION_POLICY"),
    (replace(POLICY, tree_steps=513), "INVALID_INPUT"),
    (None, "INVALID_INPUT"),
])
def test_no_implicit_rate_time_or_model_policy(policy, reason):
    result = evaluate(policy=policy)
    assert result.status is ResearchValuationStatus.UNAVAILABLE
    assert result.reason_code == reason and result.valuation is None


@pytest.mark.parametrize("req,reason", [
    (replace(REQUEST, quantities=(1, 0)), "INVALID_INPUT"),
    (replace(REQUEST, quantities=(True, 1)), "INVALID_INPUT"),
    (replace(REQUEST, quantities=(1,)), "INVALID_INPUT"),
    (replace(REQUEST, scenario_at=CLOCK.replace(tzinfo=None)), "INVALID_SCENARIO_TIME"),
    (replace(REQUEST, scenario_at=CLOCK - timedelta(days=1)), "INVALID_SCENARIO_TIME"),
    (replace(REQUEST, scenario_at=datetime(2022, 10, 25, 9, tzinfo=ZONE)), "EXPIRED_SCENARIO"),
    (replace(REQUEST, heatmap_iv_shocks=tuple(range(6))), "GRID_LIMIT_EXCEEDED"),
    (replace(REQUEST, heatmap_price_shocks=(0.01,)), "INVALID_AXES"),
])
def test_request_gate_fails_closed(req, reason):
    result = evaluate(req)
    assert result.status is ResearchValuationStatus.UNAVAILABLE
    assert result.reason_code == reason and result.valuation is None
