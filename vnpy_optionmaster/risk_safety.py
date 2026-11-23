"""Fail-closed market freshness checks and durable operator recovery audit."""
from datetime import datetime, timezone
from math import isclose, isfinite
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


def pricing_input_fingerprint(option: Any) -> tuple[Any, ...]:
    """Exactly the owner-side inputs of the actionable Worker Greeks.

    This is independent from the Worker numerical IV LRU key; an accepted
    cached numerical result cannot renew an older risk-version identity.
    """
    underlying = getattr(option, "underlying", None)
    return (
        getattr(option, "strike_price", None),
        getattr(option, "option_type", None),
        getattr(option, "interest_rate", None),
        getattr(option, "time_to_expiry", None),
        getattr(option, "underlying_adjustment", None),
        getattr(underlying, "mid_price", None),
        getattr(option, "size", None),
        id(getattr(option, "calculate_impv", None)),
        id(getattr(option, "calculate_greeks", None)),
    )


def risk_snapshot_is_fresh(option: Any, *, now: datetime | None = None) -> bool:
    """A Greek calculation is actionable only while its option and underlying feeds are fresh."""
    if (getattr(option, "recovery_quarantined", False)
            or getattr(getattr(option, "underlying", None), "recovery_quarantined", False)):
        return False
    runtime = getattr(option, "iv_runtime", None)
    if runtime is not None:
        # A cached or previously committed IV is not permission to trade after
        # a Worker crash, while a newer quote is pending, or after generation loss.
        worker = getattr(runtime, "worker", None)
        pending = getattr(runtime, "pending", None)
        if (worker is None or not worker.alive or not isinstance(pending, dict)
                or getattr(option, "vt_symbol", None) in pending
                or getattr(option, "greeks_worker_generation", None) != worker.generation
                or getattr(option, "greeks_input_identity", None) != pricing_input_fingerprint(option)):
            return False
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


def portfolio_risk_identity(
    portfolio: Any, *, now: datetime | None = None
) -> tuple[object, ...] | None:
    """Owner-local identity of one complete, currently valid portfolio risk epoch."""
    held_options: list[tuple[object, ...]] = []
    expected_delta = 0.0
    expected_gamma = 0.0
    expected_theta = 0.0
    expected_vega = 0.0
    for symbol, option in sorted(portfolio.options.items()):
        if not option.net_pos:
            continue
        if not risk_snapshot_is_fresh(option, now=now):
            return None
        # A changed position must also be reflected in its per-option Greeks.
        for observed, theoretical in (
            (option.pos_delta, option.theo_delta),
            (option.pos_gamma, option.theo_gamma),
            (option.pos_theta, option.theo_theta),
            (option.pos_vega, option.theo_vega),
        ):
            expected = theoretical * option.net_pos
            if (not isfinite(observed) or not isfinite(expected)
                    or not isclose(observed, expected, rel_tol=1e-12, abs_tol=1e-9)):
                return None
        expected_delta += option.pos_delta
        expected_gamma += option.pos_gamma
        expected_theta += option.pos_theta
        expected_vega += option.pos_vega
        underlying = getattr(option, "underlying", None)
        held_options.append((
            symbol, id(option), option.net_pos,
            id(option.tick), id(getattr(underlying, "tick", None)),
            id(option.greeks_quote_version),
            getattr(option, "greeks_worker_generation", None),
            pricing_input_fingerprint(option),
            option.theo_delta, option.theo_gamma, option.theo_theta,
            option.theo_vega, option.pos_delta, option.pos_gamma,
            option.pos_theta, option.pos_vega,
        ))

    held_underlyings: list[tuple[object, ...]] = []
    for symbol, underlying in sorted(portfolio.underlyings.items()):
        expected = underlying.theo_delta * underlying.net_pos
        if (not isfinite(underlying.pos_delta) or not isfinite(expected)
                or not isclose(underlying.pos_delta, expected, rel_tol=1e-12, abs_tol=1e-9)):
            return None
        expected_delta += underlying.pos_delta
        if not underlying.net_pos:
            continue
        if (getattr(underlying, "recovery_quarantined", False)
                or not getattr(underlying, "mark_valid", False)
                or not quote_is_fresh(getattr(underlying, "tick", None), now=now)):
            return None
        held_underlyings.append((
            symbol, id(underlying), underlying.net_pos,
            id(underlying.tick), underlying.pos_delta,
        ))
    # Chain/portfolio aggregates must equal the current individual exposures;
    # a stale chain must never be published as an actionable portfolio epoch.
    for observed, expected in (
        (portfolio.pos_delta, expected_delta),
        (portfolio.pos_gamma, expected_gamma),
        (portfolio.pos_theta, expected_theta),
        (portfolio.pos_vega, expected_vega),
    ):
        if (not isfinite(observed) or not isfinite(expected)
                or not isclose(observed, expected, rel_tol=1e-12, abs_tol=1e-9)):
            return None
    return tuple(held_options), tuple(held_underlyings)


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
