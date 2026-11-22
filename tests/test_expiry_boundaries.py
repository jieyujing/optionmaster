"""Expiry-day boundary tests, independent of wall-clock time."""
from datetime import datetime

import vnpy_optionmaster.time as expiry


class _FixedClock(datetime):
    @classmethod
    def now(cls, tz=None):
        return cls(2026, 11, 20, 16, 0)


def test_option_already_expired_today_has_no_remaining_days(monkeypatch) -> None:
    monkeypatch.setattr(expiry, "datetime", _FixedClock)
    assert expiry.calculate_days_to_expiry(datetime(2026, 11, 20, 15, 0)) == 0


def test_future_same_day_option_remains_live(monkeypatch) -> None:
    monkeypatch.setattr(expiry, "datetime", _FixedClock)
    assert expiry.calculate_days_to_expiry(datetime(2026, 11, 20, 17, 0)) > 0


def test_option_object_refreshes_on_intraday_expiry(monkeypatch) -> None:
    from vnpy_optionmaster import base
    from vnpy_optionmaster.base import OptionData

    monkeypatch.setattr(expiry, "datetime", _FixedClock)
    monkeypatch.setattr(base, "datetime", _FixedClock)
    option = object.__new__(OptionData)
    option.option_expiry = datetime(2026, 11, 20, 15, 0)
    option._expiry_date_checked = _FixedClock.now().date()
    option.days_to_expiry = 1
    option.time_to_expiry = 1 / expiry.ANNUAL_DAYS

    option.refresh_expiry()
    assert option.days_to_expiry == 0
    assert option.time_to_expiry == 0

def test_date_only_expiry_is_not_midnight_expiration(monkeypatch) -> None:
    """A date-only field cannot imply a trade cutoff at 00:00."""
    monkeypatch.setattr(expiry, "datetime", _FixedClock)
    assert expiry.calculate_days_to_expiry(datetime(2026, 11, 20)) == 1
    assert expiry.calculate_days_to_expiry(datetime(2026, 11, 19)) == 0
