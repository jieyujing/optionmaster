"""D12 T03 independent golden values, source/entry boundaries and real ASGI seam."""
import asyncio
from copy import deepcopy
from hashlib import sha256
import json
from pathlib import Path

import httpx
import pytest

from vnpy_optionmaster.research_strategy_api_dto import (
    OptionQuoteDTO, SnapshotDTO, StrategyPayoffRequestDTO, UnderlyingQuoteDTO,
)
from vnpy_optionmaster.research_strategy_payoff import StrategyQuoteSnapshot, resolve_strategy, evaluate_payoff
from vnpy_optionmaster.research_strategy_api import create_strategy_payoff_app

ROOT = Path(__file__).resolve().parents[1]
FIX = json.loads((ROOT / "docs/evidence/m1-d12/contract-fixtures.json").read_text())
TOKEN = "fixture-only-d12-payoff-token-not-a-secret"
URL = "/api/v4/research/strategy/payoffs"
HEADERS = {"Authorization": "Bearer " + TOKEN}
UID = "EX.TEST.F1"


def source(*, domain=None, underlying_patch=None, extra=None, snapshot_patch=None):
    data = FIX["chain_response"]["chain"]
    underlying = deepcopy(data["underlying"])
    if domain is not None:
        underlying["domain"] = domain
    underlying.update(underlying_patch or {})
    opt = data["rows"][0]["call"]
    options = []
    for strike in (80, 90, 100, 110, 120):
        for right in ("CALL", "PUT"):
            options.append(OptionQuoteDTO.model_validate({
                **opt, "strike": strike, "option_type": right,
                "contract_id": right[0] + str(strike),
                "market_price": 5,
            }))
    if extra:
        options.extend(extra)
    return StrategyQuoteSnapshot(
        SnapshotDTO.model_validate({**data["snapshot"], **(snapshot_patch or {})}),
        tuple(options), (UnderlyingQuoteDTO.model_validate(underlying),),
    )


def request(snapshot, positions, *, underlying=None, axis=(-.2, 0, .2), **patch):
    meta = snapshot.metadata
    entry = {"entry_trade_date": meta.trade_date, "entry_snapshot_id": meta.snapshot_id,
             "entry_origin": "USER_EDITED"}
    return StrategyPayoffRequestDTO.model_validate({
        "schema_version": "4.0", "portfolio_revision": 1, "snapshot_id": meta.snapshot_id,
        "options": [{**entry, "contract_id": name, "quantity": qty, "entry_price": price}
                    for name, qty, price in positions],
        "underlyings": [] if underlying is None else [
            {**entry, "underlying_id": UID, "quantity": underlying[0], "entry_price": underlying[1],
             "quantity_unit": underlying[2] if len(underlying) > 2 else "CONTRACTS"},
        ],
        "reference_underlying_id": UID, "curve_price_returns": list(axis),
        "global_price_shock": 0, "price_shocks_by_underlying": {}, **patch,
    })


def calc(snapshot, body, entry_snapshots=None):
    return evaluate_payoff(resolve_strategy(body, snapshot, entry_snapshots or {snapshot.metadata.snapshot_id: snapshot}))


@pytest.mark.parametrize("positions,values,profit,loss,roots", [
    ([("C100", 1, 5)], (-50, -50, 150), None, 50, [.05]),
    ([("P100", 1, 5)], (150, -50, -50), 950, 50, [-.05]),
    ([("C90", 1, 12), ("C110", -1, 3)], (-90, 10, 110), 110, 90, [-.01]),
    ([("P110", 1, 12), ("P90", -1, 3)], (110, 10, -90), 110, 90, [.01]),
    ([("C100", 1, 5), ("P100", 1, 5)], (100, -100, 100), None, 100, [-.1, .1]),
    ([("C110", 1, 3), ("P90", 1, 3)], (40, -60, 40), None, 60, [-.16, .16]),
    ([("C90", 1, 12), ("C100", -2, 5), ("C110", 1, 3)], (-50, 50, -50), 50, 50, [-.05, .05]),
    ([("C80", 1, 22), ("C90", -1, 12), ("C110", -1, 3), ("C120", 1, 1)], (-80, 20, -80), 20, 80, [-.12, .12]),
    ([("C90", 1, 12), ("C100", -2, 5)], (-20, 80, -120), 80, None, [-.08, .08]),
    ([("C90", -1, 12), ("C100", 2, 5)], (20, -80, 120), None, 80, [-.08, .08]),
])
def test_golden_strategies(positions, values, profit, loss, roots):
    snap = source()
    result = calc(snap, request(snap, positions))
    assert result.terminal.kind == "TERMINAL" and result.terminal.status == "OK"
    points = {round(p.price_return, 10): p for p in result.terminal.points}
    assert [points[x].pnl for x in (-.2, 0, .2)] == pytest.approx(values, abs=1e-8)
    assert result.risk.scope == "EXACT_DOMAIN" and result.risk.roots_exhaustive
    assert result.risk.breakevens == pytest.approx(roots, abs=1e-10)
    for metric, expected in [(result.risk.max_profit, profit), (result.risk.max_loss, loss)]:
        assert metric.kind == ("UNBOUNDED" if expected is None else "FINITE")
        assert metric.value == expected
    assert all(p.pnl == pytest.approx(sum(c.pnl for c in p.contributions), abs=1e-8)
               for p in result.terminal.points)
    assert result.legs[0].model_eligibility.status == "UNAVAILABLE"  # IV isn't needed.


def test_mixed_protective_put_and_spot_units():
    snap = source()
    result = calc(snap, request(snap, [("P100", 1, 5)], underlying=(1, 100)))
    points = {p.price_return: p.pnl for p in result.terminal.points}
    assert [points[x] for x in (-.2, 0, .2)] == [-50, -50, 150]
    assert result.accounting.net_premium == 50 and result.accounting.spot_entry_cash == 0
    spot = source(underlying_patch={"kind":"SPOT", "delivery_month":None,
                                   "quantity_unit":"NATIVE_SPOT_UNITS", "multiplier":1})
    result = calc(spot, request(spot, [("C100", -1, 5)], underlying=(10, 100, "NATIVE_SPOT_UNITS")))
    points = {p.price_return: p.pnl for p in result.terminal.points}
    assert [points[x] for x in (-.2, 0, .2)] == [-150, 50, 50]
    assert result.accounting.net_premium == -50 and result.accounting.spot_entry_cash == 1000


def test_distinct_underlying_override_is_additive_and_metrics_conditional():
    base = source()
    second = base.underlyings[0].model_copy(update={
        "underlying_id":"EX.TEST.F2", "delivery_month":"TEST2", "market_price":110,
    })
    put = next(q for q in base.options if q.contract_id == "P110").model_copy(update={
        "contract_id":"F2P110", "underlying_id":second.underlying_id, "expiry_id":"EX.TEST.EXP2",
    })
    snap = StrategyQuoteSnapshot(base.metadata, (*base.options, put), (*base.underlyings, second))
    body = request(snap, [("C100",1,5), ("F2P110",1,5)], global_price_shock=.1,
                   price_shocks_by_underlying={"EX.TEST.F2":-.1})
    result = calc(snap, body)
    origin = next(p for p in result.terminal.points if p.price_return == 0)
    assert origin.underlying_prices == {UID:110, "EX.TEST.F2":99}
    assert origin.pnl == 110
    at_twenty = next(p for p in result.terminal.points if p.price_return == .2)
    assert at_twenty.underlying_prices == {UID:130, "EX.TEST.F2":121}
    assert result.risk.scope == "CONDITIONAL_COMMON_RETURN"


def test_cross_date_entry_cost_preserved_and_version_drift_rejected():
    old = source()
    new_id = "e"*64
    current_meta = SnapshotDTO.model_validate({
        **old.metadata.model_dump(), "snapshot_id":new_id, "trade_date":"2022-09-08",
        "reference_at":"2022-09-08T15:00:00+08:00",
    })
    current = StrategyQuoteSnapshot(current_meta, tuple(q.model_copy(update={"market_price":7}) for q in old.options),
        tuple(u.model_copy(update={"market_price":110}) for u in old.underlyings))
    body = request(old, [("C100",1,5)]).model_dump()
    body["snapshot_id"] = new_id
    body["options"][0]["entry_origin"] = "ADMITTED_MARK"
    req = StrategyPayoffRequestDTO.model_validate(body)
    result = calc(current, req, {old.metadata.snapshot_id:old, new_id:current})
    assert result.accounting.net_premium == 50
    assert result.legs[0].market_price == 7
    assert next(p.pnl for p in result.terminal.points if p.price_return == 0) == 50
    body["options"][0]["entry_price"] = 6
    body["options"][0]["entry_origin"] = "USER_EDITED"
    edited = calc(current, StrategyPayoffRequestDTO.model_validate(body), {old.metadata.snapshot_id:old, new_id:current})
    assert edited.accounting.net_premium == 60
    assert edited.legs[0].market_price == 7
    assert edited.legs[0].model_eligibility == result.legs[0].model_eligibility
    assert next(p.pnl for p in edited.terminal.points if p.price_return == 0) == 40
    changed = StrategyQuoteSnapshot(current_meta, tuple(q.model_copy(update={"terms_version":"new"}) for q in current.options),
                                    current.underlyings)
    from vnpy_optionmaster.research_strategy_payoff import StrategyUnavailable
    with pytest.raises(StrategyUnavailable, match="TERMS_UNAVAILABLE"):
        calc(changed, req, {old.metadata.snapshot_id:old, new_id:changed})


def test_zero_intervals_offscreen_roots_and_exact_zero():
    snap = source()
    result = calc(snap, request(snap, [("C100", 1, 0)], axis=(0, .01)))
    assert result.risk.breakevens == []
    interval = result.risk.zero_intervals[0]
    assert (interval.start, interval.end, interval.start_inclusive, interval.end_inclusive) == (-1, 0, True, True)
    assert result.risk.max_loss.value == 0
    result = calc(snap, request(snap, [("C100", 1, 5)], axis=(0, .01)))
    assert .05 in [point.price_return for point in result.terminal.points]  # off-screen root
    result = calc(snap, request(snap, [("C100", 1, 5), ("P100", -1, 5)], underlying=(-1, 100)))
    assert result.risk.breakevens == []
    assert len(result.risk.zero_intervals) == 1
    interval = result.risk.zero_intervals[0]
    assert (interval.start, interval.end) == (-1, None)
    assert result.risk.max_profit.value == result.risk.max_loss.value == 0


def test_verified_negative_domain_and_open_endpoint():
    snap = source(domain={"status":"VERIFIED", "min_price":0, "max_price":200,
                          "min_inclusive":False, "max_inclusive":True})
    result = calc(snap, request(snap, [("P100", 1, 100)], axis=(-1, 0, .2)))
    assert result.risk.breakevens == []  # F=0 excluded; root not attained
    assert result.risk.max_profit.value == 0
    assert result.terminal.status == "PARTIAL"
    snap = source(domain={"status":"VERIFIED", "min_price":-200, "max_price":0,
                          "min_inclusive":True, "max_inclusive":True},
                  underlying_patch={"market_price":-100})
    result = calc(snap, request(snap, [("P100", 1, 200)]))
    assert result.risk.breakevens == [0]
    assert result.risk.max_profit.value == result.risk.max_loss.value == 1000


def test_unrestricted_price_domain_two_tail_risk_and_iron_condor():
    snap = source(domain={"status":"VERIFIED", "min_price":None, "max_price":None,
                          "min_inclusive":False, "max_inclusive":False})
    result = calc(snap, request(snap, [("C100",1,5), ("P100",-1,5)]))
    assert result.risk.max_profit.kind == result.risk.max_loss.kind == "UNBOUNDED"
    assert result.risk.breakevens == [0]
    result = calc(snap, request(snap, [("C100",-1,5), ("P100",-1,5)]))
    assert result.risk.max_profit.value == 100 and result.risk.max_loss.kind == "UNBOUNDED"
    assert result.risk.breakevens == [-.1,.1]
    # Buy P80/C120 for 1 each, sell P90/C110 for 3 each: 4 credit,
    # maximum loss 6 on either tail and roots at 86 and 114.
    result = calc(snap, request(snap, [("P80",1,1), ("P90",-1,3), ("C110",-1,3), ("C120",1,1)]))
    assert result.accounting.net_premium == -40
    assert result.risk.max_profit.value == 40 and result.risk.max_loss.value == 60
    assert result.risk.breakevens == [-.14,.14]


def test_same_date_different_verified_cutoffs_are_conditional():
    base = source()
    options = tuple(
        q.model_copy(update={"expiry_clock":"VERIFIED_CUTOFF", "expiry_at":
                             "2022-10-01T15:00:00+08:00" if q.option_type == "CALL" else "2022-10-01T16:00:00+08:00"})
        for q in base.options
    )
    snap = StrategyQuoteSnapshot(base.metadata, options, base.underlyings)
    result = calc(snap, request(snap, [("C100",1,5), ("P100",1,5)]))
    assert result.terminal.kind == "FIRST_EXPIRY_CONDITIONAL"
    assert result.terminal.evaluation_at == "2022-10-01T15:00:00+08:00"
    assert result.terminal.status == "UNAVAILABLE"


def test_unknown_domain_and_cross_expiry_accounting_remain_independent():
    snap = source(domain={"status":"UNAVAILABLE", "min_price":None, "max_price":None,
                          "min_inclusive":False, "max_inclusive":False})
    result = calc(snap, request(snap, [("C100", 1, 5)]))
    assert result.accounting.net_premium == 50
    assert result.risk.scope == "UNAVAILABLE" and result.terminal.reason == "DOMAIN_UNAVAILABLE"
    base = source()
    later = base.options[0].model_copy(update={"contract_id":"FAR", "expiry_id":"FAR.EXP",
                                             "expiry_date":"2022-11-01"})
    snap = source(extra=[later])
    result = calc(snap, request(snap, [("C100", 1, 5), ("FAR", -1, 4)]))
    assert result.accounting.net_premium == 10
    assert result.terminal.kind == "FIRST_EXPIRY_CONDITIONAL"
    assert result.terminal.status == "UNAVAILABLE" and result.risk.scope == "UNAVAILABLE"


class FixtureProvider:
    def __init__(self, snapshots):
        self.snapshots = snapshots
        self.calls = []

    def load_snapshot(self, snapshot_id):
        self.calls.append(snapshot_id)
        return self.snapshots.get(snapshot_id)


def send(app, *, body=None, content=None, headers=HEADERS, path=URL):
    async def run():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://testserver") as client:
            if content is not None:
                return await client.post(path, content=content, headers=headers)
            return await client.post(path, json=body, headers=headers)
    return asyncio.run(run())


def test_actual_asgi_payoff_auth_and_exact_hash():
    snap = source()
    provider = FixtureProvider({snap.metadata.snapshot_id: snap})
    app = create_strategy_payoff_app(provider, bearer_token=TOKEN, allow_synthetic_fixture=True)
    body = request(snap, [("C100", 1, 5)]).model_dump(mode="json")
    assert send(app, body=body, headers={}).status_code == 401
    assert provider.calls == []
    response = send(app, body=body)
    assert response.status_code == 200 and response.headers["cache-control"] == "no-store"
    result = response.json()
    assert result["status"] == "OK" and result["payoff"]["risk"]["max_loss"]["value"] == 50
    assert result["input_hash"] == sha256(json.dumps(body,sort_keys=True,separators=(",",":"),allow_nan=False).encode()).hexdigest()
    assert provider.calls == [snap.metadata.snapshot_id]  # exactly once, entry shares snapshot
    assert send(app, body={**body,"source_price":100}).status_code == 422
    assert send(app, content=b'{"x":1,"x":2}', headers={**HEADERS,"Content-Type":"application/json"}).status_code == 422
    assert send(app, content=b"x"*16385, headers={**HEADERS,"Content-Type":"application/json"}).status_code == 413
    assert send(app, body=body, path=URL+"?extra=1").status_code == 422
    no_fixture = create_strategy_payoff_app(provider, bearer_token=TOKEN)
    assert send(no_fixture, body=body).json()["status"] == "UNAVAILABLE"


def test_missing_leg_stale_snapshot_and_entry_not_self_attested():
    snap = source()
    provider = FixtureProvider({snap.metadata.snapshot_id: snap})
    app = create_strategy_payoff_app(provider, bearer_token=TOKEN, allow_synthetic_fixture=True)
    body = request(snap, [("C100", 1, 5)]).model_dump(mode="json")
    for patch, expected in (
        ({"contract_id":"MISSING"}, "INSTRUMENT_UNAVAILABLE"),
        ({"entry_snapshot_id":"f"*64}, "ENTRY_UNAVAILABLE"),
        ({"entry_trade_date":"2022-09-06"}, "ENTRY_DATE_INVALID"),
        ({"entry_origin":"ADMITTED_MARK","entry_price":6}, "ENTRY_UNAVAILABLE"),
    ):
        response = send(app, body={**body, "options":[{**body["options"][0], **patch}]}).json()
        assert response["status"] == "UNAVAILABLE" and response["reason"] == expected
        assert response["payoff"] is None
    stale = send(app, body={**body,"snapshot_id":"e"*64}).json()
    assert stale["reason"] == "SNAPSHOT_STALE" and stale["payoff"] is None
