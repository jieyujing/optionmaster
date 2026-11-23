"""Main-event-owner acceptance of immutable worker IV results.

Numerical values are reusable across Tick versions; risk readiness is not.
This module never sends orders or mutates state in a background process.
"""
from dataclasses import dataclass
from math import isfinite
from threading import get_ident
from typing import Any

from vnpy_optionmaster.market_data_quality import QuoteState, evaluate_quote
from vnpy_optionmaster.risk_safety import pricing_input_fingerprint
from vnpy_optionmaster.pricing.iv_worker import IVWorkRequest, IVWorkResult


@dataclass(frozen=True)
class PendingOptionIV:
    request: IVWorkRequest
    owner_thread_id: int
    option_tick: Any
    underlying_tick: Any
    impv_callable: Any
    greeks_callable: Any


def capture_option_iv(
    option: Any, *, model: str, generation: int, sequence: int,
    model_version: int = 1,
) -> PendingOptionIV | None:
    """Capture primitive-only worker payload plus owner-local Tick identity."""
    underlying = getattr(option, "underlying", None)
    if underlying is None or option.tick is None or underlying.tick is None:
        return None
    if not getattr(underlying, "mark_valid", False):
        return None
    option.refresh_expiry()
    quality = evaluate_quote(option.tick, pricetick=option.pricetick)
    if quality.state in (QuoteState.INVALID, QuoteState.EMPTY):
        return None
    adjusted = underlying.mid_price + option.underlying_adjustment
    if not all(isfinite(float(x)) for x in (
        adjusted, option.strike_price, option.interest_rate, option.time_to_expiry,
    )):
        return None
    if adjusted <= 0 or option.time_to_expiry <= 0:
        return None
    bid = option.tick.bid_price_1 if quality.can_sell else 0.0
    ask = option.tick.ask_price_1 if quality.can_buy else 0.0
    mid = (bid + ask) / 2 if quality.has_reliable_mid else 0.0
    request = IVWorkRequest(
        symbol=option.vt_symbol,
        generation=generation,
        sequence=sequence,
        model=model,
        spot=adjusted,
        strike=option.strike_price,
        rate=option.interest_rate,
        expiry=option.time_to_expiry,
        cp=option.option_type,
        bid=bid,
        ask=ask,
        mid=mid,
        quote_identity=(str(id(option.tick)), str(id(underlying.tick))),
        model_version=model_version,
    )
    return PendingOptionIV(
        request=request,
        owner_thread_id=get_ident(),
        option_tick=option.tick,
        underlying_tick=underlying.tick,
        impv_callable=option.calculate_impv,
        greeks_callable=option.calculate_greeks,
    )


def commit_option_iv(
    option: Any, pending: PendingOptionIV, result: IVWorkResult,
) -> bool:
    """Reject stale snapshots before any risk mutation, then aggregate once.

    A changed position does not invalidate the numerical IV (price inputs may
    still be identical), but exposure aggregation always uses current holdings.
    """
    request = pending.request
    if pending.owner_thread_id != get_ident():
        return False
    if (
        result.symbol != request.symbol
        or result.generation != request.generation
        or result.sequence != request.sequence
        or result.quote_identity != request.quote_identity
        or result.model_version != request.model_version
    ):
        return False
    underlying = getattr(option, "underlying", None)
    if (
        underlying is None
        or option.tick is not pending.option_tick
        or underlying.tick is not pending.underlying_tick
        or option.calculate_impv is not pending.impv_callable
        or option.calculate_greeks is not pending.greeks_callable
    ):
        return False
    current = capture_option_iv(
        option, model=request.model, generation=request.generation,
        sequence=request.sequence, model_version=request.model_version,
    )
    if current is None or current.request != request:
        return False

    inputs_before_greeks = pricing_input_fingerprint(option)
    mark_ready = (
        getattr(option, "mark_valid", False)
        and getattr(underlying, "mark_valid", False)
        and result.mid.valid
    )
    greeks: tuple[float, float, float, float] | None = None
    if mark_ready and result.mid.volatility is not None:
        try:
            _, delta, gamma, theta, vega = option.calculate_greeks(
                request.spot, request.strike, request.rate,
                request.expiry, result.mid.volatility, request.cp,
            )
            if all(isfinite(x) for x in (delta, gamma, theta, vega)):
                greeks = (
                    delta * option.size,
                    gamma * option.size,
                    theta * option.size / 240,
                    vega * option.size / 100,
                )
        except (ValueError, ArithmeticError, TypeError):
            greeks = None

    if inputs_before_greeks != pricing_input_fingerprint(option):
        return False

    # Commit as a single owner event after all version and numeric checks.
    option.bid_impv_valid = result.bid.valid
    option.ask_impv_valid = result.ask.valid
    option.mid_impv_valid = result.mid.valid and bool(mark_ready)
    for side in ("bid", "ask", "mid"):
        iv = getattr(result, side)
        if iv.valid and iv.volatility is not None:
            setattr(option, f"{side}_impv", iv.volatility)
    option.impv_valid = bool(option.mid_impv_valid)
    option.greeks_valid = greeks is not None
    option.greeks_worker_generation = result.generation if greeks is not None else None
    option.greeks_input_identity = inputs_before_greeks if greeks is not None else None
    option.greeks_quote_version = (
        (pending.option_tick, pending.underlying_tick) if greeks is not None else None
    )
    if greeks is not None:
        option.theo_delta, option.theo_gamma, option.theo_theta, option.theo_vega = greeks
    option.calculate_pos_greeks()
    chain = getattr(option, "chain", None)
    if chain is not None:
        chain.calculate_pos_greeks()
    portfolio = getattr(option, "portfolio", None)
    if portfolio is not None:
        portfolio.calculate_pos_greeks()
    return True
