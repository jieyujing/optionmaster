"""In-memory TEST-ONLY session shim for isolated order unit tests.

Never install or expose this policy to production. This stub deliberately
does not read, invent, or validate exchange calendar dates: unit tests
covering order calculations require a synthetic open state regardless of
the current day; real SessionPolicy.from_mapping remains fail-closed.
"""
from datetime import datetime
from vnpy_optionmaster.trading_sessions import SessionPolicy


class _SyntheticOpenSession(SessionPolicy):
    def __init__(self, vt_symbol: str) -> None:
        super().__init__((), {"verified_by": "SYNTHETIC UNIT TEST ONLY"})
        self.vt_symbol = vt_symbol

    def is_tradable(self, vt_symbol: str, at: datetime) -> bool:
        return vt_symbol == self.vt_symbol


def synthetic_session_policy(vt_symbol: str) -> SessionPolicy:
    return _SyntheticOpenSession(vt_symbol)
