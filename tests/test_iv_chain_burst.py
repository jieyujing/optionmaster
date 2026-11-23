"""Real portfolio/chain fanout with an entirely synthetic offline market feed."""
from datetime import datetime, timedelta
from time import monotonic, sleep

from vnpy.event import Event, EventEngine
from vnpy.trader.constant import Exchange, OptionType, Product
from vnpy.trader.event import EVENT_TICK
from vnpy.trader.object import ContractData, TickData

from vnpy_optionmaster.base import PortfolioData
from vnpy_optionmaster.engine import OptionEngine
from vnpy_optionmaster.pricing import black_76
from vnpy_optionmaster.pricing.iv_runtime import OptionIVRuntime
from vnpy_optionmaster.risk_safety import risk_snapshot_is_fresh
from vnpy_optionmaster.trading_sessions import CHINA_TZ, SessionPolicy


class _OfflineOnlySessions(SessionPolicy):
    """NEVER an exchange session policy: just a synthetic test double."""

    def __init__(self, symbols: set[str]) -> None:
        super().__init__((), {"verified_by": "SYNTHETIC_TEST_ONLY"})
        self.symbols = symbols

    def is_tradable(self, vt_symbol: str, at: datetime) -> bool:
        return vt_symbol in self.symbols


def _book(contract: ContractData, *, bid: float, ask: float, at: datetime) -> TickData:
    return TickData(
        gateway_name=contract.gateway_name, symbol=contract.symbol,
        exchange=contract.exchange, datetime=at,
        bid_price_1=round(bid, 2), ask_price_1=round(ask, 2),
        bid_volume_1=30, ask_volume_1=30,
        last_price=round((bid + ask) / 2, 2),
    )


def _real_chain_fixture(option_count: int = 6):
    if option_count < 2 or option_count % 2:
        raise ValueError("Provide an even number of call/put options")
    chain_name = "IF2612.CFFEX"
    underlying_contract = ContractData(
        gateway_name="OFFLINE", symbol="IF2612", exchange=Exchange.CFFEX,
        name="Synthetic only", product=Product.FUTURES,
        size=10, pricetick=0.1,
    )
    portfolio = PortfolioData("synthetic_chain", EventEngine())
    for index in range(option_count // 2):
        strike = 100 + (index - (option_count // 4)) * 2
        for cp in (OptionType.CALL, OptionType.PUT):
            code = "C" if cp == OptionType.CALL else "P"
            symbol = f"IO2612-{code}-{strike}"
            contract = ContractData(
                gateway_name="OFFLINE", symbol=symbol, exchange=Exchange.CFFEX,
                name=symbol, product=Product.OPTION, size=10, pricetick=0.1,
                option_strike=float(strike), option_index=str(strike),
                option_underlying="IF2612", option_type=cp,
                option_portfolio="IO",
                option_expiry=datetime(2027, 2, 15, 15),
            )
            portfolio.add_option(contract)
    portfolio.set_chain_underlying(chain_name, underlying_contract)
    portfolio.set_pricing_model(black_76)
    portfolio.set_interest_rate(0.02)
    underlying = portfolio.underlyings[underlying_contract.vt_symbol]

    baseline = datetime.now(CHINA_TZ) - timedelta(milliseconds=200)
    underlying_tick = _book(
        underlying_contract, bid=99.9, ask=100.1, at=baseline,
    )
    portfolio.update_tick(underlying_tick)
    options = [portfolio.options[s] for s in sorted(portfolio.options)]
    for index, option in enumerate(options):
        option.long_pos = 2 if index % 3 != 0 else 0
        option.net_pos = option.long_pos
        fair = black_76.calculate_price(
            100, option.strike_price, option.interest_rate,
            option.time_to_expiry, 0.32, option.option_type,
        )
        bid = max(0.1, round(fair - 0.1, 1))
        ask = round(bid + 0.2, 1)
        tick = TickData(
            gateway_name="OFFLINE", symbol=option.symbol,
            exchange=option.exchange, datetime=baseline,
            bid_price_1=bid, ask_price_1=ask,
            bid_volume_1=30, ask_volume_1=30,
            last_price=round((bid + ask) / 2, 1),
        )
        portfolio.update_tick(tick)
    portfolio.calculate_pos_greeks()

    engine = object.__new__(OptionEngine)
    engine.instruments = {underlying.vt_symbol: underlying}
    engine.instruments.update({option.vt_symbol: option for option in options})
    engine.session_policy = _OfflineOnlySessions(set(engine.instruments))
    engine.last_accepted_ticks = {}
    engine.tick_rejections = {}
    engine.iv_runtime = None
    return engine, portfolio, underlying, options


def test_real_chain_worker_underlying_fanout_is_coherent_and_fresh():
    engine, portfolio, underlying, options = _real_chain_fixture(option_count=6)
    assert all(risk_snapshot_is_fresh(o) for o in options)
    runtime = OptionIVRuntime(
        cache_entries=64, queue_capacity=2, max_pending=12,
        max_priority_streak=2, inflight_timeout_seconds=5.0,
    )
    try:
        assert runtime.start()
        assert all(runtime.attach(o) for o in options)
        engine.iv_runtime = runtime
        assert not any(risk_snapshot_is_fresh(o) for o in options)
        previous = underlying.tick
        assert previous is not None
        now = datetime.now(CHINA_TZ)
        tick = _book(
            ContractData(
                gateway_name=previous.gateway_name, symbol=previous.symbol,
                exchange=previous.exchange, name="Synthetic",
                product=Product.FUTURES, size=10, pricetick=0.1,
            ),
            bid=100.0, ask=100.2, at=now,
        )
        engine.process_tick_event(Event(EVENT_TICK, tick))
        assert not any(risk_snapshot_is_fresh(o) for o in options)
        deadline = monotonic() + 6.0
        while monotonic() < deadline:
            runtime.pump()
            if all(risk_snapshot_is_fresh(o) for o in options):
                break
            sleep(0.002)
        assert all(risk_snapshot_is_fresh(o) for o in options)
        assert all(o.greeks_quote_version == (o.tick, underlying.tick) for o in options)
        assert all(o.greeks_worker_generation == runtime.worker.generation for o in options)
        expected_delta = underlying.pos_delta + sum(o.pos_delta for o in options)
        assert abs(portfolio.pos_delta - expected_delta) < 1e-9
        assert runtime.dispatcher.scheduler.pending_count == 0
        assert engine.tick_rejections == {}
    finally:
        runtime.close()


def test_real_chain_burst_keeps_worker_pending_and_risk_gates_bounded():
    engine, portfolio, underlying, options = _real_chain_fixture(option_count=10)
    runtime = OptionIVRuntime(
        cache_entries=32, queue_capacity=1, max_pending=3,
        max_priority_streak=1, inflight_timeout_seconds=5.0,
    )
    try:
        assert runtime.start()
        assert all(runtime.attach(o) for o in options)
        engine.iv_runtime = runtime
        start = datetime.now(CHINA_TZ)
        for i in range(12):
            tick = TickData(
                gateway_name="OFFLINE", symbol=underlying.symbol,
                exchange=underlying.exchange,
                datetime=start + timedelta(milliseconds=i + 1),
                bid_price_1=99.8 + 0.1 * (i % 3),
                ask_price_1=100.0 + 0.1 * (i % 3),
                bid_volume_1=30, ask_volume_1=30,
            )
            engine.process_tick_event(Event(EVENT_TICK, tick))
            assert runtime.dispatcher.scheduler.pending_count <= 3
            for option in options:
                if risk_snapshot_is_fresh(option):
                    assert option.greeks_quote_version == (option.tick, underlying.tick)
        deadline = monotonic() + 5
        while monotonic() < deadline:
            runtime.pump()
            if runtime.dispatcher.scheduler.pending_count == 0 and runtime.dispatcher._in_flight is None:
                break
            sleep(0.002)
        assert runtime.dispatcher.scheduler.pending_count <= 3
        assert runtime.dispatcher.scheduler.overflows > 0
        assert runtime.rejected > 0
        # Overflow is not permission to trade with a previously accepted quote.
        for option in options:
            if risk_snapshot_is_fresh(option):
                assert option.greeks_quote_version == (option.tick, underlying.tick)
        assert engine.tick_rejections == {}
    finally:
        runtime.close()
