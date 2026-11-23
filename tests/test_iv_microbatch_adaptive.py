"""Experimental batching policy: no production activation or SLA assumptions."""
from __future__ import annotations

import pytest

from script.benchmark_iv_microbatch_service_hol import (
    candidate_batch_cap,
    measure_numerical_hol,
)
from script.benchmark_iv_microbatch_adaptive import run_policy_comparison


@pytest.mark.parametrize("exposure,pending,expected", [
    (0, 0, 8), (1, 0, 1), (1, 1, 1), (3, 2, 4),
    (3, 3, 4), (10, 8, 4),
])
def test_heuristic_policy_is_bounded(exposure, pending, expected):
    assert candidate_batch_cap(
        held_exposure_count=exposure, pending_held_count=pending,
    ) == expected


@pytest.mark.parametrize("exposure,pending", [
    (-1, 0), (1, -1), (0, 1), (1, 2),
])
def test_candidate_rejects_incoherent_held_priority_inputs(exposure, pending):
    with pytest.raises(ValueError):
        candidate_batch_cap(
            held_exposure_count=exposure, pending_held_count=pending,
        )


@pytest.mark.parametrize("kind", ["option", "underlying"])
@pytest.mark.parametrize("cache_state", ["cold", "warm"])
def test_candidate_real_owner_risk_restoration_and_preemptibility_limit(
    kind, cache_state,
):
    row = measure_numerical_hol(
        batch_size=8, seed=20261123, event_kind=kind,
        cache_state=cache_state, adaptive=True, matched_backlog=True,
    )
    assert row["experimental_only"]
    assert row["policy"] == "risk_aware_candidate"
    assert row["portfolio_risk_actionable"]
    assert row["first_held_next_batch"]
    assert row["ordinary_staged_count"] == 8
    assert row["ordinary_batch_size"] == 1
    assert row["scheduler_overflows"] == 0
    assert row["gate_hold_excluded_from_service"]
    assert row["injected_worker_sleep_ms"] == 0
    if kind == "option":
        assert row["urgent_batch_sizes"] == [1]
    else:
        assert row["held_count"] == 3
        assert row["urgent_batch_sizes"] == [4]


def test_fixed_cap_matched_ordinary_backlog_still_recovers():
    row = measure_numerical_hol(
        batch_size=4, seed=20261123, event_kind="underlying",
        cache_state="cold", matched_backlog=True,
    )
    assert row["policy"] == "fixed"
    assert row["ordinary_staged_count"] == 8
    assert row["ordinary_batch_size"] == 4
    assert row["portfolio_risk_actionable"]
    assert row["scheduler_overflows"] == 0


def test_paired_comparison_keeps_candidate_unapproved(monkeypatch):
    import script.benchmark_iv_microbatch_adaptive as module

    def fake_sample(
        *, batch_size, seed, event_kind, cache_state,
        adaptive, matched_backlog,
    ):
        return {
            "seed": seed, "batch_size": batch_size, "event_kind": event_kind,
            "cache_state": cache_state,
            "portfolio_risk_actionable": True, "scheduler_overflows": 0,
            "first_held_next_batch": True, "ordinary_staged_count": 8,
            "tick_to_portfolio_ready_ms": float(batch_size),
            "ordinary_numerical_service_ms": float(batch_size),
            "first_held_dispatch_after_release_ms": float(batch_size),
            "held_batch_count": 1, "adaptive": adaptive,
        }

    monkeypatch.setattr(module, "measure_numerical_hol", fake_sample)
    report = run_policy_comparison(seeds=(20261123, 20261124))
    assert report["production_approved"] is False
    assert report["synthetic"]
    assert report["test_gate_used"]
    assert report["injected_numerical_sleep"] is False
    assert report["matched_staged_ordinary_requests"] == 8
    assert report["n_runs"] == 32
    assert len(report["summary"]) == 16
    assert len(report["paired_candidate_minus_fixed8"]) == 8
