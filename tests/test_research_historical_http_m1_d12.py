"""T06 wire adapter tests with in-memory HTTP responses; zero sockets."""
from hashlib import sha256
import json
from urllib.parse import parse_qs, urlsplit

import pytest

import vnpy_optionmaster.research_historical_http as http
from vnpy_optionmaster.research_historical_provider import (
    CAPS, CATALOG_SQL, PROFILE_SQL, CatalogWindow, HistoricalQuery, catalog_query_plan, discover_history,
)
from vnpy_optionmaster.research_strategy_payoff import StrategyUnavailable

HOST = "10.254.254.254"  # Synthetic RFC 1918 address; no sockets in these tests.
ACCOUNT = "fixture-reader"
SERVER = "fixture-server"
SECRET = "fixture-only-not-a-credential"


def plan():
    return catalog_query_plan(CatalogWindow("SHFE", "CU", "XSGE.CU2701.XSGE", "2026-11-20", "2026-11-20"))


def body(rows):
    # Mock wire metadata matches the observed ClickHouse schema; no sockets.
    if rows and "server_name" in rows[0]:
        meta = http._META[PROFILE_SQL]
    elif rows and "parts" in rows[0]:
        meta = http._PLAN_META
    else:
        meta = http._META[CATALOG_SQL]
    return json.dumps({"data":rows,"rows":len(rows),
                       "meta":[{"name":n,"type":k} for n,k in meta],
                       "statistics":{"rows_read":10,"bytes_read":100,"elapsed":0.01}}).encode()


def profile(**patch):
    return [{"name":k,"value":v,"server_name":SERVER,"reader":ACCOUNT,"database":"quantfeed"}
            for k,v in {**CAPS,**patch}.items()]


def explain(rows=8192):
    return [{"database":"quantfeed","table":"option_contract_terms_daily","parts":1,"marks":1,"rows":rows}]


def catalog():
    return [{"trade_date":"2026-11-20","chain_id":"chain1","underlying_symbol":"CU2701.XSGE","physical_rows":12}]


class Response:
    def __init__(self, raw, server=SERVER, status=200):
        self.raw = raw
        self.status = status
        self.server = server

    def getheader(self, name):
        return {"Content-Length": str(len(self.raw)), "X-ClickHouse-Server-Display-Name": self.server}.get(name)

    def read(self, size):
        return self.raw[:size]


def fake_http(monkeypatch, responses, expected=None):
    calls = []
    class Conn:
        def __init__(self, host, port, *, timeout):
            assert (host, port) == (HOST,8123)
            assert 0 < timeout <= 8

        def request(self, method, url, body, headers):
            assert method == "POST"
            assert headers["Authorization"].startswith("Basic ")
            assert headers["Connection"] == "close"
            params = parse_qs(urlsplit(url).query)
            assert params["database"] == ["quantfeed"] and params["wait_end_of_query"] == ["1"]
            if expected is not None:
                q = expected[len(calls)]
                assert body.decode() == q.sql
                assert {k.removeprefix("param_"):v[0] for k,v in params.items() if k.startswith("param_")} == dict(q.parameters)
            calls.append((body.decode(), params))

        def getresponse(self):
            r = responses[len(calls)-1]
            if isinstance(r, Exception):
                raise r
            return r

        def close(self):
            pass
    monkeypatch.setattr(http, "HTTPConnection", Conn)
    return calls


def transport(tmp_path, queries=None):
    return http.HistoricalHttpTransport(host=HOST,port=8123,account=ACCOUNT,server=SERVER,
        password=lambda:SECRET,approved_plan=queries or plan(),authorization_id="offline-fixture",
        evidence_dir=tmp_path/"one-shot",allow_plaintext_lan=True)


def ledger(tmp_path):
    return [json.loads(x) for x in (tmp_path/"one-shot"/"requests.jsonl").read_text().splitlines()]


def read_catalog(t):
    return discover_history(CatalogWindow("SHFE","CU","XSGE.CU2701.XSGE","2026-11-20","2026-11-20"),
        transport=t,target=t.target,server=SERVER,account=ACCOUNT,max_requests=3)


def test_fixed_plan_and_durable_one_shot(tmp_path,monkeypatch):
    calls = fake_http(monkeypatch,[Response(body(profile())),Response(body(explain())),Response(body(catalog()))],plan())
    t = transport(tmp_path)
    assert not (tmp_path/"one-shot").exists()
    c = read_catalog(t)
    assert len(c) == 1 and c[0].physical_terms_rows == 12 and c[0].coverage == "COVERAGE_UNKNOWN"
    assert len(calls) == t.attempts == 3
    records = ledger(tmp_path)
    ats = [r for r in records if r["event"]=="attempt"]
    res = [r for r in records if r["event"]=="result"]
    assert len(ats)==len(res)==3 and [r["query_id"] for r in ats]==[r["query_id"] for r in res]
    assert records[-1]["event"]=="batch_terminal" and records[-1]["status"]=="OBSERVED"
    for r in res:
        assert r["status"]=="PASS"
        p=tmp_path/"one-shot"/r["artifact"]
        assert sha256(p.read_bytes()).hexdigest()==r["response_sha256"]
    for p in (tmp_path/"one-shot").iterdir():
        assert SECRET.encode() not in p.read_bytes()
    with pytest.raises(StrategyUnavailable):
        t.select(PROFILE_SQL,{},timeout_seconds=8)
    with pytest.raises(StrategyUnavailable):
        read_catalog(transport(tmp_path))
    assert len(calls)==3


@pytest.mark.parametrize("kind",["caps","identity","estimate","timeout","bad_json","truncated","schema"])
def test_failed_request_stops_no_retry(tmp_path,monkeypatch,kind):
    first=Response(body(profile()))
    second=Response(body(explain()))
    third=Response(body(catalog()))
    count=1
    if kind=="caps":
        first=Response(body(profile(max_rows_to_read="0")))
    elif kind=="identity":
        first=Response(body(profile()),server="other")
    elif kind=="estimate":
        second=Response(body(explain(100001)))
        count=2
    elif kind=="timeout":
        second=TimeoutError("simulated")
        count=2
    elif kind=="bad_json":
        first=Response(b'{"data":[],"data":[]}')
    elif kind=="truncated":
        data=json.loads(body(catalog()))
        data["rows_before_limit_at_least"]=2
        third=Response(json.dumps(data).encode())
        count=3
    elif kind=="schema":
        data=json.loads(body(profile()))
        data["meta"][0]["type"]="UInt64"
        first=Response(json.dumps(data).encode())
    calls=fake_http(monkeypatch,[first,second,third])
    t=transport(tmp_path)
    with pytest.raises(StrategyUnavailable):
        read_catalog(t)
    assert t.attempts==len(calls)==count
    records=ledger(tmp_path)
    assert records[-1]["status"]=="BLOCKED" and records[-1]["attempts"]==count
    with pytest.raises(StrategyUnavailable):
        t.select(PROFILE_SQL,{},timeout_seconds=8)
    assert len(calls)==count
    if kind=="timeout":
        assert [r for r in records if r["event"]=="result"][-1]["server_outcome"]=="UNKNOWN"


def test_wrong_plan_or_sql_never_dispatch(tmp_path,monkeypatch):
    calls=fake_http(monkeypatch,[])
    q=plan()
    with pytest.raises(StrategyUnavailable):
        transport(tmp_path,(q[0],q[1],HistoricalQuery("INSERT INTO x VALUES(1)",())))
    t=transport(tmp_path)
    with pytest.raises(StrategyUnavailable):
        t.select(CATALOG_SQL,dict(q[-1].parameters),timeout_seconds=8)
    assert not calls and not (tmp_path/"one-shot").exists()


def test_plan_parameter_change_fails_without_dispatch(tmp_path,monkeypatch):
    calls=fake_http(monkeypatch,[Response(body(profile()))])
    t=transport(tmp_path)
    t.select(PROFILE_SQL,{},timeout_seconds=8)
    q=plan()[1]
    params=dict(q.parameters)
    params["end"]="2026-11-21"
    with pytest.raises(StrategyUnavailable):
        t.select(q.sql,params,timeout_seconds=8)
    t.seal()
    assert len(calls)==1 and ledger(tmp_path)[-1]["status"]=="BLOCKED"
