from __future__ import annotations

import argparse
import csv
import statistics
from collections import defaultdict
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.config_loader import ROOT
from src.experiment_runner import percentile


SUMMARY_FIELDS = [
    "strategy",
    "scenario",
    "run_count",
    "total_jobs",
    "completed_jobs",
    "failed_jobs",
    "median_wait",
    "p95_wait",
    "mean_wait",
    "std_wait",
    "mean_gpu_util",
    "mean_queue_depth",
    "max_queue_depth",
]


def main() -> int:
    parser = argparse.ArgumentParser(description="Summarize experiment result directories.")
    parser.add_argument("--results-root", default=ROOT / "results", type=Path)
    parser.add_argument("--output-dir", default=None, type=Path)
    args = parser.parse_args()
    output_dir = args.output_dir or args.results_root
    output_dir.mkdir(parents=True, exist_ok=True)

    run_rows = [summarize_run_dir(path) for path in args.results_root.iterdir() if (path / "jobs.csv").exists()]
    run_rows = [row for row in run_rows if row]
    write_csv(output_dir / "summary_per_run.csv", run_rows, SUMMARY_FIELDS + ["experiment_id", "run_index"])
    write_grouped(output_dir / "summary_per_strategy.csv", run_rows, ["strategy"])
    write_grouped(output_dir / "summary_per_scenario.csv", run_rows, ["strategy", "scenario"])
    print(f"Wrote summaries to {output_dir}")
    return 0


def summarize_run_dir(path: Path) -> dict[str, object]:
    jobs = read_csv(path / "jobs.csv")
    if not jobs:
        return {}
    waits = [float(row["wait_time_seconds"]) for row in jobs if row["wait_time_seconds"]]
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
        "median_wait": round(statistics.median(waits), 3) if waits else "",
        "p95_wait": round(percentile(waits, 95), 3) if waits else "",
        "mean_wait": round(statistics.mean(waits), 3) if waits else "",
        "std_wait": round(statistics.pstdev(waits), 3) if len(waits) > 1 else 0,
        "mean_gpu_util": round(mean_number(row["gpu_util_percent"] for row in gpu_rows), 3),
        "mean_queue_depth": round(mean_number(row["queue_depth"] for row in queue_rows), 3),
        "max_queue_depth": max_number(row["queue_depth"] for row in queue_rows),
    }


def write_grouped(path: Path, rows: list[dict[str, object]], keys: list[str]) -> None:
    groups: dict[tuple[object, ...], list[dict[str, object]]] = defaultdict(list)
    for row in rows:
        groups[tuple(row[key] for key in keys)].append(row)
    output: list[dict[str, object]] = []
    for grouped_rows in groups.values():
        waits = [float(row["mean_wait"]) for row in grouped_rows if row["mean_wait"] != ""]
        output.append(
            {
                "strategy": grouped_rows[0]["strategy"],
                "scenario": grouped_rows[0].get("scenario", "all") if "scenario" in keys else "all",
                "run_count": len(grouped_rows),
                "total_jobs": sum(int(row["total_jobs"]) for row in grouped_rows),
                "completed_jobs": sum(int(row["completed_jobs"]) for row in grouped_rows),
                "failed_jobs": sum(int(row["failed_jobs"]) for row in grouped_rows),
                "median_wait": round(statistics.median(waits), 3) if waits else "",
                "p95_wait": round(percentile(waits, 95), 3) if waits else "",
                "mean_wait": round(statistics.mean(waits), 3) if waits else "",
                "std_wait": round(statistics.pstdev(waits), 3) if len(waits) > 1 else 0,
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
