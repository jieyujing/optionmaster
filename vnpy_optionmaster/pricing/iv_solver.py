"""Auditable, full-precision implied-volatility inversion for Black models.

The legacy float API remains in each pricing model. Worker and risk code consume
IVResult; a failure must not be interpreted as a zero-volatility position.
"""
from collections.abc import Callable
from dataclasses import dataclass
from enum import Enum
from math import isfinite


class IVStatus(str, Enum):
    SUCCESS = "success"
    INVALID_INPUT = "invalid_input"
    NO_SOLUTION = "no_solution"
    NUMERICAL_FAILURE = "numerical_failure"
    MAX_ITERATIONS = "max_iterations"


@dataclass(frozen=True)
class IVResult:
    volatility: float | None
    status: IVStatus
    residual: float | None = None
    iterations: int = 0

    @property
    def valid(self) -> bool:
        return (
            self.status is IVStatus.SUCCESS
            and self.volatility is not None
            and isfinite(self.volatility)
            and self.volatility > 0
            and self.residual is not None
            and isfinite(self.residual)
        )


def solve_black_iv(
    market_price: float,
    spot: float,
    strike: float,
    rate: float,
    expiry: float,
    cp: int,
    *,
    price_function: Callable[[float, float, float, float, float, int], float],
    vega_function: Callable[[float, float, float, float, float], float],
    lower_price: float,
    upper_price: float,
    price_vega_function: (
        Callable[[float, float, float, float, float, int], tuple[float, float]] | None
    ) = None,
    min_vol: float = 1e-8,
    max_vol: float = 10.0,
    max_iterations: int = 100,
    vol_tolerance: float = 1e-10,
    absolute_price_tolerance: float = 1e-8,
    relative_price_tolerance: float = 1e-7,
    min_vega: float = 1e-12,
) -> IVResult:
    """Use guarded Newton within a price-bracket and bisection fallback.

    The numerical result is never rounded here. All model-specific bounds
    are supplied by the owning pricing module.
    """
    numbers = (
        market_price, spot, strike, rate, expiry,
        lower_price, upper_price, min_vol, max_vol,
        vol_tolerance, absolute_price_tolerance,
        relative_price_tolerance, min_vega,
    )
    if (
        not all(isfinite(x) for x in numbers)
        or cp not in (-1, 1)
        or market_price <= 0 or spot <= 0 or strike <= 0 or expiry <= 0
        or min_vol <= 0 or max_vol <= min_vol or max_iterations <= 0
        or vol_tolerance <= 0 or absolute_price_tolerance <= 0
        or relative_price_tolerance < 0 or min_vega < 0
    ):
        return IVResult(None, IVStatus.INVALID_INPUT)
    if not lower_price < market_price < upper_price:
        return IVResult(None, IVStatus.NO_SOLUTION)

    price_tolerance = max(
        absolute_price_tolerance, market_price * relative_price_tolerance
    )

    def difference(volatility: float) -> float:
        return price_function(spot, strike, rate, expiry, volatility, cp) - market_price

    try:
        f_lower = difference(min_vol)
        f_upper = difference(max_vol)
        if (not isfinite(f_lower) or not isfinite(f_upper)
                or f_lower >= 0 or f_upper < 0):
            return IVResult(None, IVStatus.NO_SOLUTION)

        left = min_vol
        right = max_vol
        # Start near liquid-market volatility instead of the 1000%-vol bracket midpoint.
        current = min(max(0.3, left + vol_tolerance), right - vol_tolerance)
        # Newton steps are only trusted away from the edges; otherwise halve.
        for iteration in range(1, max_iterations + 1):
            # Model-specific scalar kernels can reuse d1/sqrt(t) for price and
            # vega. Bracketing and *all* original acceptance gates stay intact.
            if price_vega_function is None:
                err = difference(current)
                if not isfinite(err):
                    return IVResult(None, IVStatus.NUMERICAL_FAILURE, iterations=iteration)
                vega = vega_function(spot, strike, rate, expiry, current)
            else:
                model_price, vega = price_vega_function(
                    spot, strike, rate, expiry, current, cp,
                )
                err = model_price - market_price
                if not isfinite(err):
                    return IVResult(None, IVStatus.NUMERICAL_FAILURE, iterations=iteration)
            # Price agreement alone is insufficient for a low-vega option:
            # also bound the implied volatility error from the local slope.
            if abs(err) <= price_tolerance and isfinite(vega) and vega > min_vega:
                if abs(err) / vega <= max(vol_tolerance * 10, 1e-8):
                    return IVResult(current, IVStatus.SUCCESS, abs(err), iteration)
            if err < 0:
                left = current
            else:
                right = current

            proposed = current - err / vega if isfinite(vega) and vega > min_vega else float("nan")
            guard = max(vol_tolerance, min((right - left) * 0.05, 0.05))
            if not isfinite(proposed) or not left + guard < proposed < right - guard:
                proposed = (left + right) / 2
            current = proposed
            if right - left < vol_tolerance:
                break
    except (ValueError, OverflowError, ZeroDivisionError, ArithmeticError):
        return IVResult(None, IVStatus.NUMERICAL_FAILURE)

    return IVResult(None, IVStatus.MAX_ITERATIONS, iterations=max_iterations)
