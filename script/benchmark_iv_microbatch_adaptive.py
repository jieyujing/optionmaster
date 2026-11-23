"""Matched-backlog OFFLINE fixed-vs-risk-aware microbatch cap comparison.

Test-only proposal: exposed held positions + no held requests -> ordinary cap1;
two or more held requests pending -> cap4; no held exposure -> cap8.
No production scheduler, default Worker, trade gateway, approved SLO or market feed.
"""
from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path
from statistics import median
from typing import Any

from script.benchmark_iv_microbatch_service_hol import measure_numerical_hol

VARIANTS = ("fixed1", "fixed4", "fixed8", "candidate")


def run_policy_comparison(
    *, seeds: tuple[int, ...] = (20261123, 20261124, 20261125),
    cache_states: tuple[str, ...] = ("cold", "warm"),
) -> dict[str, Any]:
    if not 1 <= len(seeds) <= 3 or not cache_states or any(
        state not in ("cold", "warm") for state in cache_states
    ):
        raise ValueError("Only 1..3 seeds and cold/warm states")
    samples: list[dict[str, Any]] = []
    order: list[dict[str, Any]] = []
    for seed_index, seed in enumerate(seeds):
        for cache_index, state in enumerate(cache_states):
            for kind_index, kind in enumerate(("option", "underlying")):
                offset = (seed_index + cache_index + kind_index) % 4
                variants = VARIANTS[offset:] + VARIANTS[:offset]
                order.append({
                    "seed": seed, "cache_state": state, "event_kind": kind,
                    "order": list(variants),
                })
                for variant in variants:
                    is_candidate = variant == "candidate"
                    cap = 8 if is_candidate else int(variant[-1])
                    result = measure_numerical_hol(
                        batch_size=cap, seed=seed, event_kind=kind,
                        cache_state=state, adaptive=is_candidate,
                        matched_backlog=True,
                    )
                    if (not result["portfolio_risk_actionable"]
                            or result["scheduler_overflows"]
                            or not result["first_held_next_batch"]
                            or result["ordinary_staged_count"] != 8):
                        raise AssertionError("Matched-backlog offline safety invariant")
                    samples.append({"variant": variant, **result})

    rows = []
    for state in cache_states:
        for kind in ("option", "underlying"):
            for variant in VARIANTS:
                matching = [
                    x for x in samples if x["cache_state"] == state
                    and x["event_kind"] == kind and x["variant"] == variant
                ]
                rows.append({
                    "cache_state": state, "event_kind": kind,
                    "variant": variant, "n_seeds": len(seeds),
                    "median_urgent_to_actionable_ms": median(
                        x["tick_to_portfolio_ready_ms"] for x in matching
                    ),
                    "median_ordinary_compute_ms": median(
                        x["ordinary_numerical_service_ms"] for x in matching
                    ),
                    "median_first_held_dispatch_after_release_ms": median(
                        x["first_held_dispatch_after_release_ms"] for x in matching
                    ),
                    "median_held_batches": median(
                        x["held_batch_count"] for x in matching
                    ),
                })
    paired_deltas = [
        {
            "seed": seed, "cache_state": state, "event_kind": kind,
            "candidate_minus_fixed8_ms": next(
                x["tick_to_portfolio_ready_ms"] for x in samples
                if x["seed"] == seed and x["cache_state"] == state
                and x["event_kind"] == kind and x["variant"] == "candidate"
            ) - next(
                x["tick_to_portfolio_ready_ms"] for x in samples
                if x["seed"] == seed and x["cache_state"] == state
                and x["event_kind"] == kind and x["variant"] == "fixed8"
            ),
        }
        for seed in seeds for state in cache_states
        for kind in ("option", "underlying")
    ]
    return {
        "kind": "optionmaster_adaptive_cap_matched_backlog_offline",
        "timestamp_utc": datetime.now(timezone.utc).isoformat(),
        "synthetic": True, "production_approved": False,
        "test_gate_used": True, "injected_numerical_sleep": False,
        "matched_staged_ordinary_requests": 8,
        "batch1_is_experimental_protocol": True,
        "n_runs": len(samples), "seeds": list(seeds),
        "policy": (
            "held positions & pending held<=1: cap1; pending held>=2: cap4; "
            "no held positions: cap8"
        ),
        "orders": order, "summary": rows,
        "paired_candidate_minus_fixed8": paired_deltas,
        "records": samples,
        "limitations": [
            "Illustrative cap heuristic, not derived from an approved tail-risk budget",
            "Experimental Worker gate aligns arrival; it is not natural source-time distribution",
            "Same seed and 8-staged ordinary requests, but process runs remain sequential",
            "Only 3 repeats per cell; no statistical p95/p99 or production performance claim",
            "No real recorded Tick feed, Windows/CTP, rollback, order/send validation",
        ],
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    report = run_policy_comparison()
    rendered = json.dumps(report, ensure_ascii=False, allow_nan=False, indent=2) + "\n"
    if args.output:
        args.output.write_text(rendered, encoding="utf-8")
    print(rendered)


if __name__ == "__main__":
    main()
