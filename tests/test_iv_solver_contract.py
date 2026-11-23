"""Structured Black-model implied-volatility contract; no CTP connections."""
from math import isfinite

import pytest

from vnpy_optionmaster.pricing import black_76, black_scholes
from vnpy_optionmaster.pricing.iv_solver import IVResult, IVStatus


@pytest.mark.parametrize("model", [black_76, black_scholes])
@pytest.mark.parametrize("cp", [-1, 1])
@pytest.mark.parametrize("volatility", [0.05, 0.2, 0.75])
def test_structured_iv_preserves_full_precision_and_reprices(model, cp, volatility):
    s, k, r, t = 100.0, 103.0, 0.02, 0.8
    market = model.calculate_price(s, k, r, t, volatility, cp)
    result = model.calculate_impv_result(market, s, k, r, t, cp)
    assert isinstance(result, IVResult)
    assert result.status is IVStatus.SUCCESS
    assert result.valid and isfinite(result.volatility)
    assert result.volatility == pytest.approx(volatility, abs=1e-6)
    assert result.residual <= max(1e-8, market * 1e-7)
    assert model.calculate_impv(market, s, k, r, t, cp) == round(result.volatility, 4)


@pytest.mark.parametrize("model", [black_76, black_scholes])
def test_iv_result_rejects_invalid_inputs_and_no_solution(model):
    for price, spot, strike, rate, remaining in [
        (float("nan"), 100, 100, 0.02, 0.5),
        (1.0, 0, 100, 0.02, 0.5),
        (1.0, 100, 100, 0.02, 0),
        (float("inf"), 100, 100, 0.02, 0.5),
        (1000, 100, 100, 0.02, 0.5),
    ]:
        result = model.calculate_impv_result(price, spot, strike, rate, remaining, 1)
        assert result.status is not IVStatus.SUCCESS
        assert not result.valid
        assert result.volatility is None
        assert model.calculate_impv(price, spot, strike, rate, remaining, 1) == 0.0


@pytest.mark.parametrize("model", [black_76, black_scholes])
def test_iv_result_rejects_bad_cp_and_exact_arbitrage_bounds(model):
    for cp in (0, 3):
        assert not model.calculate_impv_result(4, 100, 100, 0.02, 0.5, cp).valid
    assert not model.calculate_impv_result(0, 100, 100, 0.02, 0.5, 1).valid


def test_iv_result_uses_bounded_iteration_and_fails_closed():
    market = black_76.calculate_price(100, 100, 0.02, 0.25, 0.4, 1)
    result = black_76.calculate_impv_result(market, 100, 100, 0.02, 0.25, 1, max_iterations=0)
    assert result.status is IVStatus.INVALID_INPUT
    assert not result.valid


def test_binomial_legacy_iv_and_backend_unchanged():
    from vnpy_optionmaster.pricing import binomial_tree
    value = binomial_tree.calculate_impv(8.0, 100.0, 100.0, 0.02, 0.5, 1)
    assert isfinite(value)
    assert not hasattr(binomial_tree, "calculate_impv_result")
