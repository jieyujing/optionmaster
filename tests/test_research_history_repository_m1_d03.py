"""M1-D03 public repository boundary, deterministic transport fixtures only."""
from collections.abc import Mapping
from dataclasses import replace
from datetime import date, timedelta

import pytest

from test_research_registry_admission_m1_d03 import (
    DAY,
    END,
    OPT,
    PUT,
    QUOTE,
    READ_AT,
    REVIEW_AT,
    UND,
    fixture_registry,
)

from vnpy_optionmaster.research_data_basis import HistoricalBasis
from vnpy_optionmaster.research_history_repository import (
    HistoricalFuturesLeg,
    HistoricalReadRequest,
    HistoricalReadStatus,
    HistoricalRepository,
)

Row = Mapping[str, object]

PORTFOLIO = "XDCE.A2211.XDCE"
CHAIN = "XDCE|A2211.XDCE|2022-10-14"
SETTLEMENT_SOURCE = "JOINQUANT_GET_EXTRAS:futures_sett_price"
PROFILE = {
    "readonly": "1", "max_rows_to_read": "100000",
    "max_bytes_to_read": "67108864", "max_execution_time": "5",
    "max_result_rows": "64", "max_result_bytes": "1048576",
    "read_overflow_mode": "throw", "result_overflow_mode": "throw",
    "timeout_overflow_mode": "throw",
}


def _terms(symbol: str, right: str, version: str) -> dict[str, object]:
    return {
        "symbol": symbol, "exchange": "XDCE", "trade_date": DAY,
        "portfolio_code": PORTFOLIO, "chain_id": CHAIN,
        "underlying_symbol": UND.symbol, "expiry_date": END,
        "option_type": right, "exercise_style": "UNKNOWN", "strike": 5000.0,
        "contract_size": 10.0, "contract_version": version,
        "source": "JQ", "provenance": "TEST_ONLY_RAW", "ingested_at": REVIEW_AT,
    }


def _option(symbol: str, close: float, settlement: float | None) -> dict[str, object]:
    return {
        "symbol": symbol, "exchange": "XDCE", "trade_date": DAY,
        "close": close, "settlement": settlement, "source": "JQ", "ingested_at": REVIEW_AT,
    }


class FakeSelect:
    """Only the external CH transport is replaced; D02 and D01 are real code."""

    def __init__(self) -> None:
        self.profile = dict(PROFILE)
        self.terms: dict[str, list[Row]] = {
            OPT.symbol: [_terms(OPT.symbol, "CALL", "fixture-call-v1")],
            PUT.symbol: [_terms(PUT.symbol, "PUT", "fixture-put-v1")],
        }
        self.options: dict[str, list[Row]] = {
            OPT.symbol: [_option(OPT.symbol, 770, 772)],
            PUT.symbol: [_option(PUT.symbol, 20, 21)],
        }
        self.futures: dict[str, list[Row]] = {
            QUOTE.symbol: [{
                "symbol": QUOTE.symbol, "exchange": "DCE", "trade_date": DAY,
                "product_symbol": "DCE.A", "close": 5768.0, "settlement": 5772.0,
                "price_source": "JQ", "settlement_source": SETTLEMENT_SOURCE, "ingested_at": REVIEW_AT,
            }],
        }
        self.calls: list[tuple[str, dict[str, str], float]] = []
        self.fail = False

    def select(
        self, sql: str, parameters: Mapping[str, str], *, timeout_seconds: float,
    ) -> tuple[Row, ...]:
        self.calls.append((sql, dict(parameters), timeout_seconds))
        if self.fail:
            raise TimeoutError("private transport failure detail")
        if "FROM system.settings" in sql:
            return tuple({"name": k, "value": v} for k, v in self.profile.items())
        assert parameters["trade_date"] == DAY.isoformat()
        symbol = parameters["symbol"]
        if "FROM quantfeed.option_contract_terms_daily" in sql:
            assert parameters["portfolio_code"] == PORTFOLIO
            return tuple(self.terms.get(symbol, ()))
        if "FROM quantfeed.option_daily" in sql:
            return tuple(self.options.get(symbol, ()))
        if "FROM quantfeed.futures_daily" in sql:
            assert parameters["product_symbol"] == "DCE.A"
            return tuple(self.futures.get(symbol, ()))
        raise AssertionError("Unexpected query")


def _request() -> HistoricalReadRequest:
    return HistoricalReadRequest(DAY, (
        HistoricalFuturesLeg(PORTFOLIO, CHAIN, OPT, "DCE.A", QUOTE, SETTLEMENT_SOURCE),
        HistoricalFuturesLeg(PORTFOLIO, CHAIN, PUT, "DCE.A", QUOTE, SETTLEMENT_SOURCE),
    ))


def _repo(transport: FakeSelect, *, approved: bool = True) -> HistoricalRepository:
    registry, approval, artifacts = fixture_registry()
    return HistoricalRepository(
        transport, registry, approval if approved else None, artifacts,
        allowed_settlement_sources=(SETTLEMENT_SOURCE,), clock=lambda: READ_AT,
    )


def _reject(transport: FakeSelect, reason: str, request: HistoricalReadRequest | None = None) -> None:
    result = _repo(transport).materialize(request or _request())
    assert result.status is HistoricalReadStatus.UNAVAILABLE
    assert result.reason_code == reason
    assert result.candidate is None


def test_materializes_one_bundle_validates_d02_then_d01_with_shared_underlying_once() -> None:
    transport = FakeSelect()
    result = _repo(transport).materialize(_request())
    assert result.status is HistoricalReadStatus.CANDIDATE
    candidate = result.candidate
    assert candidate is not None
    assert candidate.trade_date == DAY and candidate.read_at == READ_AT
    assert candidate.valuation_ready is False
    assert candidate.consistency == "UNVERIFIED_CROSS_TABLE"
    assert candidate.basis.price_basis is HistoricalBasis.SETTLEMENT
    assert [(leg.option_price, leg.underlying_price) for leg in candidate.basis.legs] == [(772, 5772), (21, 5772)]
    assert len(candidate.contracts) == 2
    assert candidate.contracts[0].terms.contract_version == "fixture-call-v1"
    assert candidate.contracts[0].terms.exercise_style.value == "AMERICAN"
    assert len(candidate.futures_quotes) == 1
    assert candidate.futures_quotes[0].settlement_source == SETTLEMENT_SOURCE
    assert len(candidate.registry_sha256) == 64
    assert len(transport.calls) == 6
    for sql, params, timeout in transport.calls[1:]:
        assert sql.lstrip().startswith("SELECT ")
        assert "LIMIT 2" in sql
        assert "FINAL" not in sql and "SETTINGS" not in sql
        assert params["symbol"] not in sql and "{symbol:String}" in sql
        assert "{trade_date:Date}" in sql and 0 < timeout <= 5


def test_every_request_reloads_once_instead_of_caching_history() -> None:
    transport = FakeSelect()
    repository = _repo(transport)
    first = repository.materialize(_request())
    transport.options[OPT.symbol] = [_option(OPT.symbol, 780, 782)]
    second = repository.materialize(_request())
    assert first.candidate is not None and second.candidate is not None
    assert first.candidate.basis.legs[0].option_price == 772
    assert second.candidate.basis.legs[0].option_price == 782
    assert len(transport.calls) == 12


def test_no_registry_approval_stops_before_database() -> None:
    transport = FakeSelect()
    result = _repo(transport, approved=False).materialize(_request())
    assert result.status is HistoricalReadStatus.UNAVAILABLE
    assert result.reason_code == "REGISTRY_NOT_APPROVED"
    assert transport.calls == []


@pytest.mark.parametrize(("setting", "bad"), [
    ("readonly", "0"), ("readonly", "2"), ("max_rows_to_read", "0"),
    ("max_rows_to_read", "100001"), ("max_bytes_to_read", "0"),
    ("max_execution_time", "0"), ("max_execution_time", "NaN"),
    ("max_result_rows", "0"), ("max_result_bytes", "0"),
    ("read_overflow_mode", "break"), ("result_overflow_mode", "break"),
    ("timeout_overflow_mode", "break"),
])
def test_profile_gate_prevents_any_market_queries(setting: str, bad: str) -> None:
    transport = FakeSelect()
    transport.profile[setting] = bad
    _reject(transport, "UNSAFE_READ_PROFILE")
    assert len(transport.calls) == 1


def test_missing_profile_setting_fails_closed() -> None:
    transport = FakeSelect()
    del transport.profile["max_bytes_to_read"]
    _reject(transport, "UNSAFE_READ_PROFILE")
    assert len(transport.calls) == 1


def test_no_default_settlement_source_admission() -> None:
    transport = FakeSelect()
    registry, approval, artifacts = fixture_registry()
    repository = HistoricalRepository(transport, registry, approval, artifacts, clock=lambda: READ_AT)
    result = repository.materialize(_request())
    assert result.reason_code == "UNAPPROVED_SETTLEMENT_SOURCE"
    assert result.candidate is None
    assert transport.calls == []


def test_late_transport_return_stops_further_reads(monkeypatch: pytest.MonkeyPatch) -> None:
    # Simulate a transport violating the individual five-second call deadline.
    times = iter((0.0, 0.0, 6.0))
    monkeypatch.setattr("vnpy_optionmaster.research_history_repository.monotonic", lambda: next(times))
    transport = FakeSelect()
    _reject(transport, "QUERY_BUDGET_EXCEEDED")
    assert len(transport.calls) == 1


@pytest.mark.parametrize(("table", "symbol", "reason"), [
    ("terms", OPT.symbol, "DUPLICATE_TERMS_ROW"),
    ("options", OPT.symbol, "DUPLICATE_OPTION_QUOTE"),
    ("futures", QUOTE.symbol, "DUPLICATE_UNDERLYING_QUOTE"),
])
def test_duplicate_rows_are_not_latest_wins(table: str, symbol: str, reason: str) -> None:
    transport = FakeSelect()
    rows = getattr(transport, table)[symbol]
    rows.append(dict(rows[0]))
    _reject(transport, reason)


@pytest.mark.parametrize(("table", "symbol", "reason"), [
    ("terms", OPT.symbol, "MISSING_TERMS_ROW"),
    ("options", OPT.symbol, "MISSING_OPTION_QUOTE"),
    ("futures", QUOTE.symbol, "MISSING_UNDERLYING_QUOTE"),
])
def test_missing_rows_return_atomic_unavailable(table: str, symbol: str, reason: str) -> None:
    transport = FakeSelect()
    getattr(transport, table)[symbol] = []
    _reject(transport, reason)


def test_real_shaped_unknown_blank_version_cannot_reach_quotes() -> None:
    transport = FakeSelect()
    transport.terms[OPT.symbol] = [{**transport.terms[OPT.symbol][0], "contract_version": ""}]
    _reject(transport, "MISSING_CONTRACT_VERSION")
    assert all("option_daily" not in sql and "futures_daily" not in sql for sql, _, _ in transport.calls)


@pytest.mark.parametrize(("table", "symbol", "field", "value"), [
    ("terms", OPT.symbol, "chain_id", "wrong-chain"),
    ("terms", OPT.symbol, "source", "other-feed"),
    ("options", OPT.symbol, "source", "GM"),
    ("options", OPT.symbol, "exchange", "DCE"),
    ("futures", QUOTE.symbol, "product_symbol", "DCE.B"),
    ("futures", QUOTE.symbol, "price_source", "GM"),
    ("futures", QUOTE.symbol, "settlement_source", "unapproved-source"),
    ("futures", QUOTE.symbol, "trade_date", date(2022, 9, 8)),
])
def test_actual_rows_must_match_exact_source_and_key(table: str, symbol: str, field: str, value: object) -> None:
    transport = FakeSelect()
    rows = getattr(transport, table)
    rows[symbol] = [{**rows[symbol][0], field: value}]
    _reject(transport, "SOURCE_KEY_CONFLICT")


def test_invalid_quote_is_not_missing_settlement_fallback() -> None:
    transport = FakeSelect()
    transport.options[PUT.symbol] = [_option(PUT.symbol, 20, 0)]
    _reject(transport, "INVALID_QUOTE")


def test_missing_settlement_reuses_d01_combination_wide_close_fallback() -> None:
    transport = FakeSelect()
    transport.options[PUT.symbol] = [_option(PUT.symbol, 20, None)]
    result = _repo(transport).materialize(_request())
    assert result.candidate is not None
    assert result.candidate.basis.price_basis is HistoricalBasis.CLOSE_FALLBACK
    assert [(leg.option_price, leg.underlying_price) for leg in result.candidate.basis.legs] == [(770, 5768), (20, 5768)]


def test_future_ingestion_rejected() -> None:
    transport = FakeSelect()
    transport.options[OPT.symbol] = [{**transport.options[OPT.symbol][0], "ingested_at": READ_AT + timedelta(seconds=1)}]
    _reject(transport, "FUTURE_INGESTED_AT")


def test_transport_exception_no_partial_bundle_or_sensitive_detail() -> None:
    transport = FakeSelect()
    transport.fail = True
    _reject(transport, "QUERY_FAILED")


def test_bad_request_and_duplicate_option_stop_before_query() -> None:
    transport = FakeSelect()
    request = _request()
    _reject(transport, "INVALID_INPUT", replace(request, legs=()))
    _reject(transport, "DUPLICATE_LEG", replace(request, legs=(request.legs[0], request.legs[0])))
    assert not transport.calls


def test_unregistered_option_never_dispatches_terms_lookup() -> None:
    transport = FakeSelect()
    req = _request()
    leg = replace(req.legs[0], option_key=replace(OPT, symbol="unregistered-option"))
    _reject(transport, "MISSING_TERMS_EVIDENCE", replace(req, legs=(leg, req.legs[1])))
    assert transport.calls == []


def test_unregistered_underlying_quote_never_dispatches_lookup() -> None:
    transport = FakeSelect()
    req = _request()
    leg = replace(req.legs[0], underlying_quote_key=replace(QUOTE, symbol="unregistered-future"))
    _reject(transport, "MISSING_ALIAS", replace(req, legs=(leg, req.legs[1])))
    assert transport.calls == []


def test_caller_cannot_self_approve_unreviewed_settlement_provider() -> None:
    transport = FakeSelect()
    req = _request()
    provider = "UNREVIEWED_SETTLEMENT_SOURCE"
    transport.futures[QUOTE.symbol] = [{**transport.futures[QUOTE.symbol][0], "settlement_source": provider}]
    _reject(transport, "UNAPPROVED_SETTLEMENT_SOURCE", replace(
        req, legs=tuple(replace(leg, settlement_source=provider) for leg in req.legs),
    ))
    assert transport.calls == []


def test_native_option_identity_collision_rejected_before_price_reads() -> None:
    transport = FakeSelect()
    registry, approval, artifacts = fixture_registry()
    registry = replace(registry, terms=(
        registry.terms[0], replace(registry.terms[1], option_contract_code=registry.terms[0].option_contract_code),
    ))
    # Independently pinned synthetic but inconsistent registry, not a live admission.
    from vnpy_optionmaster.research_registry_admission import registry_fingerprint
    approval = replace(approval, registry_sha256=registry_fingerprint(registry))
    repository = HistoricalRepository(
        transport, registry, approval, artifacts,
        allowed_settlement_sources=(SETTLEMENT_SOURCE,), clock=lambda: READ_AT,
    )
    result = repository.materialize(_request())
    assert result.status is HistoricalReadStatus.UNAVAILABLE
    assert result.reason_code == "DUPLICATE_NATIVE_OPTION"
    assert result.candidate is None
    assert all("option_daily" not in sql and "futures_daily" not in sql for sql, _, _ in transport.calls)


def test_missing_value_is_not_equivalent_to_missing_column() -> None:
    transport = FakeSelect()
    row = dict(transport.options[OPT.symbol][0])
    del row["settlement"]
    transport.options[OPT.symbol] = [row]
    _reject(transport, "INVALID_QUOTE")


def test_duplicate_or_extra_profile_rows_rejected() -> None:
    class DuplicateProfile(FakeSelect):
        def select(self, sql: str, parameters: Mapping[str, str], *, timeout_seconds: float) -> tuple[Row, ...]:
            rows = super().select(sql, parameters, timeout_seconds=timeout_seconds)
            if "FROM system.settings" in sql:
                return rows + (rows[0],)
            return rows

    transport = DuplicateProfile()
    _reject(transport, "UNSAFE_READ_PROFILE")
    assert len(transport.calls) == 1
