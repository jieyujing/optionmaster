"""T07 actual independently approved D09 archive and composed v4 HTTP path."""
import asyncio
from pathlib import Path

import httpx
import pytest

from vnpy_optionmaster.research_catalog_api import CATALOG_PIN
from vnpy_optionmaster.research_strategy_archive import ReviewedStrategyArchive
from vnpy_optionmaster.research_strategy_api import create_strategy_payoff_app
from tests.test_research_strategy_payoff_m1_d12 import HEADERS, TOKEN

ROOT = Path(__file__).resolve().parents[1] / "docs/evidence"
PATH = "/api/v4/research/strategy"


@pytest.fixture(scope="module")
def provider():
    return ReviewedStrategyArchive(ROOT)


def test_exact_approved_roster_pin_and_no_fresh_claim(provider):
    c = provider.coverage(None,None,None)
    assert len(c.products)==1
    p=c.products[0]
    assert (p.exchange,p.product,p.latest_complete_trade_date,p.spot_status)==("SHFE","CU",None,"UNAVAILABLE")
    assert sum(m.admitted_contracts for d in p.days for m in d.months)==198
    assert sum(m.discovered_contracts-m.admitted_contracts for d in p.days for m in d.months)==482
    assert all(d.status=="ARCHIVE_ONLY" for d in p.days)
    a=provider.chain("SHFE","CU","2022-09-07","SHFE.cu2211",None,None)
    b=provider.chain("SHFE","CU","2022-09-07","SHFE.cu2212",a.snapshot.snapshot_id,None)
    assert len(a.rows)==len(b.rows)==33
    assert a.snapshot==b.snapshot and a.snapshot.source_pin==CATALOG_PIN
    assert a.snapshot.provider=="APPROVED_ARCHIVE" and not a.snapshot.production_valuation_ready
    assert a.underlying.market_price==60580 and a.underlying.multiplier==5
    s=provider.load_snapshot(a.snapshot.snapshot_id)
    assert s is not None and len(s.options)==132 and len(s.underlyings)==2
    assert sum(q.model_eligibility.reason=="IV_UNAVAILABLE" for q in s.options)>0
    for q in s.options:
        assert q.expiry_clock=="APPROVED_NOMINAL" and q.payoff_eligibility.status=="ELIGIBLE"


def test_composed_asgi_actual_intrinsic_and_mixed_payoff(provider):
    app=create_strategy_payoff_app(provider,bearer_token=TOKEN,market_data=provider)

    async def run():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url="http://testserver") as client:
            assert (await client.get(PATH+"/coverage")).status_code==401
            c=await client.get(PATH+"/coverage",headers=HEADERS)
            assert c.status_code==200 and c.headers["cache-control"]=="no-store"
            params={"exchange":"SHFE","product":"CU","trade_date":"2022-09-07","underlying_id":"SHFE.cu2211"}
            chain=(await client.get(PATH+"/chain",params=params,headers=HEADERS)).json()["chain"]
            q=next(r["put"] for r in chain["rows"] if r["strike"]==79000)
            snap=chain["snapshot"]
            assert q["market_price"]==18420 and q["model_eligibility"]["reason"]=="IV_UNAVAILABLE"
            entry={"entry_trade_date":snap["trade_date"],"entry_snapshot_id":snap["snapshot_id"],"entry_origin":"ADMITTED_MARK"}
            req={"schema_version":"4.0","portfolio_revision":1,"snapshot_id":snap["snapshot_id"],
                 "options":[{**entry,"contract_id":q["contract_id"],"quantity":1,"entry_price":18420}],
                 "underlyings":[],"reference_underlying_id":"SHFE.cu2211","curve_price_returns":[-.1,0,.1],
                 "global_price_shock":0.,"price_shocks_by_underlying":{}}
            pay=(await client.post(PATH+"/payoffs",json=req,headers=HEADERS)).json()["payoff"]
            assert pay["terminal"]["kind"]=="TERMINAL"
            pts={p["price_return"]:p["pnl"] for p in pay["terminal"]["points"] if p["status"]=="OK"}
            assert pts[0]==0 and pts[-.1]==30290 and pts[.1]==-30290
            assert pay["accounting"]["net_premium"]==92100
            assert pay["risk"]["scope"]=="EXACT_DOMAIN" and pay["risk"]["max_loss"]["value"]==92100
            req["underlyings"]=[{**entry,"underlying_id":"SHFE.cu2211","quantity":1,"entry_price":60580,"quantity_unit":"CONTRACTS"}]
            pay=(await client.post(PATH+"/payoffs",json=req,headers=HEADERS)).json()["payoff"]
            assert next(p["pnl"] for p in pay["terminal"]["points"] if p["price_return"]==.1)==0
            for bad in ({**params,"underlying_id":"SHFE.cu2210"},{**params,"trade_date":"2022-09-09"},{**params,"snapshot_id":"f"*64}):
                result=(await client.get(PATH+"/chain",params=bad,headers=HEADERS)).json()
                assert result["status"]=="UNAVAILABLE" and result["chain"] is None
            assert (await client.get(PATH+"/coverage?exchange=SHFE",headers=HEADERS)).status_code==422
            assert (await client.get(PATH+"/chain",params={**params,"sql":"SELECT 1"},headers=HEADERS)).status_code==422
    asyncio.run(run())
