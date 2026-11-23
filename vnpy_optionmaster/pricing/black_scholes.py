"""Black-Scholes 期权定价。"""
from collections.abc import Callable

from scipy import stats, optimize
from scipy.special import ndtr
from vnpy_optionmaster.pricing.iv_solver import IVResult, IVStatus, solve_black_iv
from math import log, pow, sqrt, exp, isfinite, pi

cdf: Callable[..., float] = stats.norm.cdf
pdf: Callable[..., float] = stats.norm.pdf


def calculate_d1(
    s: float,
    k: float,
    r: float,
    t: float,
    v: float
) -> float:
    """计算期权 D1。"""
    d1: float = (log(s / k) + (r + 0.5 * pow(v, 2)) * t) / (v * sqrt(t))
    return d1


def calculate_price(
    s: float,
    k: float,
    r: float,
    t: float,
    v: float,
    cp: int,
    d1: float = 0.0
) -> float:
    """计算期权价格。"""
    # The expiry payoff is defined even when the model's d1 is not.
    if t <= 0:
        return max(0.0, cp * (s - k))

    # Return option space value if volatility not positive
    if v <= 0:
        return max(0, cp * (s - k * exp(-r * t)))

    if not d1:
        d1 = calculate_d1(s, k, r, t, v)
    d2: float = d1 - v * sqrt(t)

    price: float = cp * (s * cdf(cp * d1) - k * cdf(cp * d2) * exp(-r * t))
    return price


def calculate_delta(
    s: float,
    k: float,
    r: float,
    t: float,
    v: float,
    cp: int,
    d1: float = 0.0
) -> float:
    """计算期权 Delta。"""
    if t <= 0:
        return float(cp) if cp * (s - k) > 0 else 0.0
    if v <= 0:
        return float(cp) if cp * (s - k * exp(-r * t)) > 0 else 0.0

    if not d1:
        d1 = calculate_d1(s, k, r, t, v)

    delta: float = cp * cdf(cp * d1)
    return delta


def calculate_gamma(
    s: float,
    k: float,
    r: float,
    t: float,
    v: float,
    d1: float = 0.0
) -> float:
    """计算期权 Gamma。"""
    if t <= 0 or v <= 0:
        return 0

    if not d1:
        d1 = calculate_d1(s, k, r, t, v)

    gamma: float = pdf(d1) / (s * v * sqrt(t))
    return gamma


def calculate_theta(
    s: float,
    k: float,
    r: float,
    t: float,
    v: float,
    cp: int,
    d1: float = 0.0,
    annual_days: int = 240
) -> float:
    """计算期权 Theta。"""
    if t <= 0:
        return 0.0
    if v <= 0:
        # d/d(calendar time) of max(cp*(S-Ke^(-rT)), 0) at fixed S.
        discounted_strike: float = k * exp(-r * t)
        if cp * (s - discounted_strike) > 0:
            return -cp * r * discounted_strike
        return 0.0

    if not d1:
        d1 = calculate_d1(s, k, r, t, v)
    d2: float = d1 - v * sqrt(t)

    theta: float = -s * pdf(d1) * v / (2 * sqrt(t)) \
        - cp * r * k * exp(-r * t) * cdf(cp * d2)
    return theta


def calculate_vega(
    s: float,
    k: float,
    r: float,
    t: float,
    v: float,
    d1: float = 0.0
) -> float:
    """计算期权 Vega。"""
    if t <= 0 or v <= 0:
        return 0

    if not d1:
        d1 = calculate_d1(s, k, r, t, v)

    vega: float = s * pdf(d1) * sqrt(t)
    return vega


def calculate_greeks(
    s: float,
    k: float,
    r: float,
    t: float,
    v: float,
    cp: int
) -> tuple[float, float, float, float, float]:
    """计算期权价格和希腊值。"""
    if t <= 0 or v <= 0:
        return (calculate_price(s, k, r, t, v, cp),
                calculate_delta(s, k, r, t, v, cp), 0.0,
                calculate_theta(s, k, r, t, v, cp), 0.0)
    d1: float = calculate_d1(s, k, r, t, v)
    price: float = calculate_price(s, k, r, t, v, cp, d1)
    delta: float = calculate_delta(s, k, r, t, v, cp, d1)
    gamma: float = calculate_gamma(s, k, r, t, v, d1)
    theta: float = calculate_theta(s, k, r, t, v, cp, d1)
    vega: float = calculate_vega(s, k, r, t, v, d1)
    return price, delta, gamma, theta, vega


_INV_SQRT_TWO_PI = 1.0 / sqrt(2.0 * pi)


def _price_vega_for_iv(
    s: float, k: float, r: float, t: float, v: float, cp: int,
) -> tuple[float, float]:
    """Scalar-only structured IV kernel; public pricing/Greeks are unchanged."""
    root_t = sqrt(t)
    vol_time = v * root_t
    d1 = (log(s / k) + (r + 0.5 * v * v) * t) / vol_time
    d2 = d1 - vol_time
    discount = exp(-r * t)
    price = cp * (s * ndtr(cp * d1) - k * ndtr(cp * d2) * discount)
    vega = s * exp(-0.5 * d1 * d1) * root_t * _INV_SQRT_TWO_PI
    return float(price), vega


def calculate_impv_result(
    price: float,
    s: float,
    k: float,
    r: float,
    t: float,
    cp: int,
    *,
    max_iterations: int = 100,
) -> IVResult:
    """Full-precision Black-Scholes IV with structured failures."""
    if (not all(isfinite(x) for x in (price, s, k, r, t))
            or s <= 0 or k <= 0 or t <= 0 or cp not in (-1, 1)):
        return IVResult(None, IVStatus.INVALID_INPUT)
    try:
        discount = exp(-r * t)
        lower = max(0.0, cp * (s - k * discount))
        upper = s if cp == 1 else k * discount
    except OverflowError:
        return IVResult(None, IVStatus.INVALID_INPUT)
    return solve_black_iv(
        price, s, k, r, t, cp,
        price_function=calculate_price,
        vega_function=calculate_vega,
        price_vega_function=_price_vega_for_iv,
        lower_price=lower,
        upper_price=upper,
        max_iterations=max_iterations,
    )


def calculate_impv(
    price: float,
    s: float,
    k: float,
    r: float,
    t: float,
    cp: int
) -> float:
    """Original Brent compatibility route; Worker uses structured full precision."""
    if (not all(isfinite(x) for x in (price, s, k, r, t))
            or price <= 0 or s <= 0 or k <= 0 or t <= 0
            or cp not in (-1, 1)):
        return 0.0

    discount = exp(-r * t)
    lower = max(0.0, cp * (s - k * discount))
    upper = s if cp == 1 else k * discount
    if not lower < price < upper:
        return 0.0

    def residual(volatility: float) -> float:
        return calculate_price(s, k, r, t, volatility, cp) - price

    try:
        min_vol = 1e-8
        max_vol = 10.0
        f_min = residual(min_vol)
        f_max = residual(max_vol)
        if f_min > 0 or f_max < 0:
            return 0.0
        if abs(f_min) < 1e-12:
            return 0.0
        volatility = float(optimize.brentq(
            residual, min_vol, max_vol, xtol=1e-10, maxiter=100
        ))
        if abs(residual(volatility)) > max(1e-8, price * 1e-7):
            return 0.0
    except (ValueError, OverflowError, ZeroDivisionError):
        return 0.0

    return round(volatility, 4)
