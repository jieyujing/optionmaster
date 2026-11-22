"""L1 book quality must separate direction executable from mark reliability."""
from math import inf, nan
import pytest
from vnpy_optionmaster.market_data_quality import QuoteState, evaluate_quote, valid_order_price

from test_hedge_orders import _tick, _contract


def _quote(bid: float, bsize: float, ask: float, asize: float):
    tick = _tick(_contract(1))
    tick.bid_price_1, tick.bid_volume_1 = bid, bsize
    tick.ask_price_1, tick.ask_volume_1 = ask, asize
    return tick


@pytest.mark.parametrize(("bid", "bv", "ask", "av", "expected"), [
    (9.5, 10, 10.0, 8, QuoteState.TWO_SIDED),
    (9.5, 10, 0.0, 0, QuoteState.BID_ONLY),
    (0.0, 0, 10.0, 8, QuoteState.ASK_ONLY),
    (0.0, 0, 0.0, 0, QuoteState.EMPTY),
    (10.0, 2, 10.0, 2, QuoteState.INVALID),
    (10.5, 2, 10.0, 2, QuoteState.INVALID),
    (10.0, 0, 10.5, 2, QuoteState.INVALID),
    (0.0, 3, 10.5, 2, QuoteState.INVALID),
    (-1.0, 2, 10.0, 2, QuoteState.INVALID),
    (nan, 2, 10.0, 2, QuoteState.INVALID),
    (inf, 2, 10.0, 2, QuoteState.INVALID),
    (1.7976931348623157e308, 2, 10.0, 2, QuoteState.INVALID),
    (9.4, 2, 10.0, 2, QuoteState.INVALID),  # 0.5 tick misalignment
])
def test_book_classification(bid, bv, ask, av, expected):
    assert evaluate_quote(_quote(bid, bv, ask, av), pricetick=0.5).state == expected


def test_one_sided_bid_executes_sell_but_not_buy():
    quote = evaluate_quote(_quote(9.5, 10, 0.0, 0), pricetick=0.5)
    assert quote.can_sell and not quote.can_buy
    assert not quote.has_reliable_mid


def test_bad_volume_quote_does_not_look_like_legit_single_side():
    q = evaluate_quote(_quote(9.5, 0, 0.0, 0), pricetick=0.5)
    assert q.state == QuoteState.INVALID


@pytest.mark.parametrize(("price", "expected"), [
    (9.0, True), (10.0, True), (10.5, False),
    (8.5, False), (9.3, False), (0.0, False), (nan, False)
])
def test_final_limit_validated_after_payup(price, expected):
    tick = _quote(9.5, 10, 10.0, 10)
    tick.limit_down, tick.limit_up = 9.0, 10.0
    assert valid_order_price(tick, price, pricetick=0.5) is expected


def test_missing_price_limits_fail_closed():
    tick = _quote(9.5, 10, 10.0, 10)
    tick.limit_up, tick.limit_down = 0.0, 0.0
    assert not valid_order_price(tick, 10.0, pricetick=0.5)
