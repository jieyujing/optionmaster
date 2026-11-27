"""D09 actual operator-approved archive, public v3 HTTP seam."""
import asyncio
from pathlib import Path
import pytest

pytest.importorskip("fastapi")
httpx = pytest.importorskip("httpx")
pytest.importorskip("pydantic", minversion="2.10")

from vnpy_optionmaster.research_catalog_api import create_catalog_research_app, load_reviewed_catalog  # noqa: E402

ROOT = Path(__file__).resolve().parents[1] / "docs/evidence"
TOKEN = "fixture-only-d09-api-token-32-characters"
HEADERS = {"Authorization": "Bearer " + TOKEN}
PIN = "2c57d7df89e87db37749d4217c68117bd1d3ccc4c332bf1cfa41b4fb28264fea"
COVERAGE = "/api/v3/research/coverage"
VALUE = "/api/v3/research/valuations"
BODY = {
    "schema_version": "3.0", "catalog_id": "shfe-cu-dated-catalog-v3",
    "trade_date": "2022-09-07",
    "positions": {"CU2212C61000.XSGE": 1, "CU2211C61000.XSGE": -1},
    "scenario_at": "2022-09-07T15:00:00+08:00",
    "curve_price_shocks": [-0.05, 0, 0.05],
    "heatmap_price_shocks": [-0.05, 0, 0.05],
    "heatmap_iv_shocks": [-0.03, 0, 0.03],
    "price_shocks_by_underlying": {}, "iv_shocks_by_expiry": {},
}


@pytest.fixture(scope="module")
def source():
    return load_reviewed_catalog(ROOT)


@pytest.fixture
def app(source):
    return create_catalog_research_app(source, bearer_token=TOKEN)


def send(app, method, path, **kwargs):
    async def request():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://testserver") as c:
            return await c.request(method, path, **kwargs)
    return asyncio.run(request())


def ok(response):
    assert response.status_code == 200, response.text[:300]
    assert response.headers["cache-control"] == "no-store"
    doc = response.json()
    assert doc["schema_version"] == "3.0" and doc["status"] == "OK", doc
    return doc


def test_approved_date_coverage_and_exact_calendar_spread(app):
    c = ok(send(app, "GET", COVERAGE, headers=HEADERS))["coverage"]
    assert c["admitted_legs"] == len(c["legs"]) == 132
    assert c["approved_contract_dates"] == 198
    assert c["source_versions"]["packet_sha256"] == PIN
    assert c["production_valuation_ready"] is False
    assert c["available_dates"] == ["2022-09-07", "2022-09-08"]
    assert {m["underlying_id"] for m in c["months"] if m["admitted_contracts"]} == {"SHFE.cu2211", "SHFE.cu2212"}
    v = ok(send(app, "POST", VALUE, headers=HEADERS, json=BODY))["valuation"]
    assert v["grid"]["baseline_value"] == pytest.approx(1850, abs=0.001)
    assert [x["market_underlying_price"] for x in v["legs"]] == [60580, 60240]
    assert [x["baseline_time_to_expiry"] * 365 for x in v["legs"]] == pytest.approx([48, 78])
    assert [x["underlying_id"] for x in v["underlying_exposures"]] == ["SHFE.cu2211", "SHFE.cu2212"]
    assert v["source_versions"]["packet_sha256"] == PIN
    assert v["production_valuation_ready"] is False

@pytest.mark.parametrize("day,count,months", [
    ("2022-09-07", 132, {"SHFE.cu2211", "SHFE.cu2212"}),
    ("2022-09-08", 66, {"SHFE.cu2211"}),
])
def test_date_scope_and_official_counts(app, day, count, months):
    c = ok(send(app, "GET", COVERAGE + "?trade_date=" + day, headers=HEADERS))["coverage"]
    assert c["trade_date"] == day and len(c["legs"]) == count
    assert sum(m["discovered_contracts"] for m in c["months"]) == 340
    assert {m["underlying_id"] for m in c["months"] if m["admitted_contracts"]} == months
    assert all(m["child_packet_sha256"] is None and m["observed_at"] is None
               for m in c["months"] if not m["admitted_contracts"])


def test_next_day_new_marks_and_rate(app):
    b = {**BODY, "trade_date": "2022-09-08", "scenario_at": "2022-09-08T15:00:00+08:00",
         "positions": {"CU2211C61000.XSGE": 1, "CU2211P61000.XSGE": 1}}
    v = ok(send(app, "POST", VALUE, headers=HEADERS, json=b))["valuation"]
    assert v["grid"]["baseline_value"] == pytest.approx(23280, abs=0.001)
    assert v["rate"]["quote_date"] == "2022-09-07" and v["rate"]["quoted_percent"] == 1.4628
    assert all(x["baseline_time_to_expiry"] * 365 == pytest.approx(47) for x in v["legs"])


@pytest.mark.parametrize("n,expected", [(4, 5390), (8, 3700)])
def test_actual_cross_month_2_to_8_leg_contract(app, n, expected):
    symbols = ("CU2211C61000.XSGE", "CU2212C61000.XSGE", "CU2211P61000.XSGE", "CU2212P61000.XSGE",
               "CU2211C60000.XSGE", "CU2212C60000.XSGE", "CU2211P60000.XSGE", "CU2212P60000.XSGE")[:n]
    quantities = (-1, 1, -1, 1, -1, 1, 1, -1)[:n]
    b = {**BODY, "positions": dict(zip(symbols, quantities, strict=True)),
         "curve_price_shocks": [0], "heatmap_price_shocks": [0], "heatmap_iv_shocks": [0]}
    v = ok(send(app, "POST", VALUE, headers=HEADERS, json=b))["valuation"]
    assert v["grid"]["baseline_value"] == pytest.approx(expected, abs=0.001)
    assert len(v["legs"]) == n and len(v["underlying_exposures"]) == 2
    assert len(v["grid"]["heatmap_rows"][0][0]["contributions"]) == n


def test_independent_shocks_preserve_per_expiry_identity(app):
    b = {**BODY, "price_shocks_by_underlying": {"SHFE.cu2211": 0.02},
         "iv_shocks_by_expiry": {"SHFE|cu2212|2022-11-24": 0.03}}
    v = ok(send(app, "POST", VALUE, headers=HEADERS, json=b))["valuation"]
    near, far = v["legs"]
    assert near["scenario_iv"] == near["implied_volatility"]
    assert far["scenario_iv"] == pytest.approx(far["implied_volatility"] + 0.03)
    assert v["grid"]["configured_value_change"] != 0
    assert v["grid"]["heatmap_rows"][1][1]["value_change"] == v["grid"]["configured_value_change"]


def denied(response, status, code):
    assert response.status_code == status, response.text[:200]
    doc = response.json()
    assert doc["status"] == "UNAVAILABLE" and doc["reason_code"] == code
    assert doc["valuation"] is None and doc["coverage"] is None
    assert TOKEN not in response.text


@pytest.mark.parametrize("query,code", [
    ("?trade_date=2022-09-09", "SLICE_SCOPE_MISMATCH"),
    ("?trade_date=2022-09-07&trade_date=2022-09-08", "INVALID_REQUEST"),
    ("?trade_date=20220907", "INVALID_REQUEST"), ("?unknown=1", "INVALID_REQUEST"),
])
def test_bad_date_queries(app, query, code):
    denied(send(app, "GET", COVERAGE + query, headers=HEADERS), 422, code)


@pytest.mark.parametrize("changes,status,reason", [
    ({"trade_date": "2022-09-08", "scenario_at": "2022-09-08T15:00:00+08:00"}, 422, "SLICE_SCOPE_MISMATCH"),
    ({"scenario_at": "2022-10-25T00:00:00+08:00"}, 200, "EXPIRED_SCENARIO"),
    ({"scenario_at": "2022-09-07T14:59:00+08:00"}, 200, "INVALID_SCENARIO_TIME"),
    ({"positions": {"CU2211C61000.XSGE": 1, "CU2212C61000.XSGE": 0}}, 422, "INVALID_REQUEST"),
    ({"positions": {"CU2211C61000.XSGE": 1, "CU2211P79000.XSGE": 1}}, 200, "IV_UNAVAILABLE"),
    ({"iv_shocks_by_expiry": {"SHFE|cu2210|2022-09-26": 0.02}}, 200, "INVALID_SCENARIO"),
    ({"curve_price_shocks": [0] * 12}, 422, "INVALID_REQUEST"),
])
def test_fail_closed_api(app, changes, status, reason):
    denied(send(app, "POST", VALUE, headers=HEADERS, json={**BODY, **changes}), status, reason)


def test_auth_body_size_and_duplicate_keys(app):
    denied(send(app, "GET", COVERAGE), 401, "UNAUTHORIZED")
    denied(send(app, "POST", VALUE, headers={**HEADERS, "Content-Type": "application/json"},
                content=b" " * 8193), 413, "PAYLOAD_TOO_LARGE")
    denied(send(app, "POST", VALUE, headers={**HEADERS, "Content-Type": "application/json"},
                content=b'{"positions":{},"positions":{}}'), 422, "INVALID_JSON")


def test_shared_lock_busy(source):
    from threading import Lock
    lock = Lock()
    app = create_catalog_research_app(source, bearer_token=TOKEN, worker_lock=lock)
    with lock:
        denied(send(app, "GET", COVERAGE, headers=HEADERS), 429, "BUSY")


def test_actual_v3_schema_pin(app):
    import json
    from hashlib import sha256
    raw = (json.dumps(app.openapi(), sort_keys=True, indent=2, ensure_ascii=True, allow_nan=False) + "\n").encode()
    archived = (ROOT / "m1-d09/openapi_v3.json").read_bytes()
    assert raw == archived
    assert sha256(raw).hexdigest() == "a3cb4e79acc127a13d69565494259c232d65835a50edaf2c6f6e50268ca5b7f3"
