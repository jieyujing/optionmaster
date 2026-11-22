from typing import Tuple
from vnpy_optionmaster.pricing import black_76 as python_model

cdef extern from "math.h" nogil:
    double exp(double)
    double sqrt(double)
    double pow(double, double)
    double log(double)
    double erf(double)
    double fabs(double)
    double fmax(double, double)


cdef double cdf(double x):
    return 0.5 * (1 + erf(x / sqrt(2.0)))


cdef double pdf(double x):
    # 1 / sqrt(2 * 3.1416) = 0.3989422804014327
    return exp(- pow(x, 2) * 0.5) * 0.3989422804014327


cdef double calculate_d1(double s, double k, double r, double t, double v):
    """Calculate option D1 value"""
    return (log(s / k) + (0.5 * pow(v, 2)) * t) / (v * sqrt(t))


def calculate_price(
    double s,
    double k,
    double r,
    double t,
    double v,
    int cp,
    double d1 = 0.0
) -> float:
    """Calculate option price"""
    cdef double d2, price

    if t <= 0:
        return max(0.0, cp * (s - k))

    # Return discounted intrinsic when volatility is nonpositive.
    if v <= 0:
        return max(0.0, cp * (s - k)) * exp(-r * t)

    if not d1:
        d1 = calculate_d1(s, k, r, t, v)
    d2 = d1 - v * sqrt(t)

    price = cp * (s * cdf(cp * d1) - k * cdf(cp * d2)) * exp(-r * t)
    return price


def calculate_delta(
    double s,
    double k,
    double r,
    double t,
    double v,
    int cp,
    double d1 = 0.0
) -> float:
    """Calculate option delta"""
    cdef _delta, delta

    if t <= 0:
        return float(cp) if cp * (s - k) > 0 else 0.0
    if v <= 0:
        return float(cp) * exp(-r * t) if cp * (s - k) > 0 else 0.0

    if not d1:
        d1 = calculate_d1(s, k, r, t, v)

    delta: float = cp * exp(-r * t) * cdf(cp * d1)
    return delta


def calculate_gamma(
    double s,
    double k,
    double r,
    double t,
    double v,
    double d1 = 0.0
) -> float:
    """Calculate option gamma"""
    cdef _gamma, gamma

    if v <= 0 or s <= 0 or t<= 0:
        return 0

    if not d1:
        d1 = calculate_d1(s, k, r, t, v)

    gamma = exp(-r * t) * pdf(d1) / (s * v * sqrt(t))
    return gamma


def calculate_theta(
    double s,
    double k,
    double r,
    double t,
    double v,
    int cp,
    double d1 = 0.0
) -> float:
    """Calculate option theta"""
    cdef double theta

    if t <= 0:
        return 0.0
    if v <= 0:
        return r * calculate_price(s, k, r, t, v, cp)

    if not d1:
        d1 = calculate_d1(s, k, r, t, v)
    theta = (-s * exp(-r * t) * pdf(d1) * v / (2 * sqrt(t))
             + r * calculate_price(s, k, r, t, v, cp, d1))
    return theta


def calculate_vega(
    double s,
    double k,
    double r,
    double t,
    double v,
    double d1 = 0.0
) -> float:
    """Calculate option vega"""
    cdef double vega

    if t <= 0 or v <= 0:
        return 0.0

    if not d1:
        d1 = calculate_d1(s, k, r, t, v)

    vega: float = s * exp(-r * t) * pdf(d1) * sqrt(t)

    return vega


def calculate_greeks(
    double s,
    double k,
    double r,
    double t,
    double v,
    int cp
) -> Tuple[float, float, float, float, float]:
    """Calculate option price and greeks"""
    cdef double d1, price, delta, gamma, theta, vega

    if t <= 0 or v <= 0:
        return (calculate_price(s, k, r, t, v, cp),
                calculate_delta(s, k, r, t, v, cp), 0.0,
                calculate_theta(s, k, r, t, v, cp), 0.0)

    d1 = calculate_d1(s, k, r, t, v)
    
    price = calculate_price(s, k, r, t, v, cp, d1)
    delta = calculate_delta(s, k, r, t, v, cp, d1)
    gamma = calculate_gamma(s, k, r, t, v, d1)
    theta = calculate_theta(s, k, r, t, v, cp, d1)
    vega = calculate_vega(s, k, r, t, v, d1)
    
    return price, delta, gamma, theta, vega


def calculate_impv(
    double price,
    double s,
    double k,
    double r,
    double t,
    int cp
) -> float:
    """Use the validated, bracketed implied-volatility solver shared with Python."""
    return python_model.calculate_impv(price, s, k, r, t, cp)