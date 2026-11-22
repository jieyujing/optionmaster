"""Named operator recovery is durable and fail-closed when its audit cannot be written."""
import json
import os

from vnpy.event import Event
from vnpy.trader.constant import Direction, Status

from vnpy_optionmaster.risk_safety import record_recovery_audit
from test_order_terminal_states import _order
from test_hedge_orders import _run_hedge
from test_algo_orders import _make_algo


def test_recovery_journal_is_append_only(tmp_path) -> None:
    audit = tmp_path / "audit.jsonl"
    for orderid in ("TEST.1", "TEST.2"):
        assert record_recovery_audit(
            component="delta_hedge", target="IF.CFFEX", orderid=orderid,
            status="CANCELLED", operator="operator", reason="positions rechecked",
            context={"volume": 2}, path=audit
        )
    rows = [json.loads(line) for line in audit.read_text().splitlines()]
    assert [entry["orderid"] for entry in rows] == ["TEST.1", "TEST.2"]
    assert all(entry["operator"] == "operator" for entry in rows)
    assert rows[0]["at_utc"].endswith("+00:00")
    if os.name == "posix":
        assert audit.stat().st_mode & 0o077 == 0


def test_recovery_audit_failure_keeps_hedge_blocked(tmp_path) -> None:
    result = _run_hedge(
        pos_delta=-6.0, long_pos=0, long_pos_frozen=0,
        short_pos=2, short_pos_frozen=0
    )
    report = _order(
        result.contract.gateway_name, result.contract.symbol,
        result.contract.exchange, "1", Status.CANCELLED, 0, 2, Direction.LONG
    )
    result.hedge.process_order_event(Event("eOrder", report))
    assert not result.hedge.acknowledge_failed_close(
        operator="operator", reason="reviewed", expected_orderid="TEST.1",
        positions_verified=True, audit_path=tmp_path / "missing" / "audit.jsonl"
    )
    assert result.hedge.close_order_status == Status.CANCELLED
    assert result.hedge.close_orderid == "TEST.1"


def test_recovery_audit_failure_keeps_electronic_eye_blocked(tmp_path) -> None:
    algo, engine, _ = _make_algo(short_pos=2, net_pos=-2, tick=None)
    algo.send_long(1.8, 8)
    report = _order(
        engine.contract.gateway_name, engine.contract.symbol,
        engine.contract.exchange, "1", Status.REJECTED, 0, 2, Direction.LONG
    )
    algo.on_order(report)
    assert not algo.acknowledge_failed_close(
        Direction.LONG, operator="operator", reason="verified",
        expected_orderid="TEST.1", positions_verified=True,
        audit_path=tmp_path / "missing" / "audit.jsonl"
    )
    assert algo.pending_long_close_status == Status.REJECTED


def test_active_automation_cannot_be_approved(tmp_path) -> None:
    result = _run_hedge(
        pos_delta=-6.0, long_pos=0, long_pos_frozen=0,
        short_pos=2, short_pos_frozen=0
    )
    report = _order(result.contract.gateway_name, result.contract.symbol,
                    result.contract.exchange, "1", Status.REJECTED, 0, 2, Direction.LONG)
    result.hedge.process_order_event(Event("eOrder", report))
    result.hedge.active = True
    assert not result.hedge.acknowledge_failed_close(
        operator="operator", reason="verified", expected_orderid="TEST.1",
        positions_verified=True, audit_path=tmp_path / "audit.jsonl"
    )
    assert not (tmp_path / "audit.jsonl").exists()
