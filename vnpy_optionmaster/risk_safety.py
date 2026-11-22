"""Fail-closed market freshness checks and durable operator recovery audit."""
from datetime import datetime, timezone
import json
import os
from pathlib import Path
from typing import Any
from .trading_sessions import CHINA_TZ


MAX_QUOTE_AGE_SECONDS: int = 30
MAX_CLOCK_SKEW_SECONDS: int = 2
AUDIT_FILENAME: str = "optionmaster_recovery_audit.jsonl"


def quote_is_fresh(
    tick: Any,
    *,
    now: datetime | None = None,
    max_age_seconds: int = MAX_QUOTE_AGE_SECONDS
) -> bool:
    """Check exchange timestamps in Asia/Shanghai, not the host's local zone.

    Naive CTP timestamps and a naive injected clock are treated as China time;
    aware timestamps are converted. Clocks must be synchronized by deployment.
    """
    if tick is None or max_age_seconds <= 0:
        return False

    def normalize(stamp: datetime) -> datetime:
        if stamp.tzinfo is None:
            return stamp.replace(tzinfo=CHINA_TZ)
        return stamp.astimezone(CHINA_TZ)

    clock = normalize(now) if now is not None else datetime.now(CHINA_TZ)
    timestamps = [getattr(tick, "datetime", None)]
    localtime = getattr(tick, "localtime", None)
    if localtime is not None:
        timestamps.append(localtime)

    for stamp in timestamps:
        if not isinstance(stamp, datetime):
            return False
        age = (clock - normalize(stamp)).total_seconds()
        if not -MAX_CLOCK_SKEW_SECONDS <= age <= max_age_seconds:
            return False
    return True


def risk_snapshot_is_fresh(option: Any, *, now: datetime | None = None) -> bool:
    """A Greek calculation is actionable only while its option and underlying feeds are fresh."""
    if not getattr(option, "greeks_valid", False):
        return False
    if not getattr(option, "mark_valid", True):
        return False
    if not getattr(getattr(option, "underlying", None), "mark_valid", True):
        return False
    if not quote_is_fresh(getattr(option, "tick", None), now=now):
        return False
    underlying = getattr(option, "underlying", None)
    version = getattr(option, "greeks_quote_version", "legacy_no_version")
    if version != "legacy_no_version":
        if (version is None or len(version) != 2
                or version[0] is not getattr(option, "tick", None)
                or version[1] is not getattr(underlying, "tick", None)):
            return False
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
