"""Explicit, evidence-linked exchange session policy with fail-closed defaults."""
from datetime import datetime, timezone
import json
from zoneinfo import ZoneInfo

import pytest
from vnpy_optionmaster.trading_sessions import SessionPolicy, session_is_tradable


TZ = ZoneInfo("Asia/Shanghai")


def _dt(day: str, hhmm: str) -> datetime:
    return datetime.fromisoformat(day + "T" + hhmm).replace(tzinfo=TZ)


def _policy() -> SessionPolicy:
    return SessionPolicy.from_mapping({
        "version": 1,
        "source": {
            "official_url": "https://www.cffex.com.cn",
            "verified_by": "test-only-human-review",
            "verified_at": "2026-11-22",
        },
        "sessions": [
            {"symbol": "IF2612", "exchange": "CFFEX", "trading_day": "2026-11-23",
             "phase": "continuous", "start": "2026-11-23T09:30:00+08:00",
             "end": "2026-11-23T11:30:00+08:00"},
            {"symbol": "IF2612", "exchange": "CFFEX", "trading_day": "2026-11-23",
             "phase": "continuous", "start": "2026-11-23T13:00:00+08:00",
             "end": "2026-11-23T15:00:00+08:00"},
            {"symbol": "cu2701", "exchange": "SHFE", "trading_day": "2026-11-24",
             "phase": "continuous", "start": "2026-11-23T21:00:00+08:00",
             "end": "2026-11-24T01:00:00+08:00"},
            {"symbol": "IF2612", "exchange": "CFFEX", "trading_day": "2026-11-23",
             "phase": "auction", "start": "2026-11-23T09:15:00+08:00",
             "end": "2026-11-23T09:30:00+08:00"},
        ],
    })


@pytest.mark.parametrize(("at", "expected"), [
    (_dt("2026-11-22", "14:22:00"), False),  # Sunday
    (_dt("2026-11-23", "09:20:00"), False),  # Auction
    (_dt("2026-11-23", "09:31:00"), True),
    (_dt("2026-11-23", "11:45:00"), False),  # Lunch
    (_dt("2026-11-23", "14:10:00"), True),
    (_dt("2026-11-23", "15:00:00"), False),  # end exclusive
    (_dt("2026-11-24", "09:31:00"), False),  # Not verified for next day
])
def test_explicit_cffex_sessions(at: datetime, expected: bool) -> None:
    assert session_is_tradable("IF2612.CFFEX", at=at, policy=_policy()) is expected


def test_night_session_matches_next_trading_day() -> None:
    policy = _policy()
    assert policy.trading_day("cu2701.SHFE", _dt("2026-11-23", "23:10:00")) == "2026-11-24"
    assert session_is_tradable("cu2701.SHFE", at=_dt("2026-11-24", "00:30:00"), policy=policy)
    assert not session_is_tradable("cu2701.SHFE", at=_dt("2026-11-24", "02:00:00"), policy=policy)


def test_session_unknown_exchange_symbol_or_calendar_fails_closed() -> None:
    p = _policy()
    assert not session_is_tradable("IF2612.SHFE", at=_dt("2026-11-23", "09:40:00"), policy=p)
    assert not session_is_tradable("IF2701.CFFEX", at=_dt("2026-11-23", "09:40:00"), policy=p)
    assert not session_is_tradable("IF2612.CFFEX", at=_dt("2026-11-23", "09:40:00"), policy=None)


def test_timezone_utc_conversion_is_independent_of_machine_timezone() -> None:
    utc = datetime(2026, 11, 23, 1, 40, tzinfo=timezone.utc)
    assert session_is_tradable("IF2612.CFFEX", at=utc, policy=_policy())


def test_reject_ambiguous_unaudited_calendar() -> None:
    data = {
        "version": 1, "source": {},
        "sessions": [{"symbol":"IF2612","exchange":"CFFEX",
                      "start":"2026-11-23T09:30:00","end":"2026-11-23T15:00:00",
                      "phase":"continuous","trading_day":"2026-11-23"}],
    }
    with pytest.raises(ValueError):
        SessionPolicy.from_mapping(data)


def test_bad_verified_weekend_trading_window_cannot_open_market() -> None:
    """Human-configured Sunday continuous phase must not enable orders."""
    source = {
        "official_url": "https://www.cffex.com.cn",
        "verified_by": "unit-test", "verified_at": "2026-11-22",
    }
    sunday = {
        "symbol": "IF2612", "exchange": "CFFEX",
        "trading_day": "2026-11-22", "phase": "continuous",
        "start": "2026-11-22T09:30:00+08:00",
        "end": "2026-11-22T11:30:00+08:00",
    }
    with pytest.raises(ValueError):
        SessionPolicy.from_mapping({"version": 1, "source": source, "sessions": [sunday]})

    sunday["trading_day"] = "2026-11-23"
    with pytest.raises(ValueError):
        SessionPolicy.from_mapping({"version": 1, "source": source, "sessions": [sunday]})


def test_operator_session_file_reloads_and_fails_closed_when_removed(tmp_path, monkeypatch) -> None:
    from vnpy_optionmaster.trading_sessions import (
        SESSION_FILE_ENV, load_verified_policy
    )

    policy_file = tmp_path / "authorized_session.json"
    source = {
        "official_url": "https://www.cffex.com.cn",
        "verified_by": "unit-test", "verified_at": "2026-11-22",
    }
    record = {
        "symbol": "IF2612", "exchange": "CFFEX",
        "trading_day": "2026-11-23", "phase": "continuous",
        "start": "2026-11-23T09:30:00+08:00",
        "end": "2026-11-23T11:30:00+08:00",
    }
    policy_file.write_text(json.dumps({
        "version": 1, "source": source, "sessions": [record]
    }), encoding="utf-8")
    monkeypatch.setenv(SESSION_FILE_ENV, str(policy_file))
    assert load_verified_policy() is not None
    assert session_is_tradable("IF2612.CFFEX", at=_dt("2026-11-23", "10:00:00"))

    policy_file.unlink()
    assert load_verified_policy() is None
    assert not session_is_tradable("IF2612.CFFEX", at=_dt("2026-11-23", "10:00:00"))
