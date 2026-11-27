"""D12 T01: contract-only tests; synthetic fixtures never authorize market data."""
import hashlib
import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from vnpy_optionmaster.research_strategy_api_dto import (
    StrategyPayoffRequestDTO, StrategyValuationRequestDTO, StrategyEnvelopeDTO,
    strategy_openapi,
)

ROOT = Path(__file__).resolve().parents[1]
FIX = ROOT / "docs/evidence/m1-d12/contract-fixtures.json"


def fixture():
    return json.loads(FIX.read_text())


def test_one_option_and_mixed_entry_contract():
    data = fixture()
    request = StrategyPayoffRequestDTO.model_validate(data["payoff_request"])
    assert len(request.options) == 1 and request.options[0].quantity == 1
    assert request.options[0].entry_price == 5
    assert request.underlyings[0].quantity == -1
    model = StrategyValuationRequestDTO.model_validate(data["valuation_request"])
    assert model.scenario_at.endswith("+08:00")
    assert model.iv_shocks_by_expiry == {"EX.TEST.EXP": 0.02}


@pytest.mark.parametrize("patch", [
    {"schema_version": "3.0"}, {"portfolio_revision": True},
    {"portfolio_revision": 9007199254740992}, {"snapshot_id": "untrusted"},
    {"options": []}, {"curve_price_returns": [0, 0]},
    {"curve_price_returns": [-0.1, 0.1]}, {"curve_price_returns": [0.1, 0]},
    {"curve_price_returns": [0, 0.1, float("nan")]},
    {"source_price": 100}, {"model": "anything"}, {"rate": 0.03},
    {"price_shocks_by_underlying": {"EX.TEST": 1.01}}, {"global_price_shock": 1.01},
])
def test_strict_payoff_request(patch):
    with pytest.raises(ValidationError):
        StrategyPayoffRequestDTO.model_validate({**fixture()["payoff_request"], **patch})


@pytest.mark.parametrize("patch", [
    {"quantity": 0}, {"quantity": 1001}, {"quantity": True},
    {"quantity": "1"}, {"entry_price": -1}, {"entry_price": float("inf")},
    {"entry_trade_date": "2022-02-30"}, {"multiplier": 10},
    {"entry_snapshot_id": "self-approved"}, {"entry_origin": "VERIFIED"},
])
def test_strict_option_leg(patch):
    data = fixture()["payoff_request"]
    data["options"][0].update(patch)
    with pytest.raises(ValidationError):
        StrategyPayoffRequestDTO.model_validate(data)


def test_duplicate_and_leg_caps():
    data = fixture()["payoff_request"]
    leg = data["options"][0]
    for positions in ([leg, leg], [{**leg, "contract_id": str(i)} for i in range(9)]):
        with pytest.raises(ValidationError):
            StrategyPayoffRequestDTO.model_validate({**data, "options": positions})
    underlying = data["underlyings"][0]
    with pytest.raises(ValidationError):
        StrategyPayoffRequestDTO.model_validate({**data, "underlyings": [underlying, underlying]})


def test_model_aware_clock_and_no_model_on_payoff():
    data = fixture()["valuation_request"]
    for patch in ({"scenario_at": "2022-09-07T15:00:00"}, {"scenario_at": "2022-09-07T15:00:00Z"},
                  {"iv_shocks_by_expiry": {"EXP": 1.1}}):
        with pytest.raises(ValidationError):
            StrategyValuationRequestDTO.model_validate({**data, **patch})
    with pytest.raises(ValidationError):
        StrategyPayoffRequestDTO.model_validate(data)


def test_independent_response_fixture_and_failure_cannot_be_zero():
    data = fixture()
    result = StrategyEnvelopeDTO.model_validate(data["payoff_response"])
    assert result.status == "OK"
    assert result.payoff is not None
    assert result.payoff.terminal.points[1].pnl == -50
    assert result.payoff.risk.max_profit.kind == "UNBOUNDED"
    bad = data["payoff_response"]
    point = bad["payoff"]["terminal"]["points"][1]
    point.update(status="UNAVAILABLE", reason="IV_UNAVAILABLE", pnl=0)
    with pytest.raises(ValidationError):
        StrategyEnvelopeDTO.model_validate(bad)


def test_chain_admission_and_partial_model_fixtures():
    data = fixture()
    coverage = StrategyEnvelopeDTO.model_validate(data["coverage_response"]).coverage
    assert coverage is not None and coverage.products[0].latest_complete_trade_date is None
    assert coverage.products[0].days[0].months[1].status == "DISCOVERY_ONLY"
    chain = StrategyEnvelopeDTO.model_validate(data["chain_response"]).chain
    assert chain is not None and chain.rows[0].put is None
    call = chain.rows[0].call
    assert call is not None and call.payoff_eligibility.status == "ELIGIBLE"
    assert call.implied_volatility is None and call.model_eligibility.status == "UNAVAILABLE"
    partial = StrategyEnvelopeDTO.model_validate(data["model_partial_response"])
    assert partial.status == "PARTIAL"
    assert partial.valuation is not None
    assert partial.valuation.current.status == "UNAVAILABLE"
    assert partial.valuation.horizon.status == "OK"


def test_missing_leg_and_stale_snapshot_never_validate():
    bad = fixture()["payoff_response"]
    bad["snapshot_id"] = "f" * 64
    with pytest.raises(ValidationError):
        StrategyEnvelopeDTO.model_validate(bad)
    bad = fixture()["payoff_response"]
    bad["payoff"]["legs"].append({**bad["payoff"]["legs"][0], "instrument_id": "EX.TEST.EXTRA"})
    with pytest.raises(ValidationError):
        StrategyEnvelopeDTO.model_validate(bad)


def test_actual_legacy_openapi_compatibility():
    from vnpy_optionmaster.research_api import create_research_app, load_reviewed_archive
    from vnpy_optionmaster.research_chain_api import create_chain_research_app, load_reviewed_chain
    from vnpy_optionmaster.research_catalog_api import create_catalog_research_app, load_reviewed_catalog

    evidence = ROOT / "docs/evidence"
    d04 = evidence / "m1-d04/shfe-20220907"
    d05 = evidence / "m1-d05/cu2211-20220907"
    d08 = evidence / "m1-d08/cu2211-20220907"
    token = "fixture-only-d12-schema-test-not-a-secret"
    for app, pin in (
        (create_research_app(load_reviewed_archive(d04, d05), bearer_token=token), evidence / "m1-d06/openapi_v1.json"),
        (create_chain_research_app(load_reviewed_chain(d04, d05, d08), bearer_token=token), d08 / "openapi_v2.json"),
        (create_catalog_research_app(load_reviewed_catalog(evidence), bearer_token=token), evidence / "m1-d09/openapi_v3.json"),
    ):
        assert app.openapi() == json.loads(pin.read_bytes())


def test_schema_version_routes_and_legacy_pins():
    schema = strategy_openapi()
    assert schema["openapi"] == "3.1.0"
    paths = schema["paths"]
    base = "/api/v4/research/strategy/"
    assert set(paths) == {base + route for route in ("coverage", "chain", "payoffs", "valuations")}
    for path, operations in paths.items():
        method = "post" if path.endswith(("payoffs", "valuations")) else "get"
        assert set(operations) == {method}
        assert operations[method]["security"] == [{"ResearchBearer": []}]
    assert schema["components"]["schemas"]["StrategyPayoffRequestDTO"]["additionalProperties"] is False
    pins = {
        "m1-d06/openapi_v1.json": "1d8aca0aa045b5a731c25aa46b61d60dab68c8714088877f2e105a300ae8fbc6",
        "m1-d08/cu2211-20220907/openapi_v2.json": "46b37b055d5ee8e275877d1feb4aaedf94e807f3bd50b1661f67c7632ce77e1e",
        "m1-d09/openapi_v3.json": "a3cb4e79acc127a13d69565494259c232d65835a50edaf2c6f6e50268ca5b7f3",
    }
    for path, pin in pins.items():
        assert hashlib.sha256((ROOT / "docs/evidence" / path).read_bytes()).hexdigest() == pin
