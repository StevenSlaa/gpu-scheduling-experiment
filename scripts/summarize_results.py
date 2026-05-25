from __future__ import annotations

import argparse
import csv
import statistics
from collections import defaultdict
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.config_loader import ROOT
from src.experiment_runner import T_WAIT_SECONDS, percentile


SUMMARY_FIELDS = [
    "strategy",
    "scenario",
    "run_count",
    "total_jobs",
    "completed_jobs",
    "failed_jobs",
    "rejected_jobs",
    "median_wait",
    "p95_wait",
    "mean_wait",
    "std_wait",
    "t_wait_seconds",
    "t_wait_exceeded_count",
    "t_wait_exceeded_pct",
    "t_wait_adequate",
    "mean_wait_per_user",
    "std_wait_per_user",
    "max_user_wait",
    "min_user_wait",
    "mean_gpu_util",
    "mean_queue_depth",
    "max_queue_depth",
]

PER_USER_FIELDS = [
    "strategy",
    "scenario",
    "user_id",
    "mean_wait_seconds",
    "run_count",
]


def main() -> int:
    parser = argparse.ArgumentParser(description="Summarize experiment result directories.")
    parser.add_argument("--results-root", default=ROOT / "results", type=Path)
    parser.add_argument("--output-dir", default=None, type=Path)
    args = parser.parse_args()
    output_dir = args.output_dir or args.results_root
    output_dir.mkdir(parents=True, exist_ok=True)

    run_dirs = [path for path in args.results_root.iterdir() if (path / "jobs.csv").exists()]
    run_rows = [summarize_run_dir(path) for path in run_dirs]
    run_rows = [row for row in run_rows if row]

    write_csv(output_dir / "summary_per_run.csv", run_rows, SUMMARY_FIELDS + ["experiment_id", "run_index"])
    write_grouped(output_dir / "summary_per_strategy.csv", run_rows, ["strategy"])
    write_grouped(output_dir / "summary_per_scenario.csv", run_rows, ["strategy", "scenario"])

    per_user_rows = collect_per_user_rows(run_dirs)
    write_csv(output_dir / "summary_per_user.csv", per_user_rows, PER_USER_FIELDS)

    print(f"Wrote summaries to {output_dir}")
    return 0


def summarize_run_dir(path: Path) -> dict[str, object]:
    jobs = read_csv(path / "jobs.csv")
    if not jobs:
        return {}
    waits = [float(row["wait_time_seconds"]) for row in jobs if row["wait_time_seconds"]]
    exceeded = [w for w in waits if w > T_WAIT_SECONDS]
    rejected = sum(1 for row in jobs if row["status"] == "rejected")

    per_user = _per_user_mean_waits(jobs)
    per_user_values = list(per_user.values())

    queue_rows = read_csv(path / "queue_depth.csv")
    gpu_rows = read_csv(path / "gpu_metrics.csv")
    return {
        "experiment_id": jobs[0]["experiment_id"],
        "strategy": jobs[0]["strategy"],
        "scenario": jobs[0]["scenario"],
        "run_index": jobs[0]["run_index"],
        "run_count": 1,
        "total_jobs": len(jobs),
        "completed_jobs": sum(1 for row in jobs if row["status"] == "completed"),
        "failed_jobs": sum(1 for row in jobs if row["status"] == "failed"),
        "rejected_jobs": rejected,
        "median_wait": round(statistics.median(waits), 3) if waits else "",
        "p95_wait": round(percentile(waits, 95), 3) if waits else "",
        "mean_wait": round(statistics.mean(waits), 3) if waits else "",
        "std_wait": round(statistics.pstdev(waits), 3) if len(waits) > 1 else 0,
        "t_wait_seconds": T_WAIT_SECONDS,
        "t_wait_exceeded_count": len(exceeded),
        "t_wait_exceeded_pct": round(100 * len(exceeded) / len(waits), 1) if waits else "",
        "t_wait_adequate": (statistics.median(waits) < T_WAIT_SECONDS) if waits else "",
        "mean_wait_per_user": round(statistics.mean(per_user_values), 3) if per_user_values else "",
        "std_wait_per_user": round(statistics.pstdev(per_user_values), 3) if len(per_user_values) > 1 else 0,
        "max_user_wait": round(max(per_user_values), 3) if per_user_values else "",
        "min_user_wait": round(min(per_user_values), 3) if per_user_values else "",
        "mean_gpu_util": round(mean_number(row["gpu_util_percent"] for row in gpu_rows), 3),
        "mean_queue_depth": round(mean_number(row["queue_depth"] for row in queue_rows), 3),
        "max_queue_depth": max_number(row["queue_depth"] for row in queue_rows),
    }


def _per_user_mean_waits(jobs: list[dict[str, str]]) -> dict[str, float]:
    user_waits: dict[str, list[float]] = defaultdict(list)
    for row in jobs:
        if row["wait_time_seconds"]:
            user_waits[row["user_id"]].append(float(row["wait_time_seconds"]))
    return {uid: statistics.mean(ws) for uid, ws in user_waits.items() if ws}


def collect_per_user_rows(run_dirs: list[Path]) -> list[dict[str, object]]:
    # Accumulate per-(strategy, scenario, user_id) across runs
    key_waits: dict[tuple[str, str, str], list[float]] = defaultdict(list)
    for path in run_dirs:
        jobs = read_csv(path / "jobs.csv")
        if not jobs:
            continue
        strategy = jobs[0]["strategy"]
        scenario = jobs[0]["scenario"]
        per_user = _per_user_mean_waits(jobs)
        for user_id, mean_wait in per_user.items():
            key_waits[(strategy, scenario, user_id)].append(mean_wait)

    rows = []
    for (strategy, scenario, user_id), waits in sorted(key_waits.items()):
        rows.append({
            "strategy": strategy,
            "scenario": scenario,
            "user_id": user_id,
            "mean_wait_seconds": round(statistics.mean(waits), 3),
            "run_count": len(waits),
        })
    return rows


def write_grouped(path: Path, rows: list[dict[str, object]], keys: list[str]) -> None:
    groups: dict[tuple[object, ...], list[dict[str, object]]] = defaultdict(list)
    for row in rows:
        groups[tuple(row[key] for key in keys)].append(row)
    output: list[dict[str, object]] = []
    for grouped_rows in groups.values():
        waits = [float(row["mean_wait"]) for row in grouped_rows if row["mean_wait"] != ""]
        total_jobs = sum(int(row["total_jobs"]) for row in grouped_rows)
        rejected = sum(int(row.get("rejected_jobs", 0) or 0) for row in grouped_rows)
        t_exceeded = sum(int(row.get("t_wait_exceeded_count", 0) or 0) for row in grouped_rows)
        jobs_with_waits = total_jobs - rejected
        median_w = round(statistics.median(waits), 3) if waits else ""
        per_user_means = [float(row["mean_wait_per_user"]) for row in grouped_rows if row.get("mean_wait_per_user") not in ("", None)]
        per_user_stds = [float(row["std_wait_per_user"]) for row in grouped_rows if row.get("std_wait_per_user") not in ("", None)]
        max_user = [float(row["max_user_wait"]) for row in grouped_rows if row.get("max_user_wait") not in ("", None)]
        min_user = [float(row["min_user_wait"]) for row in grouped_rows if row.get("min_user_wait") not in ("", None)]
        output.append(
            {
                "strategy": grouped_rows[0]["strategy"],
                "scenario": grouped_rows[0].get("scenario", "all") if "scenario" in keys else "all",
                "run_count": len(grouped_rows),
                "total_jobs": total_jobs,
                "completed_jobs": sum(int(row["completed_jobs"]) for row in grouped_rows),
                "failed_jobs": sum(int(row["failed_jobs"]) for row in grouped_rows),
                "rejected_jobs": rejected,
                "median_wait": median_w,
                "p95_wait": round(percentile(waits, 95), 3) if waits else "",
                "mean_wait": round(statistics.mean(waits), 3) if waits else "",
                "std_wait": round(statistics.pstdev(waits), 3) if len(waits) > 1 else 0,
                "t_wait_seconds": T_WAIT_SECONDS,
                "t_wait_exceeded_count": t_exceeded,
                "t_wait_exceeded_pct": round(100 * t_exceeded / jobs_with_waits, 1) if jobs_with_waits else "",
                "t_wait_adequate": (float(median_w) < T_WAIT_SECONDS) if median_w != "" else "",
                "mean_wait_per_user": round(statistics.mean(per_user_means), 3) if per_user_means else "",
                "std_wait_per_user": round(statistics.mean(per_user_stds), 3) if per_user_stds else "",
                "max_user_wait": round(max(max_user), 3) if max_user else "",
                "min_user_wait": round(min(min_user), 3) if min_user else "",
                "mean_gpu_util": round(mean_number(row["mean_gpu_util"] for row in grouped_rows), 3),
                "mean_queue_depth": round(mean_number(row["mean_queue_depth"] for row in grouped_rows), 3),
                "max_queue_depth": max_number(row["max_queue_depth"] for row in grouped_rows),
            }
        )
    write_csv(path, output, SUMMARY_FIELDS)


def read_csv(path: Path) -> list[dict[str, str]]:
    if not path.exists():
        return []
    with path.open("r", newline="", encoding="utf-8") as handle:
        return list(csv.DictReader(handle))


def write_csv(path: Path, rows: list[dict[str, object]], fieldnames: list[str]) -> None:
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def mean_number(values) -> float:
    numbers = [float(value) for value in values if value not in ("", None, "unavailable")]
    return statistics.mean(numbers) if numbers else 0.0


def max_number(values) -> float:
    numbers = [float(value) for value in values if value not in ("", None, "unavailable")]
    return max(numbers) if numbers else 0.0


if __name__ == "__main__":
    raise SystemExit(main())
