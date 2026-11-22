"""Black-76 期权定价。"""
from collections.abc import Callable

from scipy import stats, optimize
from math import log, pow, sqrt, exp, isfinite

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
    d1: float = (log(s / k) + (0.5 * pow(v, 2)) * t) / (v * sqrt(t))
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
        return max(0, cp * (s - k)) * exp(-r * t)

    if not d1:
        d1 = calculate_d1(s, k, r, t, v)
    d2: float = d1 - v * sqrt(t)

    price: float = cp * (s * cdf(cp * d1) - k * cdf(cp * d2)) * exp(-r * t)
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
        return 0

    if not d1:
        d1 = calculate_d1(s, k, r, t, v)

    delta: float = cp * exp(-r * t) * cdf(cp * d1)
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

    gamma: float = exp(-r * t) * pdf(d1) / (s * v * sqrt(t))
    return gamma


def calculate_theta(
    s: float,
    k: float,
    r: float,
    t: float,
    v: float,
    cp: int,
    d1: float = 0.0
) -> float:
    """计算期权 Theta。"""
    if t <= 0 or v <= 0:
        return 0

    if not d1:
        d1 = calculate_d1(s, k, r, t, v)

    # Calendar-time theta at fixed futures price: the carry term is +r * price.
    theta: float = (-s * exp(-r * t) * pdf(d1) * v / (2 * sqrt(t))
                    + r * calculate_price(s, k, r, t, v, cp, d1))
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

    vega: float = s * exp(-r * t) * pdf(d1) * sqrt(t)
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
                calculate_delta(s, k, r, t, v, cp), 0.0, 0.0, 0.0)
    d1: float = calculate_d1(s, k, r, t, v)
    price: float = calculate_price(s, k, r, t, v, cp, d1)
    delta: float = calculate_delta(s, k, r, t, v, cp, d1)
    gamma: float = calculate_gamma(s, k, r, t, v, d1)
    theta: float = calculate_theta(s, k, r, t, v, cp, d1)
    vega: float = calculate_vega(s, k, r, t, v, d1)
    return price, delta, gamma, theta, vega


def calculate_impv(
    price: float,
    s: float,
    k: float,
    r: float,
    t: float,
    cp: int
) -> float:
    """反解隐含波动率；无套利边界外或求根失败时返回 0。"""
    if (not all(isfinite(x) for x in (price, s, k, r, t))
            or price <= 0 or s <= 0 or k <= 0 or t <= 0
            or cp not in (-1, 1)):
        return 0.0

    discount: float = exp(-r * t)
    lower: float = max(0.0, cp * (s - k)) * discount
    upper: float = (s if cp == 1 else k) * discount
    if not lower < price < upper:
        return 0.0

    def residual(volatility: float) -> float:
        return calculate_price(s, k, r, t, volatility, cp) - price

    try:
        min_vol: float = 1e-8
        max_vol: float = 10.0
        f_min: float = residual(min_vol)
        f_max: float = residual(max_vol)
        if f_min > 0 or f_max < 0:
            return 0.0
        if abs(f_min) < 1e-12:
            return 0.0
        volatility: float = float(optimize.brentq(
            residual, min_vol, max_vol, xtol=1e-10, maxiter=100
        ))
        if abs(residual(volatility)) > max(1e-8, price * 1e-7):
            return 0.0
    except (ValueError, OverflowError, ZeroDivisionError):
        return 0.0

    return round(volatility, 4)
