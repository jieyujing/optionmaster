"""Risk validity regressions using a real OptionData method with minimal dependencies."""
from types import SimpleNamespace

from vnpy_optionmaster.base import OptionData


def test_invalid_mid_iv_keeps_last_greeks_but_marks_unavailable() -> None:
    option = object.__new__(OptionData)
    option.refresh_expiry = lambda: None
    option.underlying = SimpleNamespace(mid_price=100.0)
    option.mid_impv = 0.0
    option.size = 10
    option.net_pos = 2
    option.tick = None
    option.greeks_valid = True

    option.theo_delta = 5.0
    option.theo_gamma = 0.4
    option.theo_theta = -1.5
    option.theo_vega = 2.0
    option.pos_delta = 10.0

    option.calculate_theo_greeks()
    option.calculate_pos_greeks()

    assert option.greeks_valid is False
    assert option.theo_delta == 5.0
    assert option.pos_delta == 10.0
    assert option.theo_theta == -1.5
