from typing import Tuple
from vnpy_optionmaster.pricing import binomial_tree as python_model
import numpy as np

cimport numpy as np
cimport cython

cdef extern from "math.h" nogil:
    double exp(double)
    double sqrt(double)
    double pow(double, double)
    double fmax(double, double)
    double fabs(double)


DEFAULT_STEP = 15


cdef tuple generate_tree(
    double f,
    double k,
    double r,
    double t,
    double v,
    int cp,
    int n
):
    """Generate binomial tree for pricing American option."""
    cdef double dt = t / n
    cdef double u = exp(v * sqrt(dt))
    cdef double d = 1 / u
    cdef double a = 1
    
    cdef int tree_size = n + 1
    cdef np.ndarray[np.double_t, ndim = 2] underlying_tree = np.zeros((tree_size, tree_size))
    cdef np.ndarray[np.double_t, ndim = 2] option_tree = np.zeros((tree_size, tree_size))

    cdef int i, j

    # Calculate risk neutral probability
    cdef double p = (a - d) / (u - d)
    cdef double p1 = p / a
    cdef double p2 = (1 - p) / a
    cdef double discount = exp(-r * dt)

    # Calculate underlying price tree
    underlying_tree[0, 0] = f

    for i in range(1, n + 1):
        underlying_tree[0, i] = underlying_tree[0, i - 1] * u
        for j in range(1, n + 1):
            underlying_tree[j, i] = underlying_tree[j - 1, i - 1] * d

    # Calculate option price tree
    for j in range(n + 1):
        option_tree[j, n] = max(0, cp * (underlying_tree[j, n] - k))

    for i in range(n - 1, -1, -1):
        for j in range(i + 1):
            option_tree[j, i] = max(
                (p1 * option_tree[j, i + 1] + p2 * option_tree[j + 1, i + 1]) * discount,
                cp * (underlying_tree[j, i] - k),
                0
            )

    # Return both trees
    return option_tree, underlying_tree


def calculate_price(
    double f,
    double k,
    double r,
    double t,
    double v,
    int cp,
    int n = DEFAULT_STEP
) -> float:
    """Calculate option price"""
    if t <= 0 or v <= 0:
        return max(0.0, cp * (f - k))
    option_tree, underlying_tree = generate_tree(f, k, r, t, v, cp, n)
    return option_tree[0, 0]


def calculate_delta(
    double f,
    double k,
    double r,
    double t,
    double v,
    int cp,
    int n = DEFAULT_STEP
) -> float:
    """Calculate option delta"""
    if t <= 0 or v <= 0:
        return float(cp) if cp * (f - k) > 0 else 0.0
    cdef double option_price_change, underlying_price_change
    cdef _delta, delta

    option_tree, underlying_tree = generate_tree(f, k, r, t, v, cp, n)
    
    option_price_change = option_tree[0, 1] - option_tree[1, 1]
    underlying_price_change = underlying_tree[0, 1] - underlying_tree[1, 1]

    delta = option_price_change / underlying_price_change
    return delta

def calculate_gamma(
    double f,
    double k,
    double r,
    double t,
    double v,
    int cp,
    int n = DEFAULT_STEP
) -> float:
    """Calculate option gamma"""
    if t <= 0 or v <= 0:
        return 0.0
    cdef double gamma_delta_1, gamma_delta_2
    cdef double _gamma, gamma

    option_tree, underlying_tree = generate_tree(f, k, r, t, v, cp, n)

    gamma_delta_1 = (option_tree[0, 2] - option_tree[1, 2]) / \
        (underlying_tree[0, 2] - underlying_tree[1, 2])
    gamma_delta_2 = (option_tree[1, 2] - option_tree[2, 2]) / \
        (underlying_tree[1, 2] - underlying_tree[2, 2])

    gamma = (gamma_delta_1 - gamma_delta_2) / \
        (0.5 * (underlying_tree[0, 2] - underlying_tree[2, 2]))
    return gamma


def calculate_theta(
    double f,
    double k,
    double r,
    double t,
    double v,
    int cp,
    int n = DEFAULT_STEP
) -> float:
    """Calcualte option theta"""
    if t <= 0 or v <= 0:
        return 0.0
    cdef double dt, theta

    option_tree, underlying_tree = generate_tree(f, k, r, t, v, cp, n)

    dt = t / n

    theta = (option_tree[1, 2] - option_tree[0, 0]) / (2 * dt)
    return theta


def calculate_vega(
    double f,
    double k,
    double r,
    double t,
    double v,
    int cp,
    int n = DEFAULT_STEP
) -> float:
    """Calculate option vega"""
    if t <= 0 or v <= 0:
        return 0.0
    cdef double price_1 = calculate_price(f, k, r, t, v, cp, n)
    cdef double price_2 = calculate_price(f, k, r, t, v * 1.001, cp, n)
    cdef double vega = (price_2 - price_1) / (v * 0.001)
    return vega


def calculate_greeks(
    double f,
    double k,
    double r,
    double t,
    double v,
    int cp,
    int n = DEFAULT_STEP
) -> Tuple[float, float, float, float, float]:
    """Calculate option price and greeks"""
    if t <= 0 or v <= 0:
        return (calculate_price(f, k, r, t, v, cp, n),
                calculate_delta(f, k, r, t, v, cp, n), 0.0, 0.0, 0.0)
    cdef double dt = t / n
    cdef double price, delta, gamma, vega, theta
    cdef double _delta, _gamma
    cdef double option_price_change, underlying_price_change
    cdef double gamma_delta_1, gamma_delta_2

    option_tree, underlying_tree = generate_tree(f, k, r, t, v, cp, n)
    option_tree_vega, underlying_tree_vega = generate_tree(f, k, r, t, v * 1.001, cp, n)

    # Price
    price = option_tree[0, 0]

    # Delta
    option_price_change = option_tree[0, 1] - option_tree[1, 1]
    underlying_price_change = underlying_tree[0, 1] - underlying_tree[1, 1]

    delta = option_price_change / underlying_price_change

    # Gamma
    gamma_delta_1 = (option_tree[0, 2] - option_tree[1, 2]) / \
        (underlying_tree[0, 2] - underlying_tree[1, 2])
    gamma_delta_2 = (option_tree[1, 2] - option_tree[2, 2]) / \
        (underlying_tree[1, 2] - underlying_tree[2, 2])

    gamma = (gamma_delta_1 - gamma_delta_2) / \
        (0.5 * (underlying_tree[0, 2] - underlying_tree[2, 2]))

    # Theta
    theta = (option_tree[1, 2] - option_tree[0, 0]) / (2 * dt)

    # Vega
    vega = (option_tree_vega[0, 0] - option_tree[0, 0]) / (0.001 * v)

    return price, delta, gamma, theta, vega


def calculate_impv(
    double price,
    double f,
    double k,
    double r,
    double t,
    int cp,
    int n = DEFAULT_STEP
) -> float:
    """Keep implied-volatility boundary handling identical to the Python model."""
    return python_model.calculate_impv(price, f, k, r, t, cp, n)