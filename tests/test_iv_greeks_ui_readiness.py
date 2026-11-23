"""The Greeks risk monitor must label display-only aggregates as non-actionable.

Tests invoke the actual rendering boundary with a minimal cell double;
there is no Qt window and no gateway / live order.
"""
from types import SimpleNamespace

from test_iv_chain_burst import _real_chain_fixture
from vnpy_optionmaster.ui.monitor import OptionGreeksMonitor


class _Cell:
    def __init__(self) -> None:
        self.value = ""
        self.tooltip = ""
        self.color = None

    def setText(self, text: str) -> None:
        self.value = text

    def setToolTip(self, text: str) -> None:
        self.tooltip = text

    def setForeground(self, color: object) -> None:
        self.color = color


def _monitor(portfolio: object) -> SimpleNamespace:
    fields = ("long_pos", "short_pos", "net_pos", "pos_delta", "pos_gamma",
              "pos_theta", "pos_vega", "risk_status")
    cells = {(portfolio.name, "组合"): {name: _Cell() for name in fields}}
    monitor = SimpleNamespace(
        cells=cells,
        portfolio_name=portfolio.name,
        greeks_precision="2f",
        row=lambda cell: 0,
        showRow=lambda row: None,
        hideRow=lambda row: None,
    )
    return monitor


def test_portfolio_risk_status_explicitly_distinguishes_actionable_and_display_only() -> None:
    _, portfolio, _, options = _real_chain_fixture(option_count=6)
    assert "risk_status" in {header["name"] for header in OptionGreeksMonitor.headers}
    monitor = _monitor(portfolio)
    status = monitor.cells[(portfolio.name, "组合")]["risk_status"]

    OptionGreeksMonitor.update_row(monitor, portfolio.name, "组合", portfolio)
    assert status.value == "快照有效"
    assert "仍需" in status.tooltip

    held = next(option for option in options if option.net_pos)
    held.greeks_valid = False
    OptionGreeksMonitor.update_row(monitor, portfolio.name, "组合", portfolio)
    assert status.value == "仅供展示"
    assert "不可" in status.tooltip
    # The numerical cells remain visible; risk validity is a distinct field.
    assert monitor.cells[(portfolio.name, "组合")]["pos_delta"].value


def test_timer_refreshes_aggregate_readiness_without_an_underlying_tick() -> None:
    _, portfolio, _, options = _real_chain_fixture(option_count=6)
    monitor = _monitor(portfolio)
    monitor.option_engine = SimpleNamespace(get_portfolio=lambda name: portfolio)
    monitor.update_row = lambda name, kind, data: OptionGreeksMonitor.update_row(
        monitor, name, kind, data,
    )
    status = monitor.cells[(portfolio.name, "组合")]["risk_status"]

    OptionGreeksMonitor.process_timer_event(monitor, SimpleNamespace(data=None))
    assert status.value == "快照有效"

    next(option for option in options if option.net_pos).greeks_valid = False
    OptionGreeksMonitor.process_timer_event(monitor, SimpleNamespace(data=None))
    assert status.value == "仅供展示"


class _Signal:
    def __init__(self) -> None:
        self.callback = None

    def connect(self, callback: object) -> None:
        self.callback = callback

    def emit(self, event: object) -> None:
        if self.callback is not None:
            self.callback(event)


def test_greeks_monitor_registers_timer_as_an_independent_refresh_source() -> None:
    from vnpy.trader.event import EVENT_TIMER

    subscriptions: dict[str, object] = {}
    monitor = SimpleNamespace(
        signal_tick=_Signal(),
        signal_trade=_Signal(),
        signal_position=_Signal(),
        signal_timer=_Signal(),
        process_tick_event=lambda event: None,
        process_trade_event=lambda event: None,
        process_position_event=lambda event: None,
        process_timer_event=lambda event: None,
        event_engine=SimpleNamespace(
            register=lambda name, handler: subscriptions.update({name: handler})
        ),
    )
    OptionGreeksMonitor.register_event(monitor)
    assert EVENT_TIMER in subscriptions
    assert monitor.signal_timer.callback is monitor.process_timer_event
