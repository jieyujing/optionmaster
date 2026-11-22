"""Fail-closed market freshness checks and durable operator recovery audit."""
from datetime import datetime, timezone
import json
import os
from pathlib import Path
from typing import Any


MAX_QUOTE_AGE_SECONDS: int = 30
MAX_CLOCK_SKEW_SECONDS: int = 2
AUDIT_FILENAME: str = "optionmaster_recovery_audit.jsonl"


def quote_is_fresh(
    tick: Any,
    *,
    now: datetime | None = None,
    max_age_seconds: int = MAX_QUOTE_AGE_SECONDS
) -> bool:
    """Reject absent, stale or future-dated exchange ticks.

    Naive exchange timestamps are interpreted in the host's local time zone;
    live gateway deployments must align their host and exchange clocks.
    """
    if tick is None or max_age_seconds <= 0:
        return False

    timestamps = [getattr(tick, "datetime", None)]
    localtime = getattr(tick, "localtime", None)
    if localtime is not None:
        timestamps.append(localtime)

    for timestamp in timestamps:
        if not isinstance(timestamp, datetime):
            return False
        if timestamp.tzinfo is None:
            clock = (now or datetime.now()).astimezone().replace(tzinfo=None) if (
                now is not None and now.tzinfo is not None
            ) else (now or datetime.now())
        else:
            clock = now.astimezone(timestamp.tzinfo) if (
                now is not None and now.tzinfo is not None
            ) else datetime.now(timestamp.tzinfo)
            if clock.tzinfo is None:
                return False
        age = (clock - timestamp).total_seconds()
        if not -MAX_CLOCK_SKEW_SECONDS <= age <= max_age_seconds:
            return False
    return True


def risk_snapshot_is_fresh(option: Any, *, now: datetime | None = None) -> bool:
    """A Greek calculation is actionable only while its option and underlying feeds are fresh."""
    if not getattr(option, "greeks_valid", False):
        return False
    if not quote_is_fresh(getattr(option, "tick", None), now=now):
        return False
    underlying = getattr(option, "underlying", None)
    return quote_is_fresh(getattr(underlying, "tick", None), now=now)


def record_recovery_audit(
    *,
    component: str,
    target: str,
    orderid: str,
    status: str,
    operator: str,
    reason: str,
    context: dict[str, Any] | None = None,
    path: Path | None = None,
) -> bool:
    """Append an fsync-backed audit entry before unlocking a trading block.

    An audit-write failure prevents unlocking; no credentials or sensitive
    customer order payloads are recorded.
    """
    if not (component and target and orderid and operator.strip() and reason.strip()):
        return False
    event = {
        "at_utc": datetime.now(timezone.utc).isoformat(),
        "component": component,
        "target": target,
        "orderid": orderid,
        "status": status,
        "operator": operator.strip(),
        "reason": reason.strip(),
        "context": context or {},
    }
    file_path = path or Path.cwd() / AUDIT_FILENAME
    try:
        flags = os.O_WRONLY | os.O_APPEND | os.O_CREAT
        if hasattr(os, "O_NOFOLLOW"):
            flags |= os.O_NOFOLLOW
        fd = os.open(file_path, flags, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            stream.write(json.dumps(event, ensure_ascii=False, sort_keys=True) + "\n")
            stream.flush()
            os.fsync(stream.fileno())
    except (OSError, TypeError, ValueError):
        return False
    return True
