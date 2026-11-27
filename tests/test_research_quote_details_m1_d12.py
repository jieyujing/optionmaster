"""Offline Qt display contract, source separation and unchanged v4 payoff."""
import asyncio
from dataclasses import replace

import httpx
import pytest
from pydantic import ValidationError

from vnpy_optionmaster.pricing import binomial_tree
from vnpy_optionmaster.research_quote_details import QuoteFieldDTO, project_quote_details
from vnpy_optionmaster.research_strategy_api import create_strategy_payoff_app
from vnpy_optionmaster.research_strategy_payoff import StrategyUnavailable
from tests.test_research_strategy_payoff_m1_d12 import HEADERS, TOKEN, UID
from tests.test_research_strategy_valuation_m1_d12 import fixture, REF, NEAR, RATE, SIGMA, years

BOOK_DAILY = ("open_interest", "volume", "bid_impv", "bid_volume", "bid_price",
              "ask_price", "ask_volume", "ask_impv")
GREEKS = ("theo_vega", "theo_theta", "theo_gamma", "theo_delta")


def test_theoretical_units_and_no_fabricated_orderbook():
    snapshot = fixture()
    detail = project_quote_details(snapshot, UID)
    q = detail.quotes[0]
    mult = snapshot.options[0].multiplier
    _, delta, gamma, theta, vega = binomial_tree.calculate_greeks(100., 100., RATE, years(REF, NEAR), SIGMA, 1, 128)
    assert q.contract_id == "NEAR"
    assert detail.snapshot == snapshot.metadata
    assert detail.underlying_id == UID
    for name, value in zip(GREEKS, (vega*mult/100, theta*mult/240, gamma*mult, delta*mult), strict=True):
        field = getattr(q, name)
        assert field.value == pytest.approx(value, abs=1e-6)
        assert field.basis == "SETTLEMENT_MODEL" and field.reason is None
    for name in BOOK_DAILY:
        field = getattr(q, name)
        assert field.value is None and field.reason == "SOURCE_INCOMPLETE"
        assert field.basis == "UNAVAILABLE"


@pytest.mark.parametrize("kwargs", [{"missing_rate": True}, {"date_only": True}, {"invalid_iv": True}])
def test_unavailable_model_preserves_unknown(kwargs):
    detail = project_quote_details(fixture(**kwargs), UID).quotes[0]
    assert all(getattr(detail, name).value is None and getattr(detail, name).reason for name in GREEKS)


def test_expired_duplicate_rate_and_foreign_month_fail_closed():
    snap = fixture()
    expired = snap.options[0].model_copy(update={"expiry_at": REF, "expiry_date": REF[:10]})
    q = project_quote_details(replace(snap, options=(expired,)), UID).quotes[0]
    assert q.theo_delta.reason == "EXPIRED_SCENARIO" and q.theo_delta.value is None
    q = project_quote_details(replace(snap, rates=snap.rates*2), UID).quotes[0]
    assert q.theo_delta.reason == "RATE_UNAVAILABLE"
    with pytest.raises(StrategyUnavailable):
        project_quote_details(snap, "EX.TEST.OTHER")


def test_strict_availability_accepts_verified_zero_not_missing_zero():
    assert QuoteFieldDTO(value=0., basis="ADMITTED_DAILY", reason=None).value == 0.
    with pytest.raises(ValidationError):
        QuoteFieldDTO(value=0., basis="UNAVAILABLE", reason="SOURCE_INCOMPLETE")
    with pytest.raises(ValidationError):
        QuoteFieldDTO(value=None, basis="ADMITTED_BOOK", reason=None)


def test_versioned_endpoint_auth_and_snapshot_guard_legacy_schema():
    snapshot = fixture()
    class Provider:
        def load_snapshot(self, snapshot_id):
            return snapshot if snapshot_id == snapshot.metadata.snapshot_id else None
    provider = Provider()
    path = "/api/v4/research/strategy/quote-details-v1"
    params = dict(exchange=snapshot.metadata.exchange, product=snapshot.metadata.product,
                  trade_date=snapshot.metadata.trade_date, underlying_id=UID,
                  snapshot_id=snapshot.metadata.snapshot_id)

    async def run():
        for admitted in (False, True):
            app = create_strategy_payoff_app(provider, bearer_token=TOKEN, market_data=provider,
                                             allow_synthetic_fixture=admitted)
            assert path not in app.openapi()["paths"]
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://testserver") as client:
                assert (await client.get(path, params=params)).status_code == 401
                res = await client.get(path, params=params, headers=HEADERS)
                if admitted:
                    assert res.status_code == 200
                    assert res.json()["schema_version"] == "1.0"
                    assert res.json()["snapshot"]["provider"] == "SYNTHETIC_FIXTURE"
                    assert res.json()["quotes"][0]["bid_price"]["value"] is None
                    assert res.headers["cache-control"] == "no-store"
                else:
                    assert res.status_code == 422 and res.json()["reason"] == "CANDIDATE_ONLY"
                for bad in ({**params, "snapshot_id": "f"*64},
                            {**params, "trade_date": "2022-09-08"},
                            {**params, "cursor": "a"*64},
                            {**params, "underlying_id": "EX.TEST.OTHER"},
                            {k:v for k,v in params.items() if k != "snapshot_id"}):
                    assert (await client.get(path, params=bad, headers=HEADERS)).status_code == 422
    asyncio.run(run())
