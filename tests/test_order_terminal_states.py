"""Safety gates for out-of-order close-order terminal reports."""
from datetime import datetime
from typing import cast

import pytest
from vnpy.event import Event
from vnpy.trader.constant import Direction, Offset, Status
from vnpy.trader.object import OrderData

from test_algo_orders import _make_algo
from test_hedge_orders import _run_hedge, _OptionEngine as _HedgeOptionEngine


def _order(gateway: str, symbol: str, exchange, orderid: str, status: Status,
           traded: float, volume: float, direction: Direction) -> OrderData:
    return OrderData(
        gateway_name=gateway,
        symbol=symbol,
        exchange=exchange,
        orderid=orderid,
        direction=direction,
        offset=Offset.CLOSE,
        volume=volume,
        traded=traded,
        status=status,
        datetime=datetime(2026, 11, 22, 9, 30),
    )


@pytest.mark.parametrize("status,traded", [(Status.CANCELLED, 1.0), (Status.REJECTED, 0.0)])
def test_hedge_cancel_or_reject_never_auto_resumes(status: Status, traded: float) -> None:
    result = _run_hedge(
        pos_delta=-6.0, long_pos=0, long_pos_frozen=0,
        short_pos=2, short_pos_frozen=0,
    )
    assert len(result.calls) == 1
    req, gateway = result.calls[0]
    assert req.offset == Offset.CLOSE
    order = _order(gateway, result.contract.symbol, result.contract.exchange,
                   "1", status, traded, 2.0, Direction.LONG)
    result.hedge.process_order_event(Event("eOrder", order))
    engine = cast(_HedgeOptionEngine, result.hedge.option_engine)
    engine.main_engine.book.holding.short_pos = 2.0 - traded
    engine._portfolio.pos_delta = -6.0 + traded * 2
    result.hedge.run()
    assert len(result.calls) == 1
    assert not result.hedge.active_orderids
    assert result.hedge.close_reconciliation is not None


def test_hedge_close_requires_real_terminal_filled_report() -> None:
    result = _run_hedge(
        pos_delta=-6.0, long_pos=0, long_pos_frozen=0,
        short_pos=2, short_pos_frozen=0,
    )
    engine = cast(_HedgeOptionEngine, result.hedge.option_engine)
    engine.main_engine.book.holding.short_pos = 0
    engine._portfolio.pos_delta = -2.0
    # No terminal order report: even seemingly reconciled positions are insufficient.
    result.hedge.active_orderids.clear()
    result.hedge.run()
    assert len(result.calls) == 1


@pytest.mark.parametrize("status,traded", [(Status.CANCELLED, 1.0), (Status.REJECTED, 0.0)])
def test_electronic_eye_cancel_or_reject_never_opens(status: Status, traded: float) -> None:
    algo, main_engine, _ = _make_algo(short_pos=2, net_pos=-2, tick=None)
    algo.send_long(1.8, 8)
    assert len(main_engine.calls) == 1
    report = _order(
        main_engine.contract.gateway_name, main_engine.contract.symbol,
        main_engine.contract.exchange, "1", status, traded, 2.0, Direction.LONG,
    )
    algo.on_order(report)
    algo.option.short_pos = 2 - int(traded)
    algo.option.net_pos = -algo.option.short_pos
    algo.send_long(1.8, 6)
    assert len(main_engine.calls) == 1


def test_electronic_eye_stale_report_cannot_reopen_after_cancel() -> None:
    algo, engine, _ = _make_algo(short_pos=2, net_pos=-2, tick=None)
    algo.send_long(1.8, 8)
    cancel = _order(engine.contract.gateway_name, engine.contract.symbol,
                    engine.contract.exchange, "1", Status.CANCELLED, 1.0, 2.0, Direction.LONG)
    stale = _order(engine.contract.gateway_name, engine.contract.symbol,
                   engine.contract.exchange, "1", Status.NOTTRADED, 0.0, 2.0, Direction.LONG)
    algo.on_order(cancel)
    algo.on_order(stale)
    algo.option.short_pos = 0
    algo.option.net_pos = 0
    algo.send_long(1.8, 5)
    assert len(engine.calls) == 1


def test_hedge_full_fill_report_waits_for_full_position_reconciliation() -> None:
    result = _run_hedge(pos_delta=-6.0, long_pos=0, long_pos_frozen=0,
                        short_pos=2, short_pos_frozen=0)
    report = _order(result.contract.gateway_name, result.contract.symbol,
                    result.contract.exchange, "1", Status.ALLTRADED, 2, 2, Direction.LONG)
    result.hedge.process_order_event(Event("eOrder", report))
    portfolio = cast(_HedgeOptionEngine, result.hedge.option_engine)
    portfolio.main_engine.book.holding.short_pos = 1  # only one fill reflected locally
    portfolio._portfolio.pos_delta = -4
    result.hedge.run()
    assert len(result.calls) == 1  # cannot send an OPEN or duplicate CLOSE yet

    portfolio.main_engine.book.holding.short_pos = 0
    portfolio._portfolio.pos_delta = -2
    result.hedge.run()
    assert len(result.calls) == 2
    assert result.calls[-1][0].offset == Offset.OPEN


def test_electronic_eye_full_fill_report_waits_for_zero_remaining_opposite() -> None:
    algo, engine, _ = _make_algo(short_pos=3, net_pos=-3, tick=None)
    algo.send_long(1.8, 8)
    report = _order(engine.contract.gateway_name, engine.contract.symbol,
                    engine.contract.exchange, "1", Status.ALLTRADED, 3, 3, Direction.LONG)
    algo.on_order(report)
    algo.option.short_pos = 1  # position callback is lagging the reported full fill
    algo.option.net_pos = -1
    algo.send_long(1.8, 5)
    assert len(engine.calls) == 1

    algo.option.short_pos = 0
    algo.option.net_pos = 0
    algo.send_long(1.8, 5)
    assert len(engine.calls) == 2
    assert engine.calls[-1][0].offset == Offset.OPEN


def test_manual_acknowledgement_required_after_hedge_cancel(tmp_path) -> None:
    result = _run_hedge(pos_delta=-6.0, long_pos=0, long_pos_frozen=0,
                        short_pos=2, short_pos_frozen=0)
    assert not result.hedge.acknowledge_failed_close()  # active submitted order
    report = _order(result.contract.gateway_name, result.contract.symbol,
                    result.contract.exchange, "1", Status.CANCELLED, 1, 2, Direction.LONG)
    result.hedge.process_order_event(Event("eOrder", report))
    portfolio = cast(_HedgeOptionEngine, result.hedge.option_engine)
    portfolio.main_engine.book.holding.short_pos = 1
    portfolio._portfolio.pos_delta = -4
    result.hedge.run()
    assert len(result.calls) == 1

    assert result.hedge.acknowledge_failed_close(
        operator="operator", reason="fills and holdings inspected",
        expected_orderid="TEST.1", positions_verified=True,
        audit_path=tmp_path / "recovery.jsonl"
    )  # operator audited external state
    result.hedge.run()
    assert len(result.calls) == 2
    assert result.calls[-1][0].offset == Offset.CLOSE  # retry closing, not opening


def test_manual_acknowledgement_required_after_electronic_eye_cancel(tmp_path) -> None:
    algo, engine, _ = _make_algo(short_pos=2, net_pos=-2, tick=None)
    algo.send_long(1.8, 8)
    assert not algo.acknowledge_failed_close(Direction.LONG)
    report = _order(engine.contract.gateway_name, engine.contract.symbol,
                    engine.contract.exchange, "1", Status.CANCELLED, 1, 2, Direction.LONG)
    algo.on_order(report)
    algo.option.short_pos = 1
    algo.option.net_pos = -1
    algo.send_long(1.8, 6)
    assert len(engine.calls) == 1
    assert algo.acknowledge_failed_close(
        Direction.LONG, operator="operator", reason="holds rechecked",
        expected_orderid="TEST.1", positions_verified=True,
        audit_path=tmp_path / "recovery.jsonl"
    )
    algo.send_long(1.8, 6)
    assert len(engine.calls) == 2
    assert engine.calls[-1][0].offset == Offset.CLOSE


def test_invalid_direction_cannot_acknowledge_cancelled_short_close() -> None:
    algo, engine, _ = _make_algo(short_pos=0, net_pos=2, tick=None)
    algo.send_short(1.8, 6)
    report = _order(engine.contract.gateway_name, engine.contract.symbol,
                    engine.contract.exchange, "1", Status.CANCELLED, 0, 2, Direction.SHORT)
    algo.on_order(report)
    assert not algo.acknowledge_failed_close(Direction.NET)
    assert algo.pending_short_close_status == Status.CANCELLED


def test_hedge_recovery_requires_named_operator_reason_and_exact_order() -> None:
    result = _run_hedge(pos_delta=-6.0, long_pos=0, long_pos_frozen=0,
                        short_pos=2, short_pos_frozen=0)
    report = _order(result.contract.gateway_name, result.contract.symbol,
                    result.contract.exchange, "1", Status.CANCELLED, 0, 2, Direction.LONG)
    result.hedge.process_order_event(Event("eOrder", report))
    pending = result.hedge.close_orderid
    assert pending
    assert not result.hedge.acknowledge_failed_close(
        operator="", reason="inspected", expected_orderid=pending)
    assert not result.hedge.acknowledge_failed_close(
        operator="trader", reason="", expected_orderid=pending)
    assert not result.hedge.acknowledge_failed_close(
        operator="trader", reason="checked positions", expected_orderid="WRONG")
    assert result.hedge.close_orderid == pending


def test_electronic_eye_recovery_requires_order_specific_audited_confirmation() -> None:
    algo, engine, _ = _make_algo(short_pos=2, net_pos=-2, tick=None)
    algo.send_long(1.8, 8)
    report = _order(engine.contract.gateway_name, engine.contract.symbol,
                    engine.contract.exchange, "1", Status.REJECTED, 0, 2, Direction.LONG)
    algo.on_order(report)
    pending = algo.pending_long_close_orderid
    assert pending
    assert not algo.acknowledge_failed_close(
        Direction.LONG, operator="", reason="reviewed", expected_orderid=pending)
    assert not algo.acknowledge_failed_close(
        Direction.LONG, operator="trader", reason="", expected_orderid=pending)
    assert not algo.acknowledge_failed_close(
        Direction.LONG, operator="trader", reason="reviewed", expected_orderid="BAD")
    assert algo.pending_long_close_orderid == pending
