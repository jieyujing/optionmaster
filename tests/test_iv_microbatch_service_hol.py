"""Synthetic owner-integrated HOL timing, without injected numerical sleep."""
from __future__ import annotations

import pytest

from script.benchmark_iv_microbatch_service_hol import (
    measure_numerical_hol,
    run_study,
)


@pytest.mark.parametrize("batch_size", (1, 4, 8))
@pytest.mark.parametrize("event_kind", ("option", "underlying"))
@pytest.mark.parametrize("cache_state", ("cold", "warm"))
def test_real_numerics_held_risk_recovers_without_premature_actionability(
    batch_size, event_kind, cache_state,
):
    row = measure_numerical_hol(
        batch_size=batch_size, seed=20261123, event_kind=event_kind,
        cache_state=cache_state,
    )
    assert row["portfolio_risk_actionable"]
    assert row["first_held_next_batch"]
    assert row["injected_worker_sleep_ms"] == 0
    assert row["gate_hold_excluded_from_service"]
    assert row["ordinary_batch_size"] == batch_size
    assert row["held_count"] == (1 if event_kind == "option" else 3)
    assert row["scheduler_overflows"] == 0
    assert row["ordinary_numerical_service_ms"] >= 0
    assert row["ordinary_compute_start_after_release_ms"] >= 0
    assert row["first_held_dispatch_after_release_ms"] >= 0
    assert row["tick_to_portfolio_ready_ms"] >= (
        row["first_held_dispatch_after_release_ms"]
    )
    assert row["last_held_result_to_portfolio_ready_ms"] >= 0


@pytest.mark.parametrize("config", [
    {"batch_size": 0},
    {"batch_size": 2},
    {"batch_size": 9},
    {"batch_size": 4, "event_kind": "ordinary"},
    {"batch_size": 4, "cache_state": "broken"},
    {"batch_size": 8, "option_count": 24},
])
def test_noninjected_study_rejects_unbounded_configuration(config):
    with pytest.raises(ValueError):
        measure_numerical_hol(seed=20261123, **config)


def test_study_summary_is_not_a_production_tail_claim(monkeypatch):
    import script.benchmark_iv_microbatch_service_hol as module

    def fake_measure(*, batch_size, seed, event_kind, cache_state):
        return {
            "seed": seed, "batch_size": batch_size,
            "event_kind": event_kind, "cache_state": cache_state,
            "ordinary_numerical_service_ms": float(batch_size),
            "tick_to_portfolio_ready_ms": float(batch_size + 1),
            "first_held_dispatch_after_release_ms": float(batch_size / 2),
        }

    monkeypatch.setattr(module, "measure_numerical_hol", fake_measure)
    report = run_study(
        seeds=(20261123, 20261124), cache_states=("cold", "warm"),
    )
    assert report["synthetic"]
    assert report["production_approved"] is False
    assert report["worker_sleep_injected"] is False
    assert report["batch1_not_production_worker"]
    assert report["test_only_gate_used"]
    assert report["n_records"] == 24
    assert len(report["summary"]) == 12
    assert [row["median_numerical_service_ms"]
            for row in report["summary"][:3]] == [1, 4, 8]
