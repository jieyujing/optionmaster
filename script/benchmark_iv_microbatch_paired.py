"""Bounded OFFLINE interleaved comparison of identical prototype IPC (caps 1/4/8).

Only synthetic owner-integrated full-portfolio risk readiness is measured.
A batch=1 prototype is NOT the production SingleIVWorker control.
"""
from __future__ import annotations

import argparse
import json
import platform
from datetime import datetime, timezone
from pathlib import Path
from statistics import median

if __package__:
    from .benchmark_iv_microbatch_integration import replay_microbatch
else:
    from benchmark_iv_microbatch_integration import replay_microbatch


def paired_replay(*, options_count: int = 24, seeds: tuple[int, ...] = (
    20261123, 20261124, 20261125,
)) -> dict:
    """Rotate treatment order to reduce deterministic first-run ordering bias."""
    if options_count not in (6, 12, 24) or not 1 <= len(seeds) <= 3:
        raise ValueError("Bounded options (6/12/24) and 1..3 seeds only")
    records = []
    orders = ((1, 4, 8), (8, 1, 4), (4, 8, 1))
    for index, seed in enumerate(seeds):
        for cap in orders[index]:
            result = replay_microbatch(
                options_count=options_count, events=options_count * 6,
                seed=seed, batch_size=cap, burst=False, wait_seconds=5.0,
            )
            records.append({
                "seed": seed, "batch_size": cap,
                "p95_ms": result["paced_event_to_ready_p95_ms"],
                "p99_ms": result["paced_event_to_ready_p99_ms"],
                "ingress_p95_ms": result["ingress_p95_ms"],
                "pending_peak": result["pending_peak_observed"],
                "superseded": result["scheduler_superseded"],
                "stale": result["stale_responses"],
                "parent_cpu_s": result["parent_cpu_s"],
                "worker_cpu_s_after_reap": result["worker_cpu_s_after_reap"],
                "actionable": result["portfolio_actionable_ready_end"],
            })
    by_seed = {
        str(seed): {
            str(cap): next(row for row in records if row["seed"] == seed and row["batch_size"] == cap)["p95_ms"]
            for cap in (1, 4, 8)
        }
        for seed in seeds
    }
    medians = {
        str(cap): median(row["p95_ms"] for row in records if row["batch_size"] == cap)
        for cap in (1, 4, 8)
    }
    return {
        "kind": "offline_microbatch_interleaved_same_prototype",
        "timestamp_utc": datetime.now(timezone.utc).isoformat(),
        "platform": platform.platform(),
        "python": platform.python_version(),
        "synthetic": True, "real_feed": False, "production_approved": False,
        "options": options_count, "events": options_count * 6,
        "batch1_not_production_singleworker": True,
        "inflight_preemption": False,
        "orders": [list(orders[i]) for i in range(len(seeds))],
        "records": records, "paired_seed_p95_ms": by_seed,
        "median_run_p95_ms": medians,
        "limitations": [
            "Sequential rotated treatments within each seed, not simultaneous randomized pairing",
            "No isolated timing attribution for in-flight HOL wait or risk-class tail",
            "No real CTP/source chronology, market regimes or G5 approved SLOs",
            "No production numerical selftest; sampled CPU is not capacity high-water",
        ],
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--options", type=int, choices=(6, 12, 24), default=24)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    result = paired_replay(options_count=args.options)
    rendered = json.dumps(result, ensure_ascii=False, indent=2, allow_nan=False) + "\n"
    if args.output:
        args.output.write_text(rendered, encoding="utf-8")
    print(rendered)


if __name__ == "__main__":
    main()
