"""D08 real operator-approved chain and v2 HTTP boundary; no listener/DB."""
import asyncio
from dataclasses import replace
from pathlib import Path
from threading import Lock
import json

import pytest

pytest.importorskip("fastapi")
httpx = pytest.importorskip("httpx")
pytest.importorskip("pydantic", minversion="2.10")

from vnpy_optionmaster.research_api import create_research_app, load_reviewed_archive  # noqa: E402
from vnpy_optionmaster.research_chain_api import create_chain_research_app, load_reviewed_chain  # noqa: E402
from vnpy_optionmaster.research_chain_api_dto import ChainApiEnvelope  # noqa: E402

ROOT = Path(__file__).resolve().parents[1] / "docs/evidence"
D04 = ROOT / "m1-d04/shfe-20220907"
D05 = ROOT / "m1-d05/cu2211-20220907"
D08 = ROOT / "m1-d08/cu2211-20220907"
TOKEN = "fixture-only-d08-browser-and-api-token"
HEADERS = {"Authorization": "Bearer " + TOKEN}
COVERAGE = "/api/v2/research/coverage"
URL = "/api/v2/research/valuations"
PIN = "9216e5598c11ef2736c353dddd08bd7c2efedf742935a9a7ee9afd8383ee8846"
BODY = {
    "schema_version": "2.0", "slice_id": "shfe-cu2211-2022-09-07-chain-v2",
    "positions": {"CU2211P62000.XSGE": 1, "CU2211C62000.XSGE": -1,
                  "CU2211P60000.XSGE": -1, "CU2211C60000.XSGE": 1},
    "scenario_at": "2022-09-07T15:00:00+08:00",
    "curve_price_shocks": [-0.05, 0, 0.05],
    "heatmap_price_shocks": [-0.05, 0, 0.05],
    "heatmap_iv_shocks": [-0.03, 0, 0.03],
}


@pytest.fixture(scope="module")
def source():
    return load_reviewed_chain(D04, D05, D08)


@pytest.fixture
def app(source):
    return create_chain_research_app(source, bearer_token=TOKEN)


def send(app, method, path, **kwargs):
    async def request():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://testserver") as c:
            return await c.request(method, path, **kwargs)
    return asyncio.run(request())


def success(response):
    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-store"
    envelope = ChainApiEnvelope.model_validate_json(response.content)
    assert envelope.status == "OK", response.json()
    return response.json()


def rejected(response, status, reason=None):
    assert response.status_code == status
    doc = response.json()
    assert doc["schema_version"] == "2.0" and doc["status"] == "UNAVAILABLE"
    assert doc["coverage"] is None and doc["valuation"] is None
    if reason is not None:
        assert doc["reason_code"] == reason
    assert ChainApiEnvelope.model_validate_json(response.content).status == "UNAVAILABLE"
    assert TOKEN not in response.text


def test_actual_approved_coverage_66_distinct_contracts(app):
    c = success(send(app, "GET", COVERAGE, headers=HEADERS))["coverage"]
    assert c["admitted_legs"] == c["requested_legs"] == len(c["legs"]) == 66
    assert c["production_valuation_ready"] is False
    assert c["source_versions"]["packet_sha256"] == PIN
    assert c["trade_date"] == "2022-09-07"
    assert c["approved_at"].startswith("2026-11-24T08:25:56")
    assert c["price_basis"] == "SETTLEMENT"
    assert len({x["source_symbol"] for x in c["legs"]}) == 66
    assert len({x["strike"] for x in c["legs"]}) == 33
    assert sum(m["discovered_contracts"] for m in c["months"]) == 340
    assert sum(m["admitted_contracts"] for m in c["months"]) == 66
    assert {m["underlying_id"] for m in c["months"] if m["admitted_contracts"]} == {"SHFE.cu2211"}
    assert {x["source_symbol"] for x in c["legs"] if x["at_intrinsic"]} == {"CU2211P79000.XSGE", "CU2211P80000.XSGE"}
    assert all(x["underlying_settlement"] == 60580 for x in c["legs"])


def test_real_four_leg_valuation_and_exact_signed_marks(app):
    v = success(send(app, "POST", URL, headers=HEADERS, json=BODY))["valuation"]
    assert [leg["source_symbol"] for leg in v["legs"]] == sorted(BODY["positions"])
    # Actual official exchange settlement 2714,1810,2136,3228; 5 tonnes.
    assert v["grid"]["baseline_value"] == pytest.approx(9980, abs=0.001)
    assert len(v["legs"]) == 4 and len(v["underlying_exposures"]) == 1
    assert all(x["raw_contract_version"] == "" for x in v["legs"])
    assert all(len(p["contributions"]) == 4 for row in v["grid"]["heatmap_rows"] for p in row)
    assert v["grid"]["configured_value_change"] == pytest.approx(0)
    assert v["source_versions"]["packet_sha256"] == PIN
    assert v["production_valuation_ready"] is False


def test_actual_v1_pair_parity_and_v1_openapi_unchanged(app):
    original = create_research_app(load_reviewed_archive(D04, D05), bearer_token=TOKEN)
    original_body = {**BODY, "schema_version": "1.0", "slice_id": "shfe-cu2211-2022-09-07-v1",
                     "positions": {"CU2211C61000.XSGE": 1, "CU2211P61000.XSGE": 1}}
    v1 = send(original, "POST", "/api/v1/research/valuations", headers=HEADERS, json=original_body).json()
    v2 = success(send(app, "POST", URL, headers=HEADERS, json={
        **original_body, "schema_version": "2.0", "slice_id": BODY["slice_id"]}))["valuation"]
    assert v1["status"] == "OK"
    assert v1["valuation"]["grid"] == v2["grid"]
    assert [x["implied_volatility"] for x in v1["valuation"]["legs"]] == [x["implied_volatility"] for x in v2["legs"]]
    pinned = json.loads((ROOT / "m1-d06/openapi_v1.json").read_bytes())
    assert original.openapi() == pinned


@pytest.mark.parametrize("change", [
    {"positions": {"CU2211C60000.XSGE": 1}},
    {"positions": {f"x{i}": 1 for i in range(9)}},
    {"positions": {"CU2211C60000.XSGE": 0, "CU2211C62000.XSGE": 1}},
    {"positions": {"CU2211C60000.XSGE": True, "CU2211C62000.XSGE": 1}},
    {"positions": {"CU2211C60000.XSGE": 1.0, "CU2211C62000.XSGE": 1}},
    {"schema_version": "1.0"}, {"slice_id": "unknown"}, {"rate": 0},
    {"scenario_at": "2022-09-07T15:00:00Z"}, {"heatmap_iv_shocks": [0, 0.0]},
])
def test_strict_v2_request(app, change):
    rejected(send(app, "POST", URL, headers=HEADERS, json={**BODY, **change}), 422, "INVALID_REQUEST")


def test_other_month_and_unknown_keys_not_approved(app):
    rejected(send(app, "POST", URL, headers=HEADERS, json={
        **BODY, "positions": {"CU2211C60000.XSGE": 1, "CU2212C60000.XSGE": 1},
    }), 422, "SLICE_SCOPE_MISMATCH")


def test_auth_duplicates_body_limit_and_query(app):
    rejected(send(app, "GET", COVERAGE), 401, "UNAUTHORIZED")
    rejected(send(app, "GET", COVERAGE, headers=[("Authorization", "Bearer "+TOKEN)]*2), 401, "UNAUTHORIZED")
    rejected(send(app, "GET", COVERAGE+"?date=2022-09-08", headers=HEADERS), 422, "INVALID_REQUEST")
    rejected(send(app, "POST", URL, headers={**HEADERS, "Content-Type": "application/json"},
                  content=b'{"positions":{},"positions":{}}'), 422, "INVALID_JSON")
    rejected(send(app, "POST", URL, headers={**HEADERS, "Content-Type": "application/json"}, content=b"x"*8193),
             413, "PAYLOAD_TOO_LARGE")


def test_busy_lock_shared_with_v1(source):
    lock = Lock()
    app = create_chain_research_app(source, bearer_token=TOKEN, worker_lock=lock)
    legacy = create_research_app(load_reviewed_archive(D04, D05), bearer_token=TOKEN, worker_lock=lock)
    with lock:
        rejected(send(app, "GET", COVERAGE, headers=HEADERS), 429, "BUSY")
        v1 = send(legacy, "GET", "/api/v1/research/coverage", headers=HEADERS)
        assert v1.status_code == 429 and v1.json()["reason_code"] == "BUSY"
    assert success(send(app, "GET", COVERAGE, headers=HEADERS))["coverage"]["admitted_legs"] == 66


def test_actual_intrinsic_mark_never_fabricates_partial_valuation(app):
    response = send(app, "POST", URL, headers=HEADERS, json={
        **BODY, "positions": {"CU2211P79000.XSGE": 1, "CU2211P80000.XSGE": 1},
    })
    rejected(response, 200, "IV_UNAVAILABLE")


def test_missing_or_changed_approval_fails_closed(source):
    with pytest.raises(ValueError):
        create_chain_research_app(replace(source, approval=replace(source.approval, packet_sha256="0"*64)), bearer_token=TOKEN)


def test_tampered_source_no_coverage_or_valuation(source):
    tampered = dict(source.evidence)
    tampered["option_daily.json"] += b" "
    app = create_chain_research_app(replace(source, evidence=tampered), bearer_token=TOKEN)
    rejected(send(app, "GET", COVERAGE, headers=HEADERS), 200, "ARCHIVE_UNAVAILABLE")
    rejected(send(app, "POST", URL, headers=HEADERS, json=BODY), 200, "ARCHIVE_UNAVAILABLE")
