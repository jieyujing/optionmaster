"""T05 independent scalar/analytic references and capability failure boundaries."""
import asyncio
from datetime import datetime
from math import isfinite
from threading import Event

import httpx

import pytest

from vnpy_optionmaster.pricing import binomial_tree
from vnpy_optionmaster.research_strategy_api_dto import (
    CapabilityDTO, StrategyRateEvidenceDTO, StrategyValuationRequestDTO,
)
from vnpy_optionmaster.research_strategy_payoff import StrategyQuoteSnapshot, resolve_strategy
from vnpy_optionmaster.research_strategy_valuation import evaluate_strategy_valuation
from vnpy_optionmaster.research_strategy_api import create_strategy_payoff_app
from tests.test_research_strategy_payoff_m1_d12 import FixtureProvider, HEADERS, TOKEN, UID, request, send, source

REF = "2022-09-07T15:00:00+08:00"
NEAR = "2022-10-01T15:00:00+08:00"
FAR = "2022-11-01T15:00:00+08:00"
SCENARIO = "2022-09-17T15:00:00+08:00"
RATE = .03
SIGMA = .25
MODEL = CapabilityDTO(status="NOT_EVALUATED", reason="NOT_EVALUATED")


def years(a, b):
    return (datetime.fromisoformat(b)-datetime.fromisoformat(a)).total_seconds()/(365*86400)


def fixture(*, far=False, diagonal=False, date_only=False, missing_rate=False, invalid_iv=False):
    base = source()
    u = base.underlyings[0]
    q = base.options[0]
    near = q.model_copy(update={
        "contract_id": "NEAR", "strike": 100., "option_type": "CALL", "model": "AMERICAN_BINOMIAL",
        "exercise_style": "AMERICAN", "expiry_id": "E1", "expiry_date": NEAR[:10],
        "expiry_at": None if date_only else NEAR, "expiry_clock": "DATE_ONLY" if date_only else "VERIFIED_CUTOFF",
        "market_price": 0. if invalid_iv else float(binomial_tree.calculate_price(100.,100.,RATE,years(REF,NEAR),SIGMA,1,128)),
        "model_eligibility": MODEL, "implied_volatility": None,
    })
    options = [near]
    underlyings = [u]
    if far:
        second = u.model_copy(update={"underlying_id":"EX.TEST.F2","delivery_month":"TEST2","market_price":110.}) if diagonal else u
        if diagonal:
            underlyings.append(second)
        f = second.market_price
        options.append(near.model_copy(update={
            "contract_id":"FAR", "underlying_id": second.underlying_id, "strike":110. if diagonal else 100.,
            "expiry_id":"E2", "expiry_date":FAR[:10], "expiry_at":FAR, "expiry_clock":"VERIFIED_CUTOFF",
            "market_price":float(binomial_tree.calculate_price(f,110. if diagonal else 100.,RATE,years(REF,FAR),SIGMA,1,128))
        }))
    rates = () if missing_rate else tuple(StrategyRateEvidenceDTO(
        expiry_id=q.expiry_id, quote_date="2022-09-06", continuous_rate=RATE,
        policy_id="FIXTURE_DATED_RATE", source_pin=base.metadata.source_pin,
    ) for q in options)
    return StrategyQuoteSnapshot(base.metadata,tuple(options),tuple(underlyings),rates=rates)


def valuation_request(snap, positions, *, underlying=None, axis=(-.1,0,.1), scenario=SCENARIO, iv=0., overrides=None, **kwargs):
    raw=request(snap,positions,underlying=underlying,axis=axis,**kwargs).model_dump()
    return StrategyValuationRequestDTO.model_validate({**raw,"scenario_at":scenario,"global_iv_shock":iv,"iv_shocks_by_expiry":overrides or {}})


def calc(snap, req):
    return evaluate_strategy_valuation(resolve_strategy(req,snap,{snap.metadata.snapshot_id:snap}),snap.rates)


def point(curve,x=0):
    return next(p for p in curve.points if p.price_return==x)


def test_current_scenario_terminal_scalar_parity_and_linear_delta():
    snap=fixture()
    req=valuation_request(snap,[("NEAR",2,3)],underlying=(-1,100),iv=.05,global_price_shock=.1)
    result=calc(snap,req)
    multiplier=snap.options[0].multiplier
    u_multiplier=snap.underlyings[0].multiplier
    base=binomial_tree.calculate_greeks(100.,100.,RATE,years(REF,NEAR),SIGMA,1,128)
    scenario=binomial_tree.calculate_greeks(110.,100.,RATE,years(SCENARIO,NEAR),SIGMA+.05,1,128)
    assert point(result.current).pnl==pytest.approx(2*multiplier*(base[0]-3),abs=1e-7)
    assert point(result.scenario).pnl==pytest.approx(2*multiplier*(scenario[0]-3)-10*u_multiplier,abs=1e-6)
    assert point(result.horizon).pnl==pytest.approx(2*multiplier*7-10*u_multiplier,abs=1e-8)
    assert result.current_greeks[0].delta==pytest.approx(2*multiplier*base[1]-u_multiplier,abs=1e-6)
    assert result.scenario_greeks[0].vega_per_unit==pytest.approx(2*multiplier*scenario[4],abs=1e-5)
    assert result.legs[0].implied_volatility==pytest.approx(SIGMA,abs=1e-6)
    assert result.tree_steps==128 and result.iv_policy=="STICKY_STRIKE"
    assert result.horizon_risk.roots_exhaustive
    edited=calc(snap,valuation_request(snap,[("NEAR",-3,9)],iv=.05))
    assert edited.legs[0].implied_volatility==result.legs[0].implied_volatility


@pytest.mark.parametrize("diagonal",[False,True])
def test_earliest_expiry_far_time_value_and_independent_shocks(diagonal):
    snap=fixture(far=True,diagonal=diagonal)
    shock={"EX.TEST.F2":-.05} if diagonal else {}
    req=valuation_request(snap,[("NEAR",-1,2),("FAR",2,4)],iv=.01,overrides={"E2":.05},
                          global_price_shock=.1,price_shocks_by_underlying=shock)
    v=calc(snap,req)
    far_f=104.5 if diagonal else 110.
    strike=110. if diagonal else 100.
    near_pnl=-snap.options[0].multiplier*(10-2)
    far_value=binomial_tree.calculate_price(far_f,strike,RATE,years(NEAR,FAR),SIGMA+.05,1,128)
    assert v.horizon.kind=="FIRST_EXPIRY_CONDITIONAL"
    assert v.horizon.evaluation_at==NEAR
    assert point(v.horizon).pnl==pytest.approx(near_pnl+2*snap.options[1].multiplier*(far_value-4),abs=1e-6)
    assert v.horizon_risk.scope=="CONDITIONAL_RANGE"
    assert not v.horizon_risk.roots_exhaustive
    assert len(v.current_greeks)==(2 if diagonal else 1)
    assert point(v.current).underlying_prices[UID]==100.
    assert point(v.scenario).underlying_prices[UID]==110.


def test_expiry_and_invalid_model_capabilities_fail_independently():
    snap=fixture(invalid_iv=True)
    v=calc(snap,valuation_request(snap,[("NEAR",1,3)]))
    assert v.current.status==v.scenario.status=="UNAVAILABLE"
    assert v.current.reason=="IV_UNAVAILABLE"
    assert point(v.horizon).pnl is not None and v.horizon.status=="OK"
    assert v.legs[0].implied_volatility is None
    snap=fixture(missing_rate=True)
    v=calc(snap,valuation_request(snap,[("NEAR",1,3)]))
    assert v.current.reason=="RATE_UNAVAILABLE" and v.horizon.status=="OK"
    snap=fixture(far=True,date_only=True)
    v=calc(snap,valuation_request(snap,[("NEAR",1,3),("FAR",1,4)]))
    assert v.horizon.reason=="HORIZON_UNAVAILABLE"
    assert v.current.reason=="HORIZON_UNAVAILABLE"
    snap=fixture(far=True)
    v=calc(snap,valuation_request(snap,[("NEAR",1,3),("FAR",1,4)],scenario=NEAR))
    assert point(v.scenario).pnl==point(v.horizon).pnl
    assert all(g.status=="UNAVAILABLE" for g in v.scenario_greeks)
    v=calc(snap,valuation_request(snap,[("NEAR",1,3),("FAR",1,4)],scenario="2022-10-02T15:00:00+08:00"))
    assert v.scenario.reason=="EXPIRED_SCENARIO" and v.current.status=="OK"


def test_shock_outside_model_domain_does_not_replace_with_zero():
    snap=fixture()
    v=calc(snap,valuation_request(snap,[("NEAR",1,3)],axis=(-1.,0.,.2),iv=-.5))
    assert v.current.status=="PARTIAL" and point(v.current,-1.).pnl is None
    assert v.scenario.status=="UNAVAILABLE"
    assert v.horizon.status=="OK"
    assert v.scenario_greeks[0].delta is None
    assert all(p.pnl is None or isfinite(p.pnl) for p in v.current.points)


def test_real_asgi_model_hash_bearer_legacy_guard_and_provider_once():
    snap=fixture(far=True)
    provider=FixtureProvider({snap.metadata.snapshot_id:snap})
    app=create_strategy_payoff_app(provider,bearer_token=TOKEN,allow_synthetic_fixture=True)
    url="/api/v4/research/strategy/valuations"
    raw=valuation_request(snap,[("NEAR",1,3),("FAR",-1,4)]).model_dump(mode="json")
    assert send(app,body=raw,path=url,headers={}).status_code==401
    response=send(app,body=raw,path=url,headers=HEADERS)
    assert response.status_code==200
    body=response.json()
    assert body["valuation"]["horizon"]["kind"]=="FIRST_EXPIRY_CONDITIONAL"
    assert body["portfolio_revision"]==raw["portfolio_revision"] and body["input_hash"]
    assert provider.calls==[snap.metadata.snapshot_id]
    bad={**raw,"iv_shocks_by_expiry":{"UNRELATED":.2}}
    assert send(app,body=bad,path=url).json()["reason"]=="INVALID_SCENARIO"
    assert send(app,body={**raw,"tree_steps":256},path=url).status_code==422


def test_model_lane_admission_before_dispatch_and_independent_payoff():
    snap = fixture()
    entered, release = Event(), Event()

    class BlockingProvider(FixtureProvider):
        def load_snapshot(self, snapshot_id):
            if not self.calls:
                self.calls.append(snapshot_id)
                entered.set()
                if not release.wait(5):
                    raise RuntimeError("fixture deadline")
                return self.snapshots.get(snapshot_id)
            return super().load_snapshot(snapshot_id)

    provider = BlockingProvider({snap.metadata.snapshot_id: snap})
    app = create_strategy_payoff_app(provider, bearer_token=TOKEN, allow_synthetic_fixture=True)
    raw = valuation_request(snap, [("NEAR",1,3)]).model_dump(mode="json")
    payoff = request(snap, [("NEAR",1,3)]).model_dump(mode="json")
    endpoint = "/api/v4/research/strategy/valuations"

    async def run():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url="http://testserver") as client:
            first = asyncio.create_task(client.post(endpoint,json=raw,headers=HEADERS))
            try:
                assert await asyncio.to_thread(entered.wait, 2)
                second = await client.post(endpoint,json=raw,headers=HEADERS)
                assert second.status_code == 429 and second.json()["reason"] == "BUSY"
                assert len(provider.calls) == 1
                lightweight = await client.post("/api/v4/research/strategy/payoffs",json=payoff,headers=HEADERS)
                assert lightweight.status_code == 200 and lightweight.json()["payoff"] is not None
                assert len(provider.calls) == 2
                first.cancel()
                with pytest.raises(asyncio.CancelledError):
                    await first
                still_busy = await client.post(endpoint,json=raw,headers=HEADERS)
                assert still_busy.status_code == 429 and len(provider.calls) == 2
            finally:
                release.set()
                if not first.done():
                    await first
            # Once the real shielded worker completes, admission opens again.
            for _ in range(100):
                await asyncio.sleep(.02)
                final = await client.post(endpoint,json=raw,headers=HEADERS)
                if final.status_code == 200:
                    assert final.json()["valuation"] is not None
                    return
                assert final.status_code == 429
            pytest.fail("model lane did not release")
    asyncio.run(run())
