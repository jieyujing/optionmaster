"""M1-D01 offline acceptance: exact historical option/underlying quote pairing.

These are deterministic materialized rows, NOT an executed ClickHouse adapter.
No symbol-prefix inference, CTP calls, historical forward-fill, or DB writes.
"""
from dataclasses import replace
from datetime import date, datetime, timedelta, timezone

import pytest

from vnpy_optionmaster.research_combination import ResearchAssetClass
from vnpy_optionmaster.research_data_basis import (
    HistoricalBasis,
    HistoricalBasisEngine,
    HistoricalBasisRequest,
    HistoricalLegSelection,
    HistoricalQuoteRow,
)
from vnpy_optionmaster.research_risk import ResearchValuationStatus

CN = timezone(timedelta(hours=8))
DAY = date(2022, 9, 7)
READ_AT = datetime(2026, 11, 24, 10, 0, tzinfo=CN)
INGESTED = datetime(2026, 10, 11, 21, 38, tzinfo=CN)


def _selection(
    option: str = "A2211-C-5000.XDCE",
    underlying_id: str = "A2211.XDCE",
    quote_symbol: str = "DCE.a2211",
    *,
    family: str = "A",
    option_exchange: str = "XDCE",
    quote_exchange: str = "DCE",
    asset: ResearchAssetClass = ResearchAssetClass.FUTURES_OPTION,
    mapping: str = "VERIFIED_CONTRACT_TERMS_AND_EXTERNAL_SYMBOL_MAP",
) -> HistoricalLegSelection:
    return HistoricalLegSelection(
        option_symbol=option,
        option_exchange=option_exchange,
        underlying_contract_id=underlying_id,
        underlying_quote_symbol=quote_symbol,
        underlying_quote_exchange=quote_exchange,
        product_family_id=family,
        asset_class=asset,
        mapping_provenance=mapping,
    )


def _quote(symbol: str, exchange: str, *,
           close: float | None = 100.0,
           settlement: float | None = 101.0,
           trade_date: date = DAY,
           source: str = "JQ",
           ingested_at: datetime = INGESTED) -> HistoricalQuoteRow:
    return HistoricalQuoteRow(
        symbol=symbol,
        exchange=exchange,
        trade_date=trade_date,
        close=close,
        settlement=settlement,
        source=source,
        ingested_at=ingested_at,
    )


def _request(
    *,
    selections: tuple[HistoricalLegSelection, ...] | None = None,
    options: tuple[HistoricalQuoteRow, ...] | None = None,
    underlyings: tuple[HistoricalQuoteRow, ...] | None = None,
    read_at: datetime = READ_AT,
    trade_date: date = DAY,
) -> HistoricalBasisRequest:
    near = _selection()
    far = _selection("A2301-C-5000.XDCE", "A2301.XDCE", "DCE.a2301")
    if selections is None:
        selections = (near, far)
    if options is None:
        options = (
            _quote(near.option_symbol, near.option_exchange, close=772, settlement=772),
            _quote(far.option_symbol, far.option_exchange, close=600, settlement=605),
        )
    if underlyings is None:
        underlyings = (
            _quote(near.underlying_quote_symbol, near.underlying_quote_exchange, close=5768, settlement=5772),
            _quote(far.underlying_quote_symbol, far.underlying_quote_exchange, close=5645, settlement=5664),
        )
    return HistoricalBasisRequest(
        trade_date=trade_date, read_at=read_at,
        selections=selections, option_quotes=options, underlying_quotes=underlyings,
    )


def test_real_quantfeed_symbol_form_requires_explicit_cross_source_mapping() -> None:
    result = HistoricalBasisEngine.evaluate(_request())
    assert result.status is ResearchValuationStatus.OK
    assert result.price_basis is HistoricalBasis.SETTLEMENT
    assert result.fallback_reason is None
    assert result.trade_date == DAY and result.read_at == READ_AT
    assert [(v.option_symbol, v.underlying_contract_id, v.underlying_quote_symbol) for v in result.legs] == [
        ("A2211-C-5000.XDCE", "A2211.XDCE", "DCE.a2211"),
        ("A2301-C-5000.XDCE", "A2301.XDCE", "DCE.a2301"),
    ]
    assert [(v.option_price, v.underlying_price) for v in result.legs] == [
        (772, 5772), (605, 5664),
    ]
    assert result.legs[0].mapping_provenance == "VERIFIED_CONTRACT_TERMS_AND_EXTERNAL_SYMBOL_MAP"
    assert result.legs[0].option_source == "JQ"
    assert result.legs[0].underlying_source == "JQ"
    assert result.legs[0].option_ingested_at == INGESTED


@pytest.mark.parametrize(("kind", "index"), [
    ("option", 0), ("underlying", 0), ("option", 1), ("underlying", 1),
])
def test_any_missing_settlement_forces_all_legs_to_close_close(
    kind: str, index: int,
) -> None:
    req = _request()
    options, underlyings = list(req.option_quotes), list(req.underlying_quotes)
    target = options if kind == "option" else underlyings
    target[index] = replace(target[index], settlement=None)
    result = HistoricalBasisEngine.evaluate(replace(
        req, option_quotes=tuple(options), underlying_quotes=tuple(underlyings),
    ))
    assert result.status is ResearchValuationStatus.OK
    assert result.price_basis is HistoricalBasis.CLOSE_FALLBACK
    assert result.fallback_reason == "INCOMPLETE_SETTLEMENT_COVERAGE"
    assert [(x.option_price, x.underlying_price) for x in result.legs] == [
        (772, 5768), (600, 5645),
    ]


def test_missing_close_for_any_leg_rejects_combination_not_mixed_basis() -> None:
    req = _request()
    options = (
        replace(req.option_quotes[0], settlement=None),
        replace(req.option_quotes[1], close=None),
    )
    result = HistoricalBasisEngine.evaluate(replace(req, option_quotes=options))
    assert result.status is ResearchValuationStatus.UNAVAILABLE
    assert result.reason_code == "NO_COMMON_PRICE_BASIS"
    assert result.price_basis is None
    assert result.legs == ()


def test_complete_settlement_ignores_missing_close_without_fake_fallback() -> None:
    req = _request()
    result = HistoricalBasisEngine.evaluate(replace(
        req, option_quotes=(replace(req.option_quotes[0], close=None), req.option_quotes[1]),
    ))
    assert result.status is ResearchValuationStatus.OK
    assert result.price_basis is HistoricalBasis.SETTLEMENT


@pytest.mark.parametrize("which", ["option", "underlying"])
def test_different_trade_date_cannot_be_paired(which: str) -> None:
    req = _request()
    if which == "option":
        changed = (replace(req.option_quotes[0], trade_date=DAY + timedelta(days=1)), *req.option_quotes[1:])
        bad = replace(req, option_quotes=changed)
    else:
        changed = (replace(req.underlying_quotes[0], trade_date=DAY + timedelta(days=1)), *req.underlying_quotes[1:])
        bad = replace(req, underlying_quotes=changed)
    result = HistoricalBasisEngine.evaluate(bad)
    assert result.status is ResearchValuationStatus.UNAVAILABLE
    assert result.reason_code == "MISMATCHED_TRADE_DATE"
    assert result.legs == ()


@pytest.mark.parametrize("which", ["option", "underlying"])
def test_missing_exact_quote_never_silently_uses_another(which: str) -> None:
    req = _request()
    bad = replace(req, **{
        "option_quotes" if which == "option" else "underlying_quotes":
        req.option_quotes[1:] if which == "option" else req.underlying_quotes[1:],
    })
    result = HistoricalBasisEngine.evaluate(bad)
    assert result.status is ResearchValuationStatus.UNAVAILABLE
    assert result.reason_code == ("MISSING_OPTION_QUOTE" if which == "option" else "MISSING_UNDERLYING_QUOTE")


@pytest.mark.parametrize("which", ["option", "underlying"])
def test_replacing_merge_tree_duplicate_rows_require_explicit_dedup(which: str) -> None:
    req = _request()
    key = "option_quotes" if which == "option" else "underlying_quotes"
    quotes = getattr(req, key)
    bad = replace(req, **{key: (*quotes, quotes[0])})
    result = HistoricalBasisEngine.evaluate(bad)
    assert result.status is ResearchValuationStatus.UNAVAILABLE
    assert result.reason_code == "DUPLICATE_QUOTE"


def test_extra_rows_are_not_silently_mixed_with_request_scope() -> None:
    req = _request()
    result = HistoricalBasisEngine.evaluate(replace(
        req, underlying_quotes=(*req.underlying_quotes, _quote("DCE.a2405", "DCE")),
    ))
    assert result.status is ResearchValuationStatus.UNAVAILABLE
    assert result.reason_code == "UNEXPECTED_QUOTE"


def test_mismatched_source_quote_symbol_must_not_be_inferred_from_underlying_id() -> None:
    req = _request()
    first = replace(req.selections[0], underlying_quote_symbol="A2211.XDCE")
    result = HistoricalBasisEngine.evaluate(replace(req, selections=(first, req.selections[1])))
    assert result.status is ResearchValuationStatus.UNAVAILABLE
    assert result.reason_code == "MISSING_UNDERLYING_QUOTE"


def test_missing_mapping_provenance_is_invalid() -> None:
    req = _request()
    result = HistoricalBasisEngine.evaluate(replace(
        req, selections=(replace(req.selections[0], mapping_provenance=""), req.selections[1]),
    ))
    assert result.status is ResearchValuationStatus.UNAVAILABLE
    assert result.reason_code == "INVALID_INPUT"


def test_same_underlying_can_be_shared_by_two_option_strikes() -> None:
    req = _request()
    second = _selection("A2211-C-5100.XDCE", "A2211.XDCE", "DCE.a2211")
    result = HistoricalBasisEngine.evaluate(replace(
        req,
        selections=(req.selections[0], second),
        option_quotes=(req.option_quotes[0], _quote(second.option_symbol, second.option_exchange)),
        underlying_quotes=(req.underlying_quotes[0],),
    ))
    assert result.status is ResearchValuationStatus.OK
    assert len(result.legs) == 2 and result.legs[0].underlying_price == result.legs[1].underlying_price


def test_two_canonical_underlying_ids_cannot_share_one_source_quote_alias() -> None:
    req = _request()
    second = replace(req.selections[1], underlying_quote_symbol=req.selections[0].underlying_quote_symbol)
    result = HistoricalBasisEngine.evaluate(replace(req, selections=(req.selections[0], second)))
    assert result.status is ResearchValuationStatus.UNAVAILABLE
    assert result.reason_code == "UNDERLYING_ALIAS_CONFLICT"


@pytest.mark.parametrize("modify", [
    {"product_family_id": "P"},
    {"asset_class": ResearchAssetClass.INDEX_OPTION},
    {"option_exchange": "XSGE"},
])
def test_cross_product_or_mixed_asset_rejected(modify: dict[str, object]) -> None:
    req = _request()
    second = replace(req.selections[1], **modify)
    result = HistoricalBasisEngine.evaluate(replace(req, selections=(req.selections[0], second)))
    assert result.status is ResearchValuationStatus.UNAVAILABLE
    assert result.reason_code == "UNSUPPORTED_COMBINATION"


def test_duplicate_option_selection_is_invalid() -> None:
    req = _request()
    result = HistoricalBasisEngine.evaluate(replace(
        req, selections=(req.selections[0], req.selections[0]),
    ))
    assert result.status is ResearchValuationStatus.UNAVAILABLE
    assert result.reason_code == "DUPLICATE_LEG"


def test_index_and_etf_have_close_only_basis_even_with_settlement_available() -> None:
    for asset in (ResearchAssetClass.INDEX_OPTION, ResearchAssetClass.ETF_OPTION):
        a = _selection("IO2301-C-2000.CCFX", "000016.XSHG", "000016.XSHG",
                       asset=asset, option_exchange="CCFX",
                       quote_exchange="XSHG", family="CSI50")
        b = replace(a, option_symbol="IO2301-C-2100.CCFX")
        req = _request(
            selections=(a, b),
            options=(_quote(a.option_symbol, "CCFX", close=200), _quote(b.option_symbol, "CCFX", close=250)),
            underlyings=(_quote("000016.XSHG", "XSHG", close=2700),),
        )
        result = HistoricalBasisEngine.evaluate(req)
        assert result.status is ResearchValuationStatus.OK
        assert result.price_basis is HistoricalBasis.CLOSE
        assert [x.underlying_price for x in result.legs] == [2700, 2700]


def test_index_spot_underlying_identity_must_not_be_mixed() -> None:
    first = _selection("IO-C-1", "000300.XSHG", "000300.XSHG",
                       asset=ResearchAssetClass.INDEX_OPTION, option_exchange="CCFX",
                       quote_exchange="XSHG", family="CSI300")
    second = replace(first, option_symbol="IO-C-2", underlying_contract_id="000016.XSHG",
                     underlying_quote_symbol="000016.XSHG")
    result = HistoricalBasisEngine.evaluate(_request(selections=(first, second), options=(), underlyings=()))
    assert result.status is ResearchValuationStatus.UNAVAILABLE
    assert result.reason_code == "UNSUPPORTED_COMBINATION"


def test_missing_index_close_returns_no_common_basis() -> None:
    a = _selection("IO-C-1", "000300.XSHG", "000300.XSHG",
                   asset=ResearchAssetClass.INDEX_OPTION, option_exchange="CCFX",
                   quote_exchange="XSHG", family="CSI300")
    b = replace(a, option_symbol="IO-C-2")
    result = HistoricalBasisEngine.evaluate(_request(
        selections=(a, b),
        options=(_quote("IO-C-1", "CCFX", close=None), _quote("IO-C-2", "CCFX")),
        underlyings=(_quote("000300.XSHG", "XSHG"),),
    ))
    assert result.status is ResearchValuationStatus.UNAVAILABLE
    assert result.reason_code == "NO_COMMON_PRICE_BASIS"


@pytest.mark.parametrize("bad_value", [0, -1, float("nan"), float("inf"), 10**1000, True])
def test_invalid_quote_numbers_fail_closed_instead_of_fallback(bad_value: object) -> None:
    req = _request()
    result = HistoricalBasisEngine.evaluate(replace(
        req, option_quotes=(replace(req.option_quotes[0], settlement=bad_value), req.option_quotes[1]),
    ))
    assert result.status is ResearchValuationStatus.UNAVAILABLE
    assert result.reason_code == "INVALID_QUOTE"
    assert result.legs == ()


def test_source_and_timestamp_provenance_must_be_valid() -> None:
    req = _request()
    for bad in (
        replace(req.option_quotes[0], source=""),
        replace(req.option_quotes[0], ingested_at=datetime(2026, 10, 11, 21)),
        replace(req.option_quotes[0], ingested_at=READ_AT + timedelta(seconds=1)),
    ):
        result = HistoricalBasisEngine.evaluate(replace(
            req, option_quotes=(bad, req.option_quotes[1]),
        ))
        assert result.status is ResearchValuationStatus.UNAVAILABLE


def test_trade_date_and_read_at_must_be_explicit_and_sane() -> None:
    req = _request()
    for bad in (replace(req, trade_date="2022-09-07"),
                replace(req, read_at=datetime(2026, 11, 24, 10)),
                replace(req, read_at=DAY)):
        result = HistoricalBasisEngine.evaluate(bad)
        assert result.status is ResearchValuationStatus.UNAVAILABLE
        assert result.reason_code == "INVALID_INPUT"


def test_unmatched_option_exchange_quote_rejected_without_default_exchange() -> None:
    req = _request()
    wrong = replace(req.option_quotes[0], exchange="DCE")
    result = HistoricalBasisEngine.evaluate(replace(
        req, option_quotes=(wrong, req.option_quotes[1]),
    ))
    assert result.status is ResearchValuationStatus.UNAVAILABLE
    assert result.reason_code == "MISSING_OPTION_QUOTE"


def test_non_request_input_rejected_and_result_immutable() -> None:
    bad = HistoricalBasisEngine.evaluate(object())
    assert bad.status is ResearchValuationStatus.UNAVAILABLE
    result = HistoricalBasisEngine.evaluate(_request())
    assert result.status is ResearchValuationStatus.OK
    with pytest.raises((AttributeError, TypeError)):
        result.price_basis = HistoricalBasis.CLOSE
