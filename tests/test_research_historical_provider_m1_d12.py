"""Offline T06 history-provider tests; never connect to ClickHouse."""
import ast
from copy import deepcopy
from dataclasses import replace
from datetime import datetime, timezone
from hashlib import sha256
import json
import socket

import pytest
from fastapi.testclient import TestClient

from vnpy_optionmaster.research_historical_provider import (
    CAPS, CATALOG_SQL, FUTURES_SQL, OPTION_SQL, PROFILE_SQL, TERMS_SQL,
    CatalogWindow, ChainScope, HistoricalClickHouseProvider, HistoricalReadReceipt,
    ReviewedOption, discover_history, history_query_plan,
)
from vnpy_optionmaster.research_strategy_api import create_strategy_payoff_app
from vnpy_optionmaster.research_strategy_api_dto import DomainDTO, StrategyRateEvidenceDTO
from vnpy_optionmaster.research_strategy_payoff import StrategyUnavailable

DAY = "2026-11-20"
PIN = "a" * 64
TARGET = "fixture-only"
SERVER = "fixture-server"
ACCOUNT = "fixture-reader"
NOW = datetime(2026, 11, 25, 0, tzinfo=timezone.utc)
TOKEN = "offline-historical-provider-fixture-token"
HEADERS = {"Authorization": "Bearer " + TOKEN}
PATH = "/api/v4/research/strategy"


@pytest.fixture(autouse=True)
def deny_network(monkeypatch):
    def refused(*args, **kwargs):
        raise AssertionError("offline T06 must never open a network socket")
    monkeypatch.setattr(socket.socket, "connect", refused)
    monkeypatch.setattr(socket.socket, "connect_ex", refused)


def scope(day=DAY, month="cu2701", product="CU"):
    u = "SHFE." + month
    return ChainScope(
        exchange="SHFE", product=product, trade_date=day, underlying_id=u,
        delivery_month=month, portfolio_code="XSGE." + month.upper() + ".XSGE",
        chain_id="chain-" + month, vendor_underlying=month.upper() + ".XSGE",
        vendor_exchange="XSGE", futures_product="SHFE." + product,
        futures_exchange="SHFE", option_source="JQ", future_price_source="GM",
        future_settlement_source="GM", multiplier=5.0, quote_unit="CNY_PER_TON",
        underlying_terms_version="underlying-v1", policy_pin=PIN,
        reference_at=day + "T15:00:00+08:00", terms_provenance=("REVIEWED",),
        price_domain=DomainDTO(status="VERIFIED", min_price=0.0, max_price=None,
                               min_inclusive=False, max_inclusive=False),
        options=tuple(ReviewedOption(
            symbol=month.upper() + right + "100.XSGE", option_type=kind,
            strike=100.0, multiplier=5.0, expiry_date="2026-12-25",
            exercise_style="EUROPEAN", terms_version="terms-v1",
            expiry_at="2026-12-25T15:00:00+08:00",
        ) for right, kind in (("C", "CALL"), ("P", "PUT"))),
        rates=(StrategyRateEvidenceDTO(expiry_id=u + "|2026-12-25",
                quote_date=day, continuous_rate=0.02, policy_id="FIXTURE_RATE", source_pin=PIN),),
    )


class Fake:
    def __init__(self, scopes):
        self.scopes = tuple(scopes)
        self.calls = []
        self.terms = {}
        self.quotes = {}
        self.futures = {}
        self.patch = None
        self.profile = dict(CAPS)
        self.catalog = [{"trade_date": DAY, "chain_id": "chain-cu2701",
                         "underlying_symbol": "CU2701.XSGE", "physical_rows": 2}]
        for s in self.scopes:
            for o in s.options:
                k = s.trade_date, o.symbol
                self.terms[k] = dict(symbol=o.symbol, exchange=s.vendor_exchange,
                    trade_date=s.trade_date, portfolio_code=s.portfolio_code, chain_id=s.chain_id,
                    underlying_symbol=s.vendor_underlying, expiry_date=o.expiry_date,
                    option_type=o.option_type, exercise_style=o.exercise_style, strike=o.strike,
                    contract_size=o.multiplier, contract_version=o.terms_version, source=s.option_source,
                    provenance="REVIEWED", ingested_at="2026-11-25 08:00:00.000")
                self.quotes[k] = dict(symbol=o.symbol, exchange=s.vendor_exchange, trade_date=s.trade_date,
                    close=5.0, settlement=5.0, source=s.option_source, ingested_at="2026-11-25 08:00:00.000")
            self.futures[s.trade_date, s.underlying_id] = dict(symbol=s.underlying_id,
                exchange=s.futures_exchange, trade_date=s.trade_date, product_symbol=s.futures_product,
                close=100.0, settlement=100.0, price_source=s.future_price_source,
                settlement_source=s.future_settlement_source, ingested_at="2026-11-25 08:00:00.000")

    def select(self, sql, parameters, *, timeout_seconds):
        self.calls.append((sql, dict(parameters), timeout_seconds))
        if sql == PROFILE_SQL:
            rows = [{"name": k, "value": v, "server_name": SERVER, "reader": ACCOUNT,
                     "database": "quantfeed"} for k, v in self.profile.items()]
        elif sql.startswith("EXPLAIN ESTIMATE "):
            base = sql.removeprefix("EXPLAIN ESTIMATE ")
            table = {TERMS_SQL: "option_contract_terms_daily", OPTION_SQL: "option_daily",
                     FUTURES_SQL: "futures_daily", CATALOG_SQL: "option_contract_terms_daily"}[base]
            rows = [{"database": "quantfeed", "table": table, "parts": 1, "rows": 8192, "marks": 1}]
        elif sql == CATALOG_SQL:
            rows = self.catalog
        elif sql in (TERMS_SQL, OPTION_SQL):
            data = self.terms if sql == TERMS_SQL else self.quotes
            symbols = ast.literal_eval(parameters["symbols"])
            rows = [data[(parameters["day"], x)] for x in symbols if (parameters["day"], x) in data]
        elif sql == FUTURES_SQL:
            row = self.futures.get((parameters["day"], parameters["symbol"]))
            rows = [] if row is None else [row]
        else:
            raise AssertionError("arbitrary SQL")
        rows = deepcopy(rows)
        if self.patch:
            rows = self.patch(sql, rows)
        return HistoricalReadReceipt(
            target=TARGET, server=SERVER, account=ACCOUNT, query_id="fixture-" + str(len(self.calls)),
            observed_at=NOW.isoformat(), rows=tuple(rows), scan_rows=100, scan_bytes=1024,
            response_bytes=4096, elapsed_seconds=0.01,
            body_sha256=sha256(json.dumps(rows, sort_keys=True).encode()).hexdigest(),
        )


def provider(scopes, fake=None, budget=64):
    f = fake or Fake(scopes)
    return HistoricalClickHouseProvider(scopes, transport=f, target=TARGET, server=SERVER,
        account=ACCOUNT, max_requests=budget, fixture=True, clock=lambda: NOW), f


def test_scope_catalog_is_discovery_only_and_bounded():
    f = Fake([])
    rows = discover_history(CatalogWindow("SHFE", "CU", "XSGE.CU2701.XSGE", DAY, DAY),
        transport=f, target=TARGET, server=SERVER, account=ACCOUNT, max_requests=3)
    assert len(f.calls) == 3
    assert rows[0].trade_date == DAY and rows[0].physical_terms_rows == 2
    assert rows[0].coverage == "COVERAGE_UNKNOWN"


def test_exact_chains_and_same_day_snapshot_reuse():
    scopes = (scope(), scope(month="cu2702"), scope(day="2026-11-19"))
    p, f = provider(scopes)
    assert f.calls == []
    p.observe()
    count = len(f.calls)
    assert count == 19
    cov = p.coverage(None, None, None)
    assert cov.products[0].latest_complete_trade_date is None
    a = p.chain("SHFE", "CU", DAY, scopes[0].underlying_id, None, None)
    b = p.chain("SHFE", "CU", DAY, scopes[1].underlying_id, a.snapshot.snapshot_id, None)
    assert a.snapshot == b.snapshot
    assert a.snapshot.provider == "SYNTHETIC_FIXTURE" and not a.snapshot.production_valuation_ready
    assert a.rows[0].call.market_price == 5 and a.rows[0].put.market_price == 5
    snap = p.load_snapshot(a.snapshot.snapshot_id)
    assert snap is not None and len(snap.options) == 4 and len(snap.underlyings) == 2
    snap.metadata.source_versions["tampered"] = "f" * 64
    assert "tampered" not in p.load_snapshot(a.snapshot.snapshot_id).metadata.source_versions
    f.quotes[DAY, scopes[0].options[0].symbol]["settlement"] = 999
    assert p.chain("SHFE", "CU", DAY, scopes[0].underlying_id, None, None).rows[0].call.market_price == 5
    assert len(f.calls) == count
    assert p.coverage("SHFE", "CU", None).products[0].days[0].trade_date == DAY
    with pytest.raises(StrategyUnavailable, match="SNAPSHOT_STALE"):
        p.chain("SHFE", "CU", DAY, scopes[0].underlying_id, "f"*64, None)
    with pytest.raises(StrategyUnavailable):
        p.observe()
    assert len(f.calls) == count


def test_partial_month_does_not_block_valid_leg():
    s = scope()
    f = Fake([s])
    del f.quotes[DAY, s.options[1].symbol]
    p, _ = provider([s], f)
    p.observe()
    m = p.coverage(None, None, None).products[0].days[0].months[0]
    assert m.status == "INCOMPLETE" and m.discovered_contracts == 2 and m.admitted_contracts == 1
    chain = p.chain("SHFE", "CU", DAY, s.underlying_id, None, None)
    assert chain.rows[0].call.payoff_eligibility.status == "ELIGIBLE"
    assert chain.rows[0].put.market_price is None
    assert chain.rows[0].put.payoff_eligibility.reason == "MARK_UNAVAILABLE"


def test_actual_empty_scope_no_fabrication():
    s = scope()
    f = Fake([s])
    f.terms.clear()
    f.quotes.clear()
    p, _ = provider([s], f)
    p.observe()
    m = p.coverage(None, None, None).products[0].days[0].months[0]
    assert m.status == "INCOMPLETE" and m.admitted_contracts == 0
    c = p.chain("SHFE", "CU", DAY, s.underlying_id, None, None)
    assert c.rows == []
    assert p.load_snapshot(c.snapshot.snapshot_id).options == ()


@pytest.mark.parametrize("bad", ["duplicate", "wrong_key", "plan", "profile", "wrong_target"])
def test_unsafe_batch_stops_and_no_retry(bad):
    s = scope()
    f = Fake([s])
    if bad == "profile":
        f.profile["max_bytes_to_read"] = "0"
    elif bad == "duplicate":
        f.patch = lambda sql, rows: rows + rows[:1] if sql == TERMS_SQL else rows
    elif bad == "wrong_key":
        f.patch = lambda sql, rows: [dict(rows[0], symbol="OTHER")] if sql == TERMS_SQL else rows
    elif bad == "plan":
        f.patch = lambda sql, rows: [dict(rows[0], rows=100001)] if sql.startswith("EXPLAIN") else rows
    p, _ = provider([s], f)
    if bad == "wrong_target":
        original = f.select
        f.select = lambda *a, **kw: replace(original(*a, **kw), account="wrong")
    with pytest.raises(StrategyUnavailable):
        p.observe()
    n = len(f.calls)
    with pytest.raises(StrategyUnavailable):
        p.observe()
    assert len(f.calls) == n
    with pytest.raises(StrategyUnavailable):
        p.coverage(None, None, None)


def test_budget_refusal_before_transport():
    p, f = provider([scope()], budget=6)
    with pytest.raises(StrategyUnavailable):
        p.observe()
    assert f.calls == []


def test_version_mismatch_not_admitted_and_basis_not_mixed():
    s = scope()
    f = Fake([s])
    f.terms[DAY, s.options[0].symbol]["contract_version"] = "different"
    f.quotes[DAY, s.options[1].symbol]["settlement"] = None
    p, _ = provider([s], f)
    p.observe()
    c = p.chain("SHFE", "CU", DAY, s.underlying_id, None, None)
    assert c.rows[0].call is None
    assert c.rows[0].put.market_price is None
    assert c.snapshot.price_basis == "SETTLEMENT"


def test_composed_v4_api_payoff_and_model_without_database_calls():
    s = scope()
    p, f = provider([s])
    p.observe()
    n = len(f.calls)
    app = create_strategy_payoff_app(p, bearer_token=TOKEN, market_data=p, allow_synthetic_fixture=True)
    with TestClient(app) as client:
        params = {"exchange":"SHFE","product":"CU","trade_date":DAY,"underlying_id":s.underlying_id}
        assert client.get(PATH + "/coverage").status_code == 401
        assert client.get(PATH + "/coverage", headers=HEADERS).json()["status"] == "OK"
        chain = client.get(PATH + "/chain", params=params, headers=HEADERS).json()["chain"]
        q = chain["rows"][0]["call"]
        entry = {"entry_trade_date":DAY,"entry_snapshot_id":chain["snapshot"]["snapshot_id"],
                 "entry_origin":"ADMITTED_MARK"}
        req = {"schema_version":"4.0","portfolio_revision":1,"snapshot_id":entry["entry_snapshot_id"],
               "options":[{**entry,"contract_id":q["contract_id"],"quantity":1,"entry_price":5.0}],
               "underlyings":[],"reference_underlying_id":s.underlying_id,"curve_price_returns":[-.1,0.,.1],
               "global_price_shock":0.,"price_shocks_by_underlying":{}}
        resp = client.post(PATH + "/payoffs", json=req, headers=HEADERS).json()
        assert resp["status"] == "PARTIAL" and resp["reason"] == "INVALID_SCENARIO"
        assert resp["payoff"]["accounting"]["net_premium"] == 25
        points = {x["price_return"]: x["pnl"] for x in resp["payoff"]["terminal"]["points"]}
        assert points[-1.0] is None
        assert [points[x] for x in (-.1, 0., .1)] == [-25, -25, 25]
        assert points[.05] == 0 and resp["payoff"]["risk"]["breakevens"] == [.05]
        v = client.post(PATH + "/valuations", json={**req,"scenario_at":DAY+"T15:00:00+08:00",
                "global_iv_shock":0.,"iv_shocks_by_expiry":{}}, headers=HEADERS).json()
        assert v["valuation"] is not None
        assert v["valuation"]["current"]["status"] == "OK"
    assert len(f.calls) == n

def test_multi_product_and_more_than_one_result_page():
    a = scope()
    b = scope(month="al2701", product="AL")
    # 33 strike pairs: two physical key chunks, not an unbounded query.
    template = a.options[0]
    opts = tuple(replace(template, symbol=f"CU2701{right}{strike}.XSGE",
                         option_type=kind, strike=float(strike))
                 for strike in range(80,113)
                 for right,kind in (("C","CALL"),("P","PUT")))
    a = replace(a, options=opts)
    p,f = provider([a,b])
    expected = history_query_plan([a,b])
    p.observe()
    assert len(f.calls) == len(expected) == 17
    assert all(sql==q.sql and params==dict(q.parameters) for (sql,params,_),q in zip(f.calls,expected,strict=True))
    terms_calls = [ast.literal_eval(params["symbols"]) for sql,params,_ in f.calls if sql==TERMS_SQL]
    assert [len(x) for x in terms_calls]==[63,3,2]
    assert len(p.coverage(None,None,None).products)==2
    assert len(p.chain("SHFE","CU",DAY,a.underlying_id,None,None).rows)==33
    assert len(p.chain("SHFE","AL",DAY,b.underlying_id,None,None).rows)==1


def test_missing_future_never_enables_option_payoff():
    s = scope()
    f = Fake([s])
    f.futures.clear()
    p,_ = provider([s],f)
    p.observe()
    chain=p.chain("SHFE","CU",DAY,s.underlying_id,None,None)
    assert chain.underlying.market_price is None
    assert chain.rows[0].call.market_price==5
    assert chain.rows[0].call.payoff_eligibility.reason=="MARK_UNAVAILABLE"
    assert p.coverage_details()[0]["eligible_options"]==0
    assert p.coverage_details()[0]["coverage"]=="PARTIAL"


def test_duplicate_scope_rejected_before_read():
    s=scope()
    f=Fake([s])
    with pytest.raises(StrategyUnavailable):
        provider([s,s],f)
    assert not f.calls


def test_fixture_never_admitted_by_real_api():
    s=scope()
    p,_=provider([s])
    p.observe()
    with TestClient(create_strategy_payoff_app(p,bearer_token=TOKEN,market_data=p)) as client:
        params={"exchange":"SHFE","product":"CU","trade_date":DAY,"underlying_id":s.underlying_id}
        result=client.get(PATH+"/chain",params=params,headers=HEADERS).json()
        assert result["status"]=="UNAVAILABLE" and result["reason"]=="CANDIDATE_ONLY"

def test_reviewed_d09_raw_rows_replay_uses_pinned_legacy_terms():
    # Real retained 2026-11-24 CH observations, replayed through a simulated
    # transport. No fresh database query, no claim of current source admission.
    from pathlib import Path

    from vnpy_optionmaster.research_catalog_api import CATALOG_PIN, load_reviewed_catalog
    from vnpy_optionmaster.research_historical_registry import load_pinned_catalog_history_scopes
    from vnpy_optionmaster.research_multi_chain_archive import _children

    root = Path(__file__).resolve().parents[1] / "docs/evidence"
    scopes = load_pinned_catalog_history_scopes(root)
    assert len(scopes) == 3
    assert len({s.trade_date for s in scopes}) == 2
    assert len({s.underlying_id for s in scopes}) == 2
    assert sum(len(s.options) for s in scopes) == 198
    assert all(o.binding_pin == CATALOG_PIN and o.raw_exercise_style == "UNKNOWN"
               and o.raw_contract_version == "" for s in scopes for o in s.options)
    f = Fake(scopes)
    f.terms.clear()
    f.quotes.clear()
    f.futures.clear()
    for child, packet in _children(load_reviewed_catalog(root).sources):
        day = child.scope.day.isoformat()
        for binding in packet["bindings"]:
            t = binding["raw_terms"]
            q = binding["raw_quote"]
            f.terms[day, t["symbol"]] = t
            f.quotes[day, q["symbol"]] = q
        u = packet["underlying"]
        f.futures[day, u["symbol"]] = u
    p, _ = provider(scopes, f)
    p.observe()
    for s in scopes:
        c = p.chain(s.exchange, s.product, s.trade_date, s.underlying_id, None, None)
        assert c.snapshot.provider == "SYNTHETIC_FIXTURE"
        actual = [q for row in c.rows for q in (row.call, row.put) if q is not None]
        assert len(actual) == len(s.options)
        assert all(q.payoff_eligibility.status == "ELIGIBLE" for q in actual)
        assert all(q.market_price == f.quotes[s.trade_date, q.contract_id]["settlement"] for q in actual)
        assert c.underlying.market_price == f.futures[s.trade_date, s.underlying_id]["settlement"]
    assert all(x.latest_complete_trade_date is None for x in p.coverage(None, None, None).products)


def test_unpinned_legacy_terms_not_accepted():
    s = scope()
    old = replace(s.options[0], raw_exercise_style="UNKNOWN", raw_contract_version="")
    f = Fake([s])
    with pytest.raises(StrategyUnavailable):
        provider([replace(s, options=(old, s.options[1]))], f)
    assert not f.calls
