"""Exchange-calendar session gate. No guessed trading hours or holidays.

Production sessions are explicit, dated, operator-verified windows from a
separately maintained JSON file. Without valid data, ALL new automated orders
are prohibited. Market data can still be observed outside those windows.
"""
from dataclasses import dataclass
from datetime import date, datetime, timedelta
import json
import os
from pathlib import Path
from typing import Any
from urllib.parse import urlparse
from zoneinfo import ZoneInfo


CHINA_TZ = ZoneInfo("Asia/Shanghai")
SESSION_FILE_ENV = "OPTIONMASTER_VERIFIED_SESSIONS_FILE"
CONTINUOUS_PHASE = "continuous"


@dataclass(frozen=True)
class SessionWindow:
    vt_symbol: str
    trading_day: date
    phase: str
    start: datetime
    end: datetime

    def contains(self, at: datetime) -> bool:
        return self.start <= at < self.end


class SessionPolicy:
    """Immutable, versioned policy of explicitly audited per-contract sessions."""

    def __init__(self, windows: tuple[SessionWindow, ...], source: dict[str, str]) -> None:
        self.windows = windows
        self.source = source

    @classmethod
    def from_mapping(cls, data: dict[str, Any]) -> "SessionPolicy":
        if data.get("version") != 1:
            raise ValueError("Unsupported trading-session policy version")
        source = data.get("source")
        if not isinstance(source, dict):
            raise ValueError("Session calendar provenance is required")
        url = source.get("official_url", "")
        operator = source.get("verified_by", "")
        verified_at = source.get("verified_at", "")
        parsed = urlparse(url) if isinstance(url, str) else urlparse("")
        if (parsed.scheme != "https" or not parsed.hostname
                or not isinstance(operator, str) or not operator.strip()
                or not isinstance(verified_at, str)):
            raise ValueError("Trusted source URL and human verification record required")
        date.fromisoformat(verified_at)
        records = data.get("sessions")
        if not isinstance(records, list):
            raise ValueError("Expected session records array")

        windows: list[SessionWindow] = []
        for rec in records:
            if not isinstance(rec, dict):
                raise ValueError("Malformed session window")
            symbol = rec.get("symbol")
            exchange = rec.get("exchange")
            if not isinstance(symbol, str) or not symbol or not isinstance(exchange, str) or not exchange:
                raise ValueError("Exact exchange and contract symbol required")
            phase = rec.get("phase")
            if phase not in ("continuous", "auction", "break", "closed"):
                raise ValueError("Unknown session phase")
            day = date.fromisoformat(rec["trading_day"])
            start = datetime.fromisoformat(rec["start"])
            end = datetime.fromisoformat(rec["end"])
            if (start.tzinfo is None or end.tzinfo is None
                    or start.utcoffset() != timedelta(hours=8)
                    or end.utcoffset() != timedelta(hours=8)):
                raise ValueError("Session timestamps need an explicit China +08:00 offset")
            start, end = start.astimezone(CHINA_TZ), end.astimezone(CHINA_TZ)
            if day.weekday() >= 5 or start.weekday() >= 5:
                raise ValueError("Weekend session/trading day cannot be auto-authorized")
            if end <= start or end - start > timedelta(hours=12):
                raise ValueError("Invalid or suspicious exchange session interval")
            if abs((day - start.date()).days) > 2:
                raise ValueError("Trading-day and calendar-day assignment inconsistent")
            window = SessionWindow(f"{symbol}.{exchange}", day, phase, start, end)
            if any(existing.vt_symbol == window.vt_symbol
                   and existing.start < window.end and window.start < existing.end
                   for existing in windows):
                raise ValueError("Overlapping sessions for the same contract")
            windows.append(window)

        return cls(tuple(windows), {
            "official_url": url,
            "verified_by": operator,
            "verified_at": verified_at,
        })

    def trading_day(self, vt_symbol: str, at: datetime) -> str | None:
        if at.tzinfo is None:
            return None
        moment = at.astimezone(CHINA_TZ)
        for window in self.windows:
            if (window.vt_symbol == vt_symbol
                    and window.phase == CONTINUOUS_PHASE
                    and window.contains(moment)):
                return window.trading_day.isoformat()
        return None

    def is_tradable(self, vt_symbol: str, at: datetime) -> bool:
        return self.trading_day(vt_symbol, at) is not None


def load_verified_policy() -> SessionPolicy | None:
    """Fail closed on missing, malformed or inaccessible operator policy."""
    name = os.getenv(SESSION_FILE_ENV)
    if not name:
        return None
    try:
        path = Path(name)
        if not path.is_absolute() or not path.is_file():
            return None
        return SessionPolicy.from_mapping(json.loads(path.read_text(encoding="utf-8")))
    except (OSError, ValueError, TypeError, KeyError, json.JSONDecodeError):
        return None


def session_is_tradable(
    vt_symbol: str,
    *,
    at: datetime | None = None,
    policy: SessionPolicy | None = None,
) -> bool:
    """No calendar/config, unknown contract, lunch, weekend or auction => False."""
    instant = at if at is not None else datetime.now(CHINA_TZ)
    if instant.tzinfo is None:
        return False
    selected = policy if policy is not None else load_verified_policy()
    return selected is not None and selected.is_tradable(vt_symbol, instant)
