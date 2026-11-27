"""D04: HTTP wire seam tests. No real endpoint or credentials."""
from datetime import datetime, timezone
import json
import math
import ssl
from struct import pack, unpack
from urllib.parse import parse_qs, urlsplit

import pytest

from vnpy_optionmaster.research_history_repository import (
    _OPTION_SQL,
    _PROFILE_SQL,
)
from vnpy_optionmaster.research_clickhouse_http import ClickHouseHttpSelect, ClickHouseReadError


def payload(fields, rows):
    return json.dumps({
        "meta": [{"name": name, "type": kind} for name, kind in fields],
        "data": rows, "rows": len(rows), "statistics": {"elapsed": 0.001},
    }).encode()


SETTINGS = {
    "readonly": "1", "max_rows_to_read": "100000", "max_bytes_to_read": "67108864",
    "max_execution_time": "5", "max_result_rows": "64", "max_result_bytes": "1048576",
    "read_overflow_mode": "throw", "result_overflow_mode": "throw", "timeout_overflow_mode": "throw",
}
OPTION_FIELDS = [
    ("symbol", "String"), ("exchange", "LowCardinality(String)"), ("trade_date", "Date"),
    ("close", "Nullable(String)"), ("settlement", "Nullable(String)"),
    ("source", "LowCardinality(String)"), ("ingested_at", "String"),
    ("__type_close", "String"), ("__type_settlement", "String"), ("__type_ingested_at", "String"),
]
OPTION_ROW = {
    "symbol": "A2211-C-5000.XDCE", "exchange": "XDCE", "trade_date": "2022-09-07",
    "close": "4650001799632125952", "settlement": None, "source": "JQ",
    "ingested_at": "1791725934395",
    "__type_close": "Nullable(Float64)", "__type_settlement": "Nullable(Float64)",
    "__type_ingested_at": "DateTime64(3, 'Asia/Shanghai')",
}
PARAMS = {"symbol": "A2211-C-5000.XDCE", "trade_date": "2022-09-07"}


class FakeSocket:
    def settimeout(self, value):
        assert 0 < value <= 5


class FakeResponse:
    def __init__(self, body, *, status=200, server="test-server", extra_headers=(), will_close=False):
        self.body = body
        self.status = status
        self.will_close = will_close
        self.chunked = False
        self.headers = [
            ("Content-Type", "application/json; charset=UTF-8"),
            ("Content-Length", str(len(body))),
            ("X-ClickHouse-Server-Display-Name", server),
            ("X-ClickHouse-Format", "JSON"),
            *extra_headers,
        ]

    def getheaders(self):
        return self.headers

    def read1(self, size):
        out, self.body = self.body[:size], self.body[size:]
        return out

    def close(self):
        pass


class FakeConnection:
    def __init__(self, responses):
        self.responses = list(responses)
        self.requests = []
        self.sock = None
        self.timeout = None
        self.connect_count = 0
        self.closed = False

    def connect(self):
        self.connect_count += 1
        self.sock = FakeSocket()

    def request(self, method, url, body, headers):
        self.requests.append((method, url, body, headers))

    def getresponse(self):
        return self.responses.pop(0)

    def close(self):
        self.closed = True
        self.sock = None


def profile(settings=None):
    return FakeResponse(payload([("name", "String"), ("value", "String")], [
        {"name": k, "value": v} for k, v in (SETTINGS if settings is None else settings).items()
    ]))


def option(**updates):
    return FakeResponse(payload(OPTION_FIELDS, [{**OPTION_ROW, **updates}]))


def client(monkeypatch, responses):
    conn = FakeConnection(responses)
    monkeypatch.setattr("vnpy_optionmaster.research_clickhouse_http.HTTPSConnection", lambda *a, **kw: conn)
    transport = ClickHouseHttpSelect(
        host="example.invalid", port=8443, username="fixture", password="fixture-only",
        expected_server_name="test-server",
    )
    return transport, conn


def test_fixed_parameterized_https_session_preserves_float_and_epoch(monkeypatch):
    transport, conn = client(monkeypatch, [profile(), option()])
    with transport:
        rows = transport.select(_PROFILE_SQL, {}, timeout_seconds=5)
        assert {r["name"]: r["value"] for r in rows} == SETTINGS
        result = transport.select(_OPTION_SQL, PARAMS, timeout_seconds=5)
        assert result == ({
            "symbol": PARAMS["symbol"], "exchange": "XDCE", "trade_date": "2022-09-07",
            "close": 772.0, "settlement": None, "source": "JQ",
            "ingested_at": datetime(2026, 10, 11, 13, 38, 54, 395000, tzinfo=timezone.utc),
        },)
    assert conn.closed and conn.connect_count == 1 and len(conn.requests) == 2
    first, second = conn.requests
    q1, q2 = parse_qs(urlsplit(first[1]).query), parse_qs(urlsplit(second[1]).query)
    assert first[0] == second[0] == "POST"
    assert q1["session_id"] == q2["session_id"]
    assert q2["session_check"] == ["1"]
    assert q2["param_symbol"] == [PARAMS["symbol"]]
    assert q2["param_trade_date"] == [PARAMS["trade_date"]]
    assert PARAMS["symbol"].encode() not in second[2]
    assert b"{symbol:String}" in second[2] and b"FORMAT JSON" in second[2]
    assert b"reinterpretAsUInt64" in second[2]
    assert b"toUnixTimestamp64Milli" in second[2]
    assert first[3]["Authorization"].startswith("Basic ")
    assert "fixture-only" not in second[1]


def test_profile_required_before_market_and_unsafe_profile_cannot_be_bypassed(monkeypatch):
    transport, conn = client(monkeypatch, [profile({**SETTINGS, "max_bytes_to_read": "0"})])
    with pytest.raises(ClickHouseReadError):
        transport.select(_OPTION_SQL, PARAMS, timeout_seconds=5)
    assert not conn.requests
    transport.select(_PROFILE_SQL, {}, timeout_seconds=5)
    with pytest.raises(ClickHouseReadError):
        transport.select(_OPTION_SQL, PARAMS, timeout_seconds=5)
    assert len(conn.requests) == 1


@pytest.mark.parametrize("sql", ["SELECT 1", "DROP TABLE foo", _OPTION_SQL + "; SELECT 1"])
def test_only_exact_repository_selects_admitted(monkeypatch, sql):
    transport, conn = client(monkeypatch, [])
    with pytest.raises(ClickHouseReadError):
        transport.select(sql, PARAMS, timeout_seconds=5)
    assert not conn.requests


def test_nonfinite_float_is_not_sql_null(monkeypatch):
    transport, _ = client(monkeypatch, [
        profile(), option(close="9221120237041090560", settlement="9218868437227405312"),
    ])
    transport.select(_PROFILE_SQL, {}, timeout_seconds=5)
    row = transport.select(_OPTION_SQL, PARAMS, timeout_seconds=5)[0]
    assert math.isnan(row["close"])
    assert math.isinf(row["settlement"])


@pytest.mark.parametrize("field,value", [
    ("__type_close", "Float32"), ("__type_ingested_at", "DateTime64(6, 'UTC')"),
    ("close", "18446744073709551616"), ("close", "nan"),
    ("ingested_at", "2026-10-11 21:38:54.395"),
    ("trade_date", "20220907"),
])
def test_wire_schema_and_value_drift_fail_closed(monkeypatch, field, value):
    transport, conn = client(monkeypatch, [profile(), option(**{field: value})])
    transport.select(_PROFILE_SQL, {}, timeout_seconds=5)
    with pytest.raises(ClickHouseReadError):
        transport.select(_OPTION_SQL, PARAMS, timeout_seconds=5)
    assert conn.closed
    with pytest.raises(ClickHouseReadError):
        transport.select(_OPTION_SQL, PARAMS, timeout_seconds=5)
    assert len(conn.requests) == 2


@pytest.mark.parametrize("response", [
    FakeResponse(b'{}', status=302),
    FakeResponse(b'{}', server="different-server"),
    FakeResponse(b'{}', extra_headers=(("X-ClickHouse-Exception-Code", "241"),)),
    FakeResponse(b'{}', will_close=True),
    FakeResponse(b'{"meta":[],"data":[],"rows":0,"rows":0}'),
    FakeResponse(payload(OPTION_FIELDS, [OPTION_ROW]) + b'\nDB::Exception'),
    FakeResponse(payload(OPTION_FIELDS, [OPTION_ROW]), extra_headers=(("Content-Encoding", "gzip"),)),
])
def test_http_and_partial_or_ambiguous_response_never_return_rows(monkeypatch, response):
    transport, conn = client(monkeypatch, [profile(), response])
    transport.select(_PROFILE_SQL, {}, timeout_seconds=5)
    with pytest.raises(ClickHouseReadError):
        transport.select(_OPTION_SQL, PARAMS, timeout_seconds=5)
    assert conn.closed

def test_tls_verification_must_remain_enabled_at_connect(monkeypatch):
    context = ssl.create_default_context()
    conn = FakeConnection([profile()])
    monkeypatch.setattr("vnpy_optionmaster.research_clickhouse_http.HTTPSConnection", lambda *a, **kw: conn)
    transport = ClickHouseHttpSelect(
        host="example.invalid", port=8443, username="fixture", password="fixture-only",
        expected_server_name="test-server", tls_context=context,
    )
    context.check_hostname = False
    context.verify_mode = ssl.CERT_NONE
    with pytest.raises(ClickHouseReadError):
        transport.select(_PROFILE_SQL, {}, timeout_seconds=5)
    assert conn.connect_count == 0 and not conn.requests


def test_extreme_timeout_rejected_before_connect(monkeypatch):
    transport, conn = client(monkeypatch, [])
    with pytest.raises(ClickHouseReadError):
        transport.select(_PROFILE_SQL, {}, timeout_seconds=10**1000)
    assert not conn.requests and conn.connect_count == 0


@pytest.mark.parametrize("params", [
    {"symbol": "A2211-C-5000.XDCE"},
    {**PARAMS, "unexpected": "x"},
    {**PARAMS, "trade_date": "20220907"},
    {**PARAMS, "symbol": "a" + chr(92) + "N"},
])
def test_invalid_parameter_shape_never_dispatches(monkeypatch, params):
    transport, conn = client(monkeypatch, [profile()])
    transport.select(_PROFILE_SQL, {}, timeout_seconds=5)
    with pytest.raises(ClickHouseReadError):
        transport.select(_OPTION_SQL, params, timeout_seconds=5)
    assert len(conn.requests) == 1


def test_response_framing_rejects_truncated_content_length(monkeypatch):
    response = option()
    response.headers = [(name, str(int(value) + 1) if name == "Content-Length" else value)
                        for name, value in response.headers]
    transport, conn = client(monkeypatch, [profile(), response])
    transport.select(_PROFILE_SQL, {}, timeout_seconds=5)
    with pytest.raises(ClickHouseReadError, match="INCOMPLETE_HTTP_RESPONSE"):
        transport.select(_OPTION_SQL, PARAMS, timeout_seconds=5)
    assert conn.closed


TERMS_FIELDS = [
    ("symbol", "String"), ("exchange", "LowCardinality(String)"), ("trade_date", "Date"),
    ("portfolio_code", "String"), ("chain_id", "String"), ("underlying_symbol", "String"),
    ("expiry_date", "Date"), ("option_type", "LowCardinality(String)"),
    ("exercise_style", "LowCardinality(String)"), ("strike", "Nullable(String)"),
    ("contract_size", "Nullable(String)"), ("contract_version", "String"),
    ("source", "LowCardinality(String)"), ("provenance", "LowCardinality(String)"),
    ("ingested_at", "String"), ("__type_strike", "String"),
    ("__type_contract_size", "String"), ("__type_ingested_at", "String"),
]
FUTURES_FIELDS = [
    ("symbol", "String"), ("exchange", "LowCardinality(String)"), ("trade_date", "Date"),
    ("product_symbol", "String"), ("close", "String"), ("settlement", "Nullable(String)"),
    ("price_source", "LowCardinality(String)"), ("settlement_source", "LowCardinality(String)"),
    ("ingested_at", "String"), ("__type_close", "String"), ("__type_settlement", "String"),
    ("__type_ingested_at", "String"),
]


def wire_response(fields, row, source_types):
    wire = {}
    for name, kind in fields:
        if name.startswith("__type_"):
            wire[name] = source_types[name[len("__type_"):]]
        elif name == "ingested_at":
            dt = row[name] - datetime(1970, 1, 1, tzinfo=timezone.utc)
            wire[name] = str((dt.days * 86400 + dt.seconds) * 1000 + dt.microseconds // 1000)
        elif name in source_types:
            value = row[name]
            wire[name] = None if value is None else str(unpack("<Q", pack("<d", value))[0])
        elif kind == "Date":
            wire[name] = row[name].isoformat()
        else:
            wire[name] = row[name]
    return FakeResponse(payload(fields, [wire]))


def test_real_repository_runs_over_concrete_http_transport_fixture(monkeypatch):
    from test_research_history_repository_m1_d03 import (
        FakeSelect, OPT, PUT, QUOTE, READ_AT, SETTLEMENT_SOURCE, _request, fixture_registry,
    )
    from vnpy_optionmaster.research_data_basis import HistoricalBasis
    from vnpy_optionmaster.research_history_repository import HistoricalRepository, HistoricalReadStatus

    data = FakeSelect()
    ts = {"ingested_at": "DateTime64(3, 'Asia/Shanghai')"}
    responses = [profile()]
    for symbol in (OPT.symbol, PUT.symbol):
        responses.append(wire_response(TERMS_FIELDS, data.terms[symbol][0], {
            "strike": "Nullable(Float64)", "contract_size": "Nullable(Float64)", **ts,
        }))
    for symbol in (OPT.symbol, PUT.symbol):
        responses.append(wire_response(OPTION_FIELDS, data.options[symbol][0], {
            "close": "Nullable(Float64)", "settlement": "Nullable(Float64)", **ts,
        }))
    responses.append(wire_response(FUTURES_FIELDS, data.futures[QUOTE.symbol][0], {
        "close": "Float64", "settlement": "Nullable(Float64)", **ts,
    }))
    transport, conn = client(monkeypatch, responses)
    registry, approval, artifacts = fixture_registry()
    with transport:
        repo = HistoricalRepository(
            transport, registry, approval, artifacts,
            allowed_settlement_sources=(SETTLEMENT_SOURCE,), clock=lambda: READ_AT,
        )
        result = repo.materialize(_request())
    assert result.status is HistoricalReadStatus.CANDIDATE
    candidate = result.candidate
    assert candidate is not None and candidate.valuation_ready is False
    assert candidate.basis.price_basis is HistoricalBasis.SETTLEMENT
    assert [(leg.option_price, leg.underlying_price) for leg in candidate.basis.legs] == [(772, 5772), (21, 5772)]
    assert len(conn.requests) == 6 and conn.connect_count == 1
    assert conn.closed and not conn.responses
