"""Research-only NumPy American futures-option CRR lattice.

Time-major rows preserve the scalar reference's multiply order (all up
moves followed by down moves). Do not substitute powers, reassociate the
continuation expression or change the exercise/Greek/volatility definitions.
The desktop/live pricing module is not imported or modified here.
"""
from math import exp, sqrt

import numpy as np

DEFAULT_STEP = 15


def _lattice(
    f: float, k: float, r: float, t: float, v: float, cp: int, n: int,
) -> tuple[np.ndarray, np.ndarray]:
    dt = t / n
    u = exp(v * sqrt(dt))
    d = 1 / u
    p = (1 - d) / (u - d)
    q = 1 - p
    discount = exp(-r * dt)

    # Time-major storage: one contiguous slice per active layer.
    underlying = np.empty((n + 1, n + 1), dtype=np.float64)
    underlying[0, 0] = f
    for i in range(1, n + 1):
        underlying[i, 0] = underlying[i - 1, 0] * u
        underlying[i, 1:i + 1] = underlying[i - 1, :i] * d

    # Keep the first three layers for exactly the reference Greeks. A
    # rolling vector contains the next layer during backward induction.
    layers = np.empty((3, 3), dtype=np.float64)
    intrinsic = cp * (underlying[n, :n + 1] - k)
    values = np.where(intrinsic > 0, intrinsic, 0.0)
    if n <= 2:
        layers[n, :n + 1] = values
    for i in range(n - 1, -1, -1):
        continuation = (p * values[:i + 1] + q * values[1:i + 2]) * discount
        intrinsic = cp * (underlying[i, :i + 1] - k)
        # Match Python max(continuation, intrinsic, 0), including ties
        # and the ordering of unordered comparisons, rather than fmax.
        values = np.where(intrinsic > continuation, intrinsic, continuation)
        values = np.where(0 > values, 0.0, values)
        if i <= 2:
            layers[i, :i + 1] = values
    return layers, underlying


def calculate_price(
    f: float, k: float, r: float, t: float, v: float, cp: int, n: int = DEFAULT_STEP,
) -> float:
    if t <= 0 or v <= 0:
        return max(0.0, cp * (f - k))
    layers, _ = _lattice(f, k, r, t, v, cp, n)
    return float(layers[0, 0])


def calculate_vega(
    f: float, k: float, r: float, t: float, v: float, cp: int, n: int = DEFAULT_STEP,
) -> float:
    if t <= 0 or v <= 0:
        return 0.0
    price = calculate_price(f, k, r, t, v, cp, n)
    bumped = calculate_price(f, k, r, t, v * 1.001, cp, n)
    return (bumped - price) / (v * 0.001)


def calculate_greeks(
    f: float, k: float, r: float, t: float, v: float, cp: int, n: int = DEFAULT_STEP,
) -> tuple[float, float, float, float, float]:
    if t <= 0 or v <= 0:
        return max(0.0, cp * (f - k)), float(cp) if cp * (f - k) > 0 else 0.0, 0.0, 0.0, 0.0
    dt = t / n
    values, underlying = _lattice(f, k, r, t, v, cp, n)
    price = values[0, 0]
    delta = (values[1, 0] - values[1, 1]) / (underlying[1, 0] - underlying[1, 1])
    delta_1 = (values[2, 0] - values[2, 1]) / (underlying[2, 0] - underlying[2, 1])
    delta_2 = (values[2, 1] - values[2, 2]) / (underlying[2, 1] - underlying[2, 2])
    gamma = (delta_1 - delta_2) / (0.5 * (underlying[2, 0] - underlying[2, 2]))
    theta = (values[2, 1] - price) / (2 * dt)
    bumped = calculate_price(f, k, r, t, v * 1.001, cp, n)
    vega = (bumped - price) / (0.001 * v)
    return float(price), float(delta), float(gamma), float(theta), float(vega)
