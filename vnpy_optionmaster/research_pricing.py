"""Model selection and structured IV inversion for offline research only.

Existing pricing labels and live engines are not modified. Black model IV uses
the project's full-precision structured solver. The binomial research path
uses a bracketed solve instead of the legacy rounded/zero-on-failure API.
"""
from collections import OrderedDict
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from enum import Enum
from math import isfinite
from typing import TypeVar

from numpy import float64
from scipy.optimize import brentq

from vnpy_optionmaster import research_crr as _binomial_kernel
from vnpy_optionmaster.pricing import black_76, black_scholes
from vnpy_optionmaster.pricing.iv_solver import IVResult, IVStatus


class ResearchPricingModel(str, Enum):
    BLACK_76 = "Black-76 欧式期货期权"
    BLACK_SCHOLES = "Black-Scholes 欧式股票期权"
    BINOMIAL_TREE = "二叉树 美式期货期权"


# Exact, bounded, request-local reuse. No price/IV rounding, no market data,
# no cache shared between calculations and no change to live pricing.
_Greeks = tuple[float, float, float, float, float]
_Key = tuple[ResearchPricingModel, str, str, str, str, str, int, int]
_T = TypeVar("_T")
_MAX_MEMO_ENTRIES = 4096


class _PricingMemo:
    def __init__(self) -> None:
        self.greeks: OrderedDict[_Key, _Greeks] = OrderedDict()
        self.iv: OrderedDict[_Key, IVResult] = OrderedDict()


_MEMO: ContextVar[_PricingMemo | None] = ContextVar("research_pricing_memo", default=None)


@contextmanager
def research_pricing_scope() -> Iterator[None]:
    """Create an independent memo for one synchronous grid evaluation."""
    token = _MEMO.set(_PricingMemo())
    try:
        yield
    finally:
        _MEMO.reset(token)


def _key(
    model: ResearchPricingModel, a: float, b: float, c: float, d: float, e: float,
    option_type: int, tree_steps: int,
) -> _Key | None:
    # Use exact float encodings, including signed zero. Safe integer/float64
    # values coalesce only when exactly representable. Unusual numeric
    # subclasses or integers outside this range take the original path.
    values = (a, b, c, d, e)
    if (
        type(model) is not ResearchPricingModel
        or type(option_type) is not int or type(tree_steps) is not int
        or any(type(x) not in (int, float, float64) for x in values)
        or any(type(x) is int and abs(x) > 2**53 for x in values)
        or not all(isfinite(x) for x in values)
    ):
        return None
    return (model, float(a).hex(), float(b).hex(), float(c).hex(), float(d).hex(),
            float(e).hex(), option_type, tree_steps)


def _cached(
    cache: OrderedDict[_Key, _T], key: _Key | None, calculate: Callable[[], _T],
    usable: Callable[[_T], bool],
) -> _T:
    if key is None:
        return calculate()
    if key in cache:
        cache.move_to_end(key)
        return cache[key]
    result = calculate()
    if usable(result):
        cache[key] = result
        if len(cache) > _MAX_MEMO_ENTRIES:
            cache.popitem(last=False)
    return result


def research_greeks(
    model: ResearchPricingModel, underlying: float, strike: float, rate: float,
    maturity: float, volatility: float, option_type: int, tree_steps: int,
) -> _Greeks:
    """Original full price and Greeks; reuse only exact inputs within a grid."""
    args = (model, underlying, strike, rate, maturity, volatility, option_type, tree_steps)
    memo = _MEMO.get()
    if memo is None:
        return _calculate_greeks(*args)
    return _cached(memo.greeks, _key(*args), lambda: _calculate_greeks(*args),
                   lambda value: all(isfinite(x) for x in value))


def research_iv(
    model: ResearchPricingModel, reference_option_price: float, underlying: float,
    strike: float, rate: float, maturity: float, option_type: int, tree_steps: int,
) -> IVResult:
    """Original solver and status; only successful exact inversions are reused."""
    args = (model, reference_option_price, underlying, strike, rate, maturity, option_type, tree_steps)
    memo = _MEMO.get()
    if memo is None:
        return _calculate_iv(*args)
    return _cached(memo.iv, _key(*args), lambda: _calculate_iv(*args), lambda value: value.valid)


def _calculate_greeks(
    model: ResearchPricingModel,
    underlying: float,
    strike: float,
    rate: float,
    maturity: float,
    volatility: float,
    option_type: int,
    tree_steps: int,
) -> tuple[float, float, float, float, float]:
    """Price, delta, gamma, annual theta and vega per 1.0 sigma unit."""
    args = (underlying, strike, rate, maturity, volatility, option_type)
    if model is ResearchPricingModel.BLACK_76:
        return black_76.calculate_greeks(*args)
    if model is ResearchPricingModel.BLACK_SCHOLES:
        return black_scholes.calculate_greeks(*args)
    return _binomial_kernel.calculate_greeks(*args, tree_steps)


def _calculate_iv(
    model: ResearchPricingModel,
    reference_option_price: float,
    underlying: float,
    strike: float,
    rate: float,
    maturity: float,
    option_type: int,
    tree_steps: int,
) -> IVResult:
    """Invert an observed option reference price without a zero-on-failure fallback."""
    args = (reference_option_price, underlying, strike, rate, maturity, option_type)
    if model is ResearchPricingModel.BLACK_76:
        return black_76.calculate_impv_result(*args)
    if model is ResearchPricingModel.BLACK_SCHOLES:
        return black_scholes.calculate_impv_result(*args)
    return _binomial_research_iv(*args, tree_steps)


def _binomial_research_iv(
    market_price: float,
    underlying: float,
    strike: float,
    rate: float,
    maturity: float,
    option_type: int,
    steps: int,
) -> IVResult:
    """Bracketed American futures-option inversion; no implied zero/rounded IV.

    Search interval (1e-6, 5.0) is deliberate; outside it the research solver
    reports no reliable solution instead of extrapolating unboundedly.
    """
    if (
        not all(isfinite(x) for x in (market_price, underlying, strike, rate, maturity))
        or market_price <= 0
        or underlying <= 0
        or strike <= 0
        or maturity <= 0
        or option_type not in (-1, 1)
        or steps < 2
    ):
        return IVResult(None, IVStatus.INVALID_INPUT)

    tolerance = max(1e-8, market_price * 1e-7)
    min_vol = 1e-6
    max_vol = 5.0

    def error(sigma: float) -> float:
        return float(_binomial_kernel.calculate_price(
            underlying, strike, rate, maturity, sigma, option_type, steps,
        )) - market_price

    try:
        lower_error = error(min_vol)
        upper_error = error(max_vol)
        if (
            not isfinite(lower_error)
            or not isfinite(upper_error)
            or lower_error >= -tolerance
            or upper_error <= tolerance
        ):
            return IVResult(None, IVStatus.NO_SOLUTION)
        volatility, details = brentq(
            error, min_vol, max_vol, xtol=1e-11, maxiter=100, full_output=True,
        )
        if not details.converged:
            return IVResult(None, IVStatus.MAX_ITERATIONS, iterations=details.iterations)
        residual = abs(error(float(volatility)))
        vega = float(_binomial_kernel.calculate_vega(
            underlying, strike, rate, maturity, float(volatility), option_type, steps,
        ))
        if (
            not isfinite(residual)
            or residual > tolerance
            or not isfinite(vega)
            or vega <= 1e-10
            or residual / vega > 1e-5
        ):
            return IVResult(None, IVStatus.NUMERICAL_FAILURE, iterations=details.iterations)
        return IVResult(float(volatility), IVStatus.SUCCESS, residual, details.iterations)
    except (ValueError, OverflowError, ZeroDivisionError, ArithmeticError):
        return IVResult(None, IVStatus.NUMERICAL_FAILURE)
    except RuntimeError:
        return IVResult(None, IVStatus.MAX_ITERATIONS, iterations=100)
