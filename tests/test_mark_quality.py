"""Greeks/IV must never depend on a fabricated single-sided midpoint."""
from datetime import datetime
from types import SimpleNamespace

from vnpy.trader.constant import OptionType, Product
from vnpy.trader.object import ContractData, TickData

from vnpy_optionmaster.base import UnderlyingData, OptionData
from vnpy_optionmaster.pricing import black_76
from vnpy_optionmaster.risk_safety import risk_snapshot_is_fresh

from test_hedge_orders import _contract, _tick


def test_single_bid_keeps_last_display_price_but_invalidates_mark() -> None:
    underlying = UnderlyingData(_contract(1))
    good = _tick(_contract(1))
    underlying.update_tick(good)
    assert underlying.mid_price == 9.75
    assert underlying.mark_valid is True

    bad = _tick(_contract(1))
    bad.ask_price_1, bad.ask_volume_1 = 0.0, 0
    underlying.update_tick(bad)
    assert underlying.mid_price == 9.75
    assert underlying.mark_valid is False

    recovered = _tick(_contract(1))
    recovered.bid_price_1 = 10.0
    recovered.ask_price_1 = 10.5
    underlying.update_tick(recovered)
    assert underlying.mid_price == 10.25
    assert underlying.mark_valid is True


def _real_option_pair():
    base_contract = ContractData(
        gateway_name="TEST", symbol="IF2612", exchange=_contract(1).exchange,
        name="Futures", product=Product.FUTURES, size=10,
        pricetick=0.5,
    )
    underlying = UnderlyingData(base_contract)
    spot = TickData(
        gateway_name="TEST", symbol=base_contract.symbol,
        exchange=base_contract.exchange, datetime=datetime.now(),
        bid_price_1=99.5, ask_price_1=100.5,
        bid_volume_1=10, ask_volume_1=10,
    )
    underlying.update_tick(spot)

    opt_contract = ContractData(
        gateway_name="TEST", symbol="IO2612-C-100", exchange=base_contract.exchange,
        name="Option", product=Product.OPTION, size=10,
        pricetick=0.5, option_strike=100.0,
        option_index="100", option_underlying="IF2612",
        option_type=OptionType.CALL, option_portfolio="IO",
        option_expiry=datetime(2027, 1, 15, 15, 0),
    )
    option = OptionData(opt_contract)
    option.set_pricing_model(black_76)
    option.set_underlying(underlying)
    option.set_interest_rate(0.02)
    option.net_pos = 3
    option_tick = TickData(
        gateway_name="TEST", symbol=opt_contract.symbol,
        exchange=opt_contract.exchange, datetime=datetime.now(),
        bid_price_1=2.0, ask_price_1=2.5, bid_volume_1=10, ask_volume_1=10,
    )
    option.update_tick(option_tick)
    return underlying, option, option_tick


def test_option_single_side_rejects_iv_greeks_without_zeroing_last_delta() -> None:
    underlying, option, tick = _real_option_pair()
    assert option.greeks_valid and option.impv_valid
    previous_delta = option.theo_delta
    previous_mid_impv = option.mid_impv
    assert previous_delta > 0

    single = TickData(
        gateway_name="TEST", symbol=tick.symbol, exchange=tick.exchange,
        datetime=datetime.now(), bid_price_1=2.0,
        bid_volume_1=10, ask_price_1=0.0, ask_volume_1=0,
    )
    option.update_tick(single)
    assert option.mark_valid is False
    assert not option.impv_valid
    assert not option.greeks_valid
    assert option.mid_impv == previous_mid_impv
    assert option.theo_delta == previous_delta
    assert not risk_snapshot_is_fresh(option)

    option.update_tick(tick)
    assert option.mark_valid is True
    assert option.greeks_valid and option.impv_valid


def test_single_sided_underlying_invalidates_option_risk_even_if_last_iv_exists() -> None:
    underlying, option, _ = _real_option_pair()
    assert risk_snapshot_is_fresh(option)
    assert option.greeks_valid

    def risk_refresh():
        option.update_underlying_tick(0.0)

    underlying.add_chain(SimpleNamespace(chain_symbol="IO.CFFEX", update_underlying_tick=risk_refresh))
    single_underlying = TickData(
        gateway_name="TEST", symbol=underlying.symbol,
        exchange=underlying.exchange, datetime=datetime.now(),
        bid_price_1=99.5, bid_volume_1=10,
        ask_price_1=0.0, ask_volume_1=0,
    )
    underlying.update_tick(single_underlying)
    assert underlying.mark_valid is False
    assert not option.greeks_valid
    assert not risk_snapshot_is_fresh(option)

    underlying.update_tick(TickData(
        gateway_name="TEST", symbol=underlying.symbol,
        exchange=underlying.exchange, datetime=datetime.now(),
        bid_price_1=99.5, bid_volume_1=10,
        ask_price_1=100.5, ask_volume_1=10,
    ))
    assert option.greeks_valid
    assert risk_snapshot_is_fresh(option)


def test_greek_snapshot_must_match_exact_option_and_underlying_tick_versions() -> None:
    underlying, option, _ = _real_option_pair()
    assert risk_snapshot_is_fresh(option)
    prior_tick = underlying.tick
    assert prior_tick is not None
    # A fresher feed must invalidate old Greeks even if bid/ask prices match.
    underlying.tick = TickData(
        gateway_name="TEST", symbol=underlying.symbol,
        exchange=underlying.exchange, datetime=datetime.now(),
        bid_price_1=99.5, ask_price_1=100.5,
        bid_volume_1=10, ask_volume_1=10,
    )
    assert not risk_snapshot_is_fresh(option)
    option.update_underlying_tick(0.0)
    assert risk_snapshot_is_fresh(option)


def test_incomplete_synthetic_atm_pair_revokes_forward_mark() -> None:
    from vnpy_optionmaster.base import ChainData
    chain = object.__new__(ChainData)
    chain.use_synthetic = True
    chain.atm_index = "100"
    chain.atm_price = 100.0
    chain.calls = {"100": SimpleNamespace(mid_price=2.0, mark_valid=False)}
    chain.puts = {"100": SimpleNamespace(mid_price=3.0, mark_valid=True)}
    chain.underlying = SimpleNamespace(mid_price=100.0, mark_valid=True)
    notices: list[str] = []
    chain.update_underlying_tick = lambda: notices.append("invalidated")

    chain.update_synthetic_price()
    assert chain.underlying.mark_valid is False
    assert notices == ["invalidated"]
