import importlib.util
from math import erf, exp, log, sqrt
from pathlib import Path
from types import ModuleType

import pytest


_PRICING_DIR: Path = Path(__file__).resolve().parent.parent.joinpath("vnpy_optionmaster", "pricing")
_ABS_TOLERANCE: float = 1e-8


def _load_pricing(module_name: str) -> ModuleType:
    path: Path = _PRICING_DIR.joinpath(f"{module_name}.py")
    spec = importlib.util.spec_from_file_location(f"pricing_{module_name}", path)
    if spec is None or spec.loader is None:
        raise ImportError(module_name)
    module: ModuleType = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _norm_cdf(x: float) -> float:
    return 0.5 * (1.0 + erf(x / sqrt(2.0)))


def _black_scholes_call(s: float, k: float, r: float, t: float, v: float) -> tuple[float, float]:
    d1: float = (log(s / k) + (r + 0.5 * v * v) * t) / (v * sqrt(t))
    d2: float = d1 - v * sqrt(t)
    price: float = s * _norm_cdf(d1) - k * exp(-r * t) * _norm_cdf(d2)
    delta: float = _norm_cdf(d1)
    return price, delta


def _black_76_call(f: float, k: float, r: float, t: float, v: float) -> tuple[float, float]:
    d1: float = (log(f / k) + 0.5 * v * v * t) / (v * sqrt(t))
    d2: float = d1 - v * sqrt(t)
    discount: float = exp(-r * t)
    price: float = discount * (f * _norm_cdf(d1) - k * _norm_cdf(d2))
    delta: float = discount * _norm_cdf(d1)
    return price, delta


def _american_futures_call(
    f: float,
    k: float,
    r: float,
    t: float,
    v: float,
    n: int,
) -> tuple[float, float]:
    dt: float = t / n
    up: float = exp(v * sqrt(dt))
    down_factor: float = 1.0 / up
    probability: float = (1.0 - down_factor) / (up - down_factor)
    discount: float = exp(-r * dt)

    spot: list[list[float]] = [[0.0 for _ in range(n + 1)] for _ in range(n + 1)]
    option: list[list[float]] = [[0.0 for _ in range(n + 1)] for _ in range(n + 1)]
    spot[0][0] = f
    step: int
    for step in range(1, n + 1):
        spot[0][step] = spot[0][step - 1] * up
        down_moves: int
        for down_moves in range(1, step + 1):
            spot[down_moves][step] = spot[down_moves - 1][step - 1] * down_factor

    down_moves = 0
    for down_moves in range(n + 1):
        spot_payoff: float = spot[down_moves][n] - k
        option[down_moves][n] = max(0.0, spot_payoff)

    for step in range(n - 1, -1, -1):
        for down_moves in range(step + 1):
            hold: float = (
                probability * option[down_moves][step + 1]
                + (1.0 - probability) * option[down_moves + 1][step + 1]
            ) * discount
            exercise: float = max(0.0, spot[down_moves][step] - k)
            option[down_moves][step] = max(hold, exercise)

    delta: float = (option[0][1] - option[1][1]) / (spot[0][1] - spot[1][1])
    return option[0][0], delta


_BLACK_SCHOLES: ModuleType = _load_pricing("black_scholes")
_BLACK_76: ModuleType = _load_pricing("black_76")
_BINOMIAL_TREE: ModuleType = _load_pricing("binomial_tree")


class TestBlackScholes:
    def test_call_price_and_delta_match_closed_form(self) -> None:
        s: float = 42.0
        k: float = 40.0
        r: float = 0.1
        t: float = 0.5
        v: float = 0.2
        expected_price: float
        expected_delta: float
        expected_price, expected_delta = _black_scholes_call(s, k, r, t, v)

        price: float = _BLACK_SCHOLES.calculate_price(s, k, r, t, v, 1)
        delta: float = _BLACK_SCHOLES.calculate_delta(s, k, r, t, v, 1)
        greeks: tuple[float, float, float, float, float] = _BLACK_SCHOLES.calculate_greeks(
            s, k, r, t, v, 1
        )

        assert price == pytest.approx(expected_price, abs=_ABS_TOLERANCE)
        assert delta == pytest.approx(expected_delta, abs=_ABS_TOLERANCE)
        assert greeks[0] == pytest.approx(expected_price, abs=_ABS_TOLERANCE)
        assert greeks[1] == pytest.approx(expected_delta, abs=_ABS_TOLERANCE)


class TestBlack76:
    def test_call_price_and_delta_match_closed_form(self) -> None:
        f: float = 100.0
        k: float = 100.0
        r: float = 0.05
        t: float = 1.0
        v: float = 0.2
        expected_price: float
        expected_delta: float
        expected_price, expected_delta = _black_76_call(f, k, r, t, v)

        price: float = _BLACK_76.calculate_price(f, k, r, t, v, 1)
        delta: float = _BLACK_76.calculate_delta(f, k, r, t, v, 1)
        greeks: tuple[float, float, float, float, float] = _BLACK_76.calculate_greeks(
            f, k, r, t, v, 1
        )

        assert price == pytest.approx(expected_price, abs=_ABS_TOLERANCE)
        assert delta == pytest.approx(expected_delta, abs=_ABS_TOLERANCE)
        assert greeks[0] == pytest.approx(expected_price, abs=_ABS_TOLERANCE)
        assert greeks[1] == pytest.approx(expected_delta, abs=_ABS_TOLERANCE)


class TestBinomialTree:
    def test_call_price_and_delta_match_two_step_tree(self) -> None:
        f: float = 100.0
        k: float = 100.0
        r: float = 0.05
        t: float = 1.0
        v: float = 0.2
        n: int = 2
        expected_price: float
        expected_delta: float
        expected_price, expected_delta = _american_futures_call(f, k, r, t, v, n)

        price: float = _BINOMIAL_TREE.calculate_price(f, k, r, t, v, 1, n)
        delta: float = _BINOMIAL_TREE.calculate_delta(f, k, r, t, v, 1, n)
        greeks: tuple[float, float, float, float, float] = _BINOMIAL_TREE.calculate_greeks(
            f, k, r, t, v, 1, n
        )

        assert price == pytest.approx(expected_price, abs=_ABS_TOLERANCE)
        assert delta == pytest.approx(expected_delta, abs=_ABS_TOLERANCE)
        assert greeks[0] == pytest.approx(expected_price, abs=_ABS_TOLERANCE)
        assert greeks[1] == pytest.approx(expected_delta, abs=_ABS_TOLERANCE)


@pytest.mark.parametrize("model", [_BLACK_SCHOLES, _BLACK_76, _BINOMIAL_TREE])
@pytest.mark.parametrize("cp", [1, -1])
def test_expiry_boundary_is_intrinsic(model: ModuleType, cp: int) -> None:
    spot: float = 105.0
    strike: float = 100.0
    expected: float = max(0.0, cp * (spot - strike))
    assert model.calculate_price(spot, strike, 0.05, 0.0, 0.2, cp) == pytest.approx(expected)
    greeks = model.calculate_greeks(spot, strike, 0.05, 0.0, 0.2, cp)
    assert greeks[0] == pytest.approx(expected)
    assert all(abs(greek) <= 1.0 for greek in greeks[1:])


def test_black_76_theta_matches_finite_difference() -> None:
    f, k, r, t, v, cp = 100.0, 100.0, 0.05, 1.0, 0.2, 1
    dt = 1e-5
    expected = -(
        _BLACK_76.calculate_price(f, k, r, t + dt, v, cp)
        - _BLACK_76.calculate_price(f, k, r, t - dt, v, cp)
    ) / (2 * dt)
    theta = _BLACK_76.calculate_theta(f, k, r, t, v, cp)
    assert theta == pytest.approx(expected, abs=1e-5)


@pytest.mark.parametrize("model", [_BLACK_SCHOLES, _BLACK_76])
def test_implied_volatility_round_trip_deep_otm(model: ModuleType) -> None:
    f, k, r, t, sigma, cp = 100.0, 140.0, 0.02, 0.05, 0.6, 1
    fair_price = model.calculate_price(f, k, r, t, sigma, cp)
    implied = model.calculate_impv(fair_price, f, k, r, t, cp)
    assert implied == pytest.approx(sigma, abs=0.0001)


@pytest.mark.parametrize("model", [_BLACK_SCHOLES, _BLACK_76, _BINOMIAL_TREE])
def test_impv_at_expiry_has_no_volatility(model: ModuleType) -> None:
    assert model.calculate_impv(1.0, 100.0, 100.0, 0.02, 0.0, 1) == 0.0


@pytest.mark.parametrize(
    ("model", "expected_scale"),
    [(_BLACK_76, exp(-0.05 * 0.5)), (_BLACK_SCHOLES, 1.0)],
)
@pytest.mark.parametrize(
    ("spot", "cp", "expected_sign"),
    [(105.0, 1, 1.0), (95.0, -1, -1.0), (95.0, 1, 0.0), (105.0, -1, 0.0)],
)
def test_zero_volatility_itm_delta(
    model: ModuleType, expected_scale: float, spot: float, cp: int, expected_sign: float
) -> None:
    expected = expected_scale * expected_sign
    assert model.calculate_delta(spot, 100.0, 0.05, 0.5, 0.0, cp) == pytest.approx(expected)
    greeks = model.calculate_greeks(spot, 100.0, 0.05, 0.5, 0.0, cp)
    assert greeks[1] == pytest.approx(expected)


@pytest.mark.parametrize(
    ("model", "spot", "cp"),
    [
        (_BLACK_76, 110.0, 1),
        (_BLACK_76, 90.0, -1),
        (_BLACK_SCHOLES, 110.0, 1),
        (_BLACK_SCHOLES, 90.0, -1),
    ],
)
def test_zero_volatility_theta_matches_calendar_time_finite_difference(
    model: ModuleType, spot: float, cp: int
) -> None:
    """At positive time, deterministic discounted payoff still has time sensitivity."""
    strike, rate, maturity, sigma = 100.0, 0.05, 0.5, 0.0
    dt = 1e-5
    expected = -(
        model.calculate_price(spot, strike, rate, maturity + dt, sigma, cp)
        - model.calculate_price(spot, strike, rate, maturity - dt, sigma, cp)
    ) / (2.0 * dt)
    assert abs(expected) > 0.01
    theta = model.calculate_theta(spot, strike, rate, maturity, sigma, cp)
    assert theta == pytest.approx(expected, abs=1e-7)
    assert model.calculate_greeks(spot, strike, rate, maturity, sigma, cp)[3] == pytest.approx(expected, abs=1e-7)
