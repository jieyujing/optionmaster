"""Structured IV scalar hot-path must preserve the original numerical contract.

No live gateway, order handling or official trading sessions are accessed.
"""
from math import exp, isclose

import pytest

from vnpy_optionmaster.pricing import black_76, black_scholes
from vnpy_optionmaster.pricing.iv_solver import IVStatus, solve_black_iv


@pytest.mark.parametrize("model", [black_76, black_scholes])
@pytest.mark.parametrize("cp", [-1, 1])
@pytest.mark.parametrize("strike", [80., 95., 100., 105., 120.])
@pytest.mark.parametrize("expiry", [0.04, 0.5, 1.0])
@pytest.mark.parametrize("vol", [0.1, 0.3, 0.75])
def test_scalar_fast_price_and_vega_match_public_model(model, cp, strike, expiry, vol):
    price, vega = model._price_vega_for_iv(
        100., strike, 0.02, expiry, vol, cp,
    )
    expected_price = model.calculate_price(100., strike, 0.02, expiry, vol, cp)
    expected_vega = model.calculate_vega(100., strike, 0.02, expiry, vol)
    assert isclose(price, expected_price, rel_tol=2e-13, abs_tol=1e-11)
    assert isclose(vega, expected_vega, rel_tol=2e-13, abs_tol=1e-11)


@pytest.mark.parametrize("model", [black_76, black_scholes])
@pytest.mark.parametrize("cp", [-1, 1])
@pytest.mark.parametrize("strike", [90., 100., 110.])
@pytest.mark.parametrize("vol", [0.13, 0.4, 0.8])
def test_optimized_iv_retains_reference_solver_acceptance(model, cp, strike, vol):
    s, r, t = 100., 0.02, 0.5
    market = model.calculate_price(s, strike, r, t, vol, cp)
    discount = exp(-r * t)
    if model is black_76:
        lower = max(0., cp * (s - strike)) * discount
        upper = (s if cp == 1 else strike) * discount
    else:
        lower = max(0., cp * (s - strike * discount))
        upper = s if cp == 1 else strike * discount
    reference = solve_black_iv(
        market, s, strike, r, t, cp,
        price_function=model.calculate_price,
        vega_function=model.calculate_vega,
        lower_price=lower, upper_price=upper,
    )
    optimized = model.calculate_impv_result(market, s, strike, r, t, cp)
    assert optimized.status == reference.status
    assert optimized.valid == reference.valid
    if reference.status is IVStatus.SUCCESS:
        assert optimized.volatility is not None and reference.volatility is not None
        assert isclose(optimized.volatility, reference.volatility, rel_tol=1e-8, abs_tol=1e-8)
        assert optimized.residual is not None
        assert optimized.residual <= max(1e-8, market * 1e-7)


@pytest.mark.parametrize("model", [black_76, black_scholes])
def test_fast_solver_keeps_grid_statuses_iterations_and_iv_values(model):
    """Including cases with no solution and cases that exhaust iteration budget."""
    for cp in (-1, 1):
        for k in (85., 95., 100., 105., 115.):
            for t in (0.04, 0.25, 1.0):
                for vol in (0.1, 0.3, 0.65):
                    s, r = 100., 0.02
                    market = model.calculate_price(s, k, r, t, vol, cp)
                    discount = exp(-r * t)
                    if model is black_76:
                        lower = max(0., cp * (s - k)) * discount
                        upper = (s if cp == 1 else k) * discount
                    else:
                        lower = max(0., cp * (s - k * discount))
                        upper = s if cp == 1 else k * discount
                    reference = solve_black_iv(
                        market, s, k, r, t, cp,
                        price_function=model.calculate_price,
                        vega_function=model.calculate_vega,
                        lower_price=lower,
                        upper_price=upper,
                    )
                    optimized = model.calculate_impv_result(market, s, k, r, t, cp)
                    assert optimized.status is reference.status
                    assert optimized.valid == reference.valid
                    assert optimized.iterations == reference.iterations
                    if reference.valid:
                        assert reference.volatility is not None
                        assert optimized.volatility is not None
                        assert isclose(optimized.volatility, reference.volatility, rel_tol=1e-9, abs_tol=1e-9)


@pytest.mark.parametrize("model", [black_76, black_scholes])
def test_optimized_solver_preserves_bounded_fail_closed(model):
    valid_price = model.calculate_price(100., 100., 0.02, 0.5, 0.3, 1)
    assert model.calculate_impv_result(valid_price, 100., 100., 0.02, 0.5, 1, max_iterations=0).status is IVStatus.INVALID_INPUT
    assert not model.calculate_impv_result(2000., 100., 100., 0.02, 0.5, 1).valid
