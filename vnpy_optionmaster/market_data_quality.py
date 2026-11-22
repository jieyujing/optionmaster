"""CTP/VeighNa L1 quote quality and executable-side decision contract."""
from dataclasses import dataclass
from enum import Enum
from math import isfinite
from typing import Any


MAX_CTP_SENTINEL: float = 1e100


class QuoteState(str, Enum):
    TWO_SIDED = "VALID_TWO_SIDED"
    BID_ONLY = "BID_ONLY"
    ASK_ONLY = "ASK_ONLY"
    EMPTY = "EMPTY"
    INVALID = "INVALID"


@dataclass(frozen=True)
class QuoteQuality:
    state: QuoteState
    reason: str

    @property
    def can_buy(self) -> bool:
        return self.state in (QuoteState.TWO_SIDED, QuoteState.ASK_ONLY)

    @property
    def can_sell(self) -> bool:
        return self.state in (QuoteState.TWO_SIDED, QuoteState.BID_ONLY)

    @property
    def has_reliable_mid(self) -> bool:
        return self.state == QuoteState.TWO_SIDED


def _finite_number(value: Any) -> bool:
    return isinstance(value, (int, float)) and isfinite(float(value))


def _price_step_ok(price: float, pricetick: float | None) -> bool:
    if pricetick is None:
        return True
    if not _finite_number(pricetick) or pricetick <= 0:
        return False
    steps = price / pricetick
    return abs(steps - round(steps)) <= 1e-7


def _side_status(price: Any, volume: Any, pricetick: float | None) -> str:
    if not _finite_number(price) or not _finite_number(volume):
        return "invalid"
    if price >= MAX_CTP_SENTINEL or volume >= MAX_CTP_SENTINEL:
        return "invalid"
    if price == 0 and volume == 0:
        return "absent"
    if (price <= 0 or volume <= 0
            or not _price_step_ok(float(price), pricetick)):
        return "invalid"
    return "valid"


def evaluate_quote(tick: Any, *, pricetick: float | None = None) -> QuoteQuality:
    """Do not infer that a missing side is a zero-priced executable quote."""
    if tick is None:
        return QuoteQuality(QuoteState.INVALID, "no_tick")
    bid = getattr(tick, "bid_price_1", None)
    ask = getattr(tick, "ask_price_1", None)
    bid_state = _side_status(bid, getattr(tick, "bid_volume_1", None), pricetick)
    ask_state = _side_status(ask, getattr(tick, "ask_volume_1", None), pricetick)

    if bid_state == "invalid":
        return QuoteQuality(QuoteState.INVALID, "invalid_bid")
    if ask_state == "invalid":
        return QuoteQuality(QuoteState.INVALID, "invalid_ask")
    if bid_state == "valid" and ask_state == "valid":
        if bid is not None and ask is not None and bid >= ask:
            return QuoteQuality(QuoteState.INVALID, "locked_or_crossed_book")
        return QuoteQuality(QuoteState.TWO_SIDED, "ok")
    if bid_state == "valid":
        return QuoteQuality(QuoteState.BID_ONLY, "ask_absent")
    if ask_state == "valid":
        return QuoteQuality(QuoteState.ASK_ONLY, "bid_absent")
    return QuoteQuality(QuoteState.EMPTY, "empty_book")


def valid_order_price(tick: Any, price: Any, *, pricetick: float) -> bool:
    """Final limit check AFTER payup and rounding, fail closed on missing bounds."""
    if not _finite_number(price):
        return False
    checked_price = float(price)
    if checked_price <= 0 or checked_price >= MAX_CTP_SENTINEL:
        return False
    if not _price_step_ok(checked_price, pricetick):
        return False
    upper = getattr(tick, "limit_up", None)
    lower = getattr(tick, "limit_down", None)
    if not _finite_number(upper) or not _finite_number(lower):
        return False
    ceiling = float(upper if upper is not None else 0.0)
    floor = float(lower if lower is not None else 0.0)
    if (ceiling <= 0 or floor <= 0 or ceiling < floor
            or ceiling >= MAX_CTP_SENTINEL):
        return False
    tolerance = max(pricetick * 1e-7, 1e-9)
    return floor - tolerance <= checked_price <= ceiling + tolerance


def direction_is_executable(quality: QuoteQuality, *, buying: bool) -> bool:
    return quality.can_buy if buying else quality.can_sell
