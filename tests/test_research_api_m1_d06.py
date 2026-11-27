"""D06 optional Web tests exercise ASGI in process, never a network listener."""
import asyncio
from dataclasses import replace
from datetime import datetime, timezone
import json
from pathlib import Path
from threading import Event

import pytest

pytest.importorskip("fastapi")
httpx = pytest.importorskip("httpx")
pytest.importorskip("pydantic", minversion="2.10")

from vnpy_optionmaster.research_api import create_research_app, load_reviewed_archive  # noqa: E402
from vnpy_optionmaster.research_api_dto import ApiEnvelope  # noqa: E402

ROOT = Path(__file__).resolve().parents[1] / "docs/evidence"
TOKEN = "fixture-only-api-token-not-a-real-secret"
HEADERS = {"Authorization": f"Bearer {TOKEN}"}
SLICE = "shfe-cu2211-2022-09-07-v1"
URL = "/api/v1/research/valuations"
COVERAGE = "/api/v1/research/coverage"
BODY = {
    "schema_version": "1.0", "slice_id": SLICE,
    "positions": {"CU2211C61000.XSGE": 1, "CU2211P61000.XSGE": 1},
    "scenario_at": "2022-09-07T15:00:00+08:00",
    "curve_price_shocks": [-0.05, 0, 0.05],
    "heatmap_price_shocks": [-0.05, 0, 0.05],
    "heatmap_iv_shocks": [-0.03, 0, 0.03],
}


@pytest.fixture(scope="module")
def archive():
    return load_reviewed_archive(ROOT / "m1-d04/shfe-20220907", ROOT / "m1-d05/cu2211-20220907")


@pytest.fixture
def app(archive):
    return create_research_app(archive, bearer_token=TOKEN)


def send(app, method, path, **kwargs):
    async def execute():
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://testserver",
        ) as client:
            return await client.request(method, path, **kwargs)
    return asyncio.run(execute())


def test_real_archived_api_contract(app):
    coverage = send(app, "GET", COVERAGE, headers=HEADERS)
    assert coverage.status_code == 200
    c = coverage.json()
    assert c["schema_version"] == "1.0" and c["status"] == "OK"
    assert c["valuation"] is None
    assert c["coverage"]["slice_id"] == SLICE
    assert c["coverage"]["admitted_legs"] == c["coverage"]["requested_legs"] == 2
    assert c["coverage"]["production_valuation_ready"] is False

    response = send(app, "POST", URL, headers=HEADERS, json=BODY)
    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-store"
    doc = response.json()
    assert doc["schema_version"] == "1.0" and doc["status"] == "OK"
    assert doc["reason_code"] is None and doc["coverage"] is None
    assert len(doc["calculation_id"]) == 32
    v = doc["valuation"]
    assert v["slice_id"] == SLICE and v["scope"] == "ARCHIVED_HISTORICAL_RESEARCH_ONLY"
    assert v["production_valuation_ready"] is False
    assert v["trade_date"] == "2022-09-07" and v["price_basis"] == "SETTLEMENT"
    assert v["source_versions"]["packet_sha256"] == "85a27c0d97b836cf51d2f8db7ac92c1e9681138ffe477d0f324f4518276ad826"
    assert len(v["source_versions"]["artifacts"]) == 8
    assert v["rate"]["quoted_percent"] == 1.4442 and v["tree_steps"] == 128
    assert v["grid"]["baseline_value"] == pytest.approx(24400, abs=0.001)
    assert v["grid"]["heatmap_rows"][1][1]["value_change"] == pytest.approx(0)
    assert [x["implied_volatility"] for x in v["legs"]] == pytest.approx([0.2768374853587945, 0.27690828731287165])
    assert all(x["raw_contract_version"] == "" and x["raw_exercise_style"] == "UNKNOWN"
               and x["exercise_style"] == "AMERICAN" for x in v["legs"])
    assert v["grid"]["heatmap_rows"][0][0]["value_change"] == pytest.approx(1062.58501605, abs=0.001)
    assert v["underlying_exposures"][0]["underlying_id"] == "SHFE.cu2211"
    assert v["underlying_exposures"][0]["baseline_greeks"]["delta"] == pytest.approx(-0.07360362774257645)
    assert v["units"]["vega"] == "CNY_PER_1_ABSOLUTE_SIGMA"
    assert ApiEnvelope.model_validate_json(response.content).status == "OK"


def rejected(response, status, reason):
    assert response.status_code == status
    body = response.json()
    assert body["schema_version"] == "1.0" and body["status"] == "UNAVAILABLE"
    assert body["reason_code"] == reason
    assert body["valuation"] is None and body["coverage"] is None
    assert response.headers["cache-control"] == "no-store"
    assert ApiEnvelope.model_validate_json(response.content).status == "UNAVAILABLE"


@pytest.mark.parametrize("headers", [
    {},
    {"Authorization": "Bearer wrong"},
    {"Authorization": f"Basic {TOKEN}"},
    [("Authorization", f"Bearer {TOKEN}"), ("Authorization", f"Bearer {TOKEN}")],
])
def test_authentication_fail_closed(app, headers):
    for method, path, kwargs in (("GET", COVERAGE, {}), ("POST", URL, {"json": BODY})):
        response = send(app, method, path, headers=headers, **kwargs)
        rejected(response, 401, "UNAUTHORIZED")
        assert response.headers["www-authenticate"] == "Bearer"
        assert TOKEN not in response.text


@pytest.mark.parametrize("token", ["", "short", " " * 32, "x" * 257, "非" * 32, "a" * 31 + "\n"])
def test_factory_requires_explicit_safe_token(archive, token):
    with pytest.raises(ValueError):
        create_research_app(archive, bearer_token=token)


@pytest.mark.parametrize("change", [
    {"schema_version": "2.0"}, {"slice_id": "unknown"},
    {"positions": {"CU2211C61000.XSGE": True, "CU2211P61000.XSGE": 1}},
    {"positions": {"CU2211C61000.XSGE": 1.0, "CU2211P61000.XSGE": 1}},
    {"positions": {"CU2211C61000.XSGE": "1", "CU2211P61000.XSGE": 1}},
    {"positions": {"CU2211C61000.XSGE": 0, "CU2211P61000.XSGE": 1}},
    {"positions": {"CU2211C61000.XSGE": 1001, "CU2211P61000.XSGE": 1}},
    {"positions": {"CU2211C61000.XSGE": 1}},
    {"scenario_at": "2022-09-07T15:00:00"}, {"scenario_at": "2022-09-07T15:00:00Z"},
    {"scenario_at": "2022-02-30T15:00:00+08:00"},
    {"curve_price_shocks": [0, 0.0]}, {"curve_price_shocks": [0.1]},
    {"curve_price_shocks": [False, 0.1]}, {"curve_price_shocks": ["0", 0.1]},
    {"heatmap_iv_shocks": [0, 2]}, {"heatmap_iv_shocks": [0, 0.1, 0.2, 0.3, 0.4, 0.5]},
    {"database_url": "http://invalid.example"}, {"rate_policy": "DEFAULT_ZERO"},
    {"tree_steps": 2}, {"evidence_path": "/etc/passwd"},
])
def test_strict_request_schema(app, change):
    rejected(send(app, "POST", URL, headers=HEADERS, json={**BODY, **change}), 422, "INVALID_REQUEST")


def test_extreme_datetime_cannot_escape_structured_error_boundary(app):
    response = send(app, "POST", URL, headers=HEADERS, json={
        **BODY, "scenario_at": "0001-01-01T00:00:00+08:00",
    })
    rejected(response, 422, "INVALID_REQUEST")


def test_exact_source_key_scope_and_no_query_override(app):
    rejected(send(app, "POST", URL, headers=HEADERS, json={
        **BODY, "positions": {"CU2211C61000.XSGE": 1, "cu2211P61000.XSGE": 1},
    }), 422, "SLICE_SCOPE_MISMATCH")
    rejected(send(app, "POST", URL + "?rate=0", headers=HEADERS, json=BODY), 422, "INVALID_REQUEST")


@pytest.mark.parametrize("payload", [
    b'{"schema_version":"1.0","schema_version":"1.0"}',
    b'{"positions":{"a":1,"a":2}}',
    b'{"x":NaN}', b'{"x":Infinity}', b'{"x":-Infinity}',
    b'{"a":', b'[]', b'null', b'\xff',
])
def test_strict_json_parse(app, payload):
    rejected(send(app, "POST", URL, headers={**HEADERS, "Content-Type": "application/json"}, content=payload),
             422, "INVALID_JSON")


def test_json_float_overflow_rejected(app):
    payload = json.dumps(BODY).replace("[-0.05, 0, 0.05]", "[0, 1e999]")
    rejected(send(app, "POST", URL, headers={**HEADERS, "Content-Type": "application/json"}, content=payload),
             422, "INVALID_REQUEST")


def test_body_media_and_stream_bounds(app):
    rejected(send(app, "POST", URL, headers=HEADERS, content=b"{}"), 415, "UNSUPPORTED_MEDIA_TYPE")
    rejected(send(app, "POST", URL, headers={**HEADERS, "Content-Type": "application/json", "Content-Encoding": "gzip"},
                  content=b"{}"), 415, "UNSUPPORTED_MEDIA_TYPE")
    rejected(send(app, "POST", URL, headers={**HEADERS, "Content-Type": "application/json"}, content=b" " * 8193),
             413, "PAYLOAD_TOO_LARGE")

    async def chunks():
        yield b" " * 8192
        yield b"x"

    rejected(send(app, "POST", URL, headers={**HEADERS, "Content-Type": "application/json"}, content=chunks()),
             413, "PAYLOAD_TOO_LARGE")


def test_signed_time_scenario_and_invalid_grid_cells(app):
    request = {
        **BODY, "positions": {"CU2211P61000.XSGE": -1, "CU2211C61000.XSGE": 2},
        "scenario_at": "2022-09-08T15:00:00+08:00", "curve_price_shocks": [0],
        "heatmap_price_shocks": [0, -1], "heatmap_iv_shocks": [-0.5, 0],
    }
    response = send(app, "POST", URL, headers=HEADERS, json=request)
    assert response.status_code == 200
    v = response.json()["valuation"]
    assert v is not None and v["grid"]["baseline_value"] == pytest.approx(9050, abs=0.001)
    assert [x["quantity"] for x in v["legs"]] == [2, -1]
    assert v["legs"][1]["baseline_greeks"]["vega_per_unit"] < 0
    assert v["legs"][0]["scenario_time_to_expiry"] == pytest.approx(47 / 365)
    rows = v["grid"]["heatmap_rows"]
    for point in (rows[0][0], rows[0][1], rows[1][1]):
        assert point["status"] == "UNAVAILABLE" and point["reason_code"] is not None
        assert point["baseline_value"] is None and point["scenario_value"] is None
        assert point["value_change"] is None and point["contributions"] == []
    origin = rows[1][0]
    assert origin["status"] == "OK"
    assert origin["value_change"] == v["grid"]["configured_value_change"] != 0
    assert len(origin["contributions"]) == 2
    for item in v["legs"]:
        assert item["scenario_iv"] == item["implied_volatility"]


@pytest.mark.parametrize("scenario,reason", [
    ("2022-09-06T15:00:00+08:00", "INVALID_SCENARIO_TIME"),
    ("2022-10-25T09:00:00+08:00", "EXPIRED_SCENARIO"),
])
def test_domain_failure_has_no_partial_valuation(app, scenario, reason):
    rejected(send(app, "POST", URL, headers=HEADERS, json={**BODY, "scenario_at": scenario}), 200, reason)


def test_archive_corruption_rejected_at_both_boundaries(archive):
    bad = dict(archive.evidence)
    bad["option_contract_base.json"] += b" "
    app = create_research_app(replace(archive, evidence=bad), bearer_token=TOKEN)
    rejected(send(app, "GET", COVERAGE, headers=HEADERS), 200, "ARCHIVE_UNAVAILABLE")
    rejected(send(app, "POST", URL, headers=HEADERS, json=BODY), 200, "ARCHIVE_UNAVAILABLE")


def test_rate_tampering_does_not_produce_valuation(archive):
    app = create_research_app(replace(archive, rate_artifact=archive.rate_artifact + b" "), bearer_token=TOKEN)
    assert send(app, "GET", COVERAGE, headers=HEADERS).json()["status"] == "OK"  # Input coverage only.
    rejected(send(app, "POST", URL, headers=HEADERS, json=BODY), 200, "RATE_UNAVAILABLE")


def test_source_mapping_is_snapshotted(archive):
    original = dict(archive.evidence)
    frozen = replace(archive, evidence=original)
    original["option_contract_base.json"] += b" "
    assert send(create_research_app(frozen, bearer_token=TOKEN), "GET", COVERAGE, headers=HEADERS).json()["status"] == "OK"


def test_concurrent_computation_busy_and_recovery(archive):
    entered, release = Event(), Event()

    def clock():
        entered.set()
        if not release.wait(timeout=5):
            raise RuntimeError("Test timed out")
        return datetime(2026, 11, 24, 7, 0, tzinfo=timezone.utc)

    app = create_research_app(archive, bearer_token=TOKEN, clock=clock)

    async def exercise():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://testserver") as client:
            first = asyncio.create_task(client.get(COVERAGE, headers=HEADERS))
            try:
                assert await asyncio.to_thread(entered.wait, 3)
                busy = await client.post(URL, headers=HEADERS, json=BODY)
                rejected(busy, 429, "BUSY")
                assert busy.headers["retry-after"] == "1"
            finally:
                release.set()
                done = await first
            assert done.status_code == 200 and done.json()["status"] == "OK"
            retry = await client.get(COVERAGE, headers=HEADERS)
            assert retry.json()["status"] == "OK"

    asyncio.run(exercise())


def test_internal_exception_sanitized_and_lock_released(archive):
    calls = 0

    def clock():
        nonlocal calls
        calls += 1
        if calls == 1:
            raise RuntimeError("sentinel-private-error")
        return datetime(2026, 11, 24, 7, 0, tzinfo=timezone.utc)

    app = create_research_app(archive, bearer_token=TOKEN, clock=clock)
    response = send(app, "GET", COVERAGE, headers=HEADERS)
    rejected(response, 500, "INTERNAL_ERROR")
    assert "sentinel-private-error" not in response.text
    assert send(app, "GET", COVERAGE, headers=HEADERS).json()["status"] == "OK"


def test_openapi_exact_routes_security_and_no_public_docs(app):
    schema = app.openapi()
    assert set(schema["paths"]) == {COVERAGE, URL}
    assert set(schema["paths"][COVERAGE]) == {"get"}
    assert set(schema["paths"][URL]) == {"post"}
    assert schema["components"]["securitySchemes"]["ResearchBearer"]["scheme"] == "bearer"
    assert schema["paths"][URL]["post"]["security"] == [{"ResearchBearer": []}]
    request = schema["paths"][URL]["post"]["requestBody"]["content"]["application/json"]["schema"]
    assert request["additionalProperties"] is False and "$defs" not in request
    assert request["properties"]["schema_version"]["const"] == "1.0"
    assert request["properties"]["positions"]["maxProperties"] == 2
    assert request["properties"]["heatmap_price_shocks"]["maxItems"] == 5
    rejected(send(app, "GET", "/openapi.json"), 404, "NOT_FOUND")
    rejected(send(app, "GET", "/docs"), 404, "NOT_FOUND")
    rejected(send(app, "POST", "/api/v1/research/orders", headers=HEADERS, json={}), 404, "NOT_FOUND")
    rejected(send(app, "DELETE", COVERAGE, headers=HEADERS), 405, "METHOD_NOT_ALLOWED")
