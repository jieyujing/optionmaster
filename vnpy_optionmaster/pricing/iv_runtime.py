"""Owner-thread IV worker bridge with conservative risk revocation.

A runtime is opt-in for offline evaluation only until performance, order and
platform production gates have been independently accepted.
"""
from threading import get_ident
from typing import Any

from .iv_snapshot import PendingOptionIV, capture_option_iv, commit_option_iv
from .iv_worker import IVDispatcher, IVWorkResult, LatestWinsScheduler, SingleIVWorker


class OptionIVRuntime:
    """One immutable calculation channel; no worker-owned risk state."""

    def __init__(
        self, *, cache_entries: int, queue_capacity: int,
        max_pending: int, max_priority_streak: int,
        inflight_timeout_seconds: float | None = None,
    ) -> None:
        self.owner_thread = get_ident()
        self.worker = SingleIVWorker(cache_entries=cache_entries, queue_capacity=queue_capacity)
        self.dispatcher = IVDispatcher(
            self.worker, LatestWinsScheduler(max_pending, max_priority_streak),
            inflight_timeout_seconds=inflight_timeout_seconds,
        )
        self.options: dict[str, Any] = {}
        self.pending: dict[str, PendingOptionIV] = {}
        self.sequence = 0
        self.accepted = 0
        self.rejected = 0
        self._started = False
        self._closed = False

    def start(self) -> bool:
        if get_ident() != self.owner_thread or self._closed:
            return False
        # Reusing a stopped process would retain stale scheduler/dispatcher
        # work, and can silently cross the Worker generation boundary.
        # Require a newly constructed runtime for an explicit new epoch.
        if self._started:
            return self.worker.alive and not self.dispatcher.timed_out
        if not self.worker.start():
            self._closed = True
            return False
        self._started = True
        return True

    @staticmethod
    def _model_name(option: Any) -> str | None:
        model = getattr(option, "calculate_impv", None)
        module = getattr(model, "__module__", "")
        label = module.rsplit(".", 1)[-1]
        return label if label in ("black_76", "black_scholes") else None

    def attach(self, option: Any) -> bool:
        if (get_ident() != self.owner_thread or self._closed
                or not self._started or not self.worker.alive):
            return False
        if self._model_name(option) is None:
            return False
        previous = self.options.get(option.vt_symbol)
        if previous is not None and previous is not option:
            return False
        self.options[option.vt_symbol] = option
        option.iv_submit = self.submit
        option.iv_runtime = self
        # Never inherit a sync Greek when explicitly attaching the Worker.
        option.greeks_valid = False
        option.impv_valid = False
        option.greeks_quote_version = None
        option.greeks_worker_generation = None
        option.greeks_input_identity = None
        return True

    def submit(self, option: Any) -> None:
        # Revocation MUST precede enqueue, including for missing/invalid inputs.
        option.greeks_valid = False
        option.impv_valid = False
        option.bid_impv_valid = False
        option.ask_impv_valid = False
        option.mid_impv_valid = False
        option.greeks_quote_version = None
        option.greeks_worker_generation = None
        option.greeks_input_identity = None
        self.pending.pop(option.vt_symbol, None)
        if get_ident() != self.owner_thread or not self.worker.alive:
            return
        model = self._model_name(option)
        if model is None or self.options.get(option.vt_symbol) is not option:
            return
        self.sequence += 1
        snapshot = capture_option_iv(
            option, model=model, generation=self.worker.generation,
            sequence=self.sequence,
        )
        if snapshot is None or option is None:
            return
        if self.dispatcher.offer(snapshot.request, active_risk=bool(option.net_pos)):
            self.pending[option.vt_symbol] = snapshot
        else:
            self.rejected += 1
        # Never consume a valid response without main-owner commit handling.
        self.pump()

    def pump(self) -> bool:
        if get_ident() != self.owner_thread:
            return False
        if not self.worker.alive:
            for option in self.options.values():
                option.greeks_valid = False
                option.impv_valid = False
                option.greeks_worker_generation = None
                option.greeks_input_identity = None
            self.pending.clear()
            return False
        result: IVWorkResult | None = self.dispatcher.pump()
        if self.dispatcher.timed_out or not self.worker.alive:
            for option in self.options.values():
                option.greeks_valid = False
                option.impv_valid = False
                option.greeks_quote_version = None
                option.greeks_worker_generation = None
                option.greeks_input_identity = None
            self.pending.clear()
            return False
        if result is None:
            return False
        snapshot = self.pending.get(result.symbol)
        option = self.options.get(result.symbol)
        if snapshot is None or option is None:
            return False
        if (
            snapshot.request.generation != result.generation
            or snapshot.request.sequence != result.sequence
            or snapshot.request.quote_identity != result.quote_identity
        ):
            return False
        self.pending.pop(result.symbol, None)
        if commit_option_iv(option, snapshot, result):
            self.accepted += 1
            return True
        self.rejected += 1
        option.greeks_valid = False
        option.impv_valid = False
        return False

    def close(self) -> None:
        self._closed = True
        for option in self.options.values():
            # Do not detach the hook and silently return to the synchronous
            # pricing path while a trading algorithm may still be active.
            # A stopped Worker keeps risk revoked until explicit reattachment.
            option.greeks_valid = False
            option.impv_valid = False
            option.greeks_quote_version = None
            option.greeks_worker_generation = None
            option.greeks_input_identity = None
            option.iv_runtime = self
        self.pending.clear()
        self.options.clear()
        self.worker.close()
