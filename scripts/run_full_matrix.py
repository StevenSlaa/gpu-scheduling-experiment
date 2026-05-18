from __future__ import annotations

import argparse
import asyncio
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.config_loader import ROOT
from src.experiment_runner import run_experiment


MAIN_STRATEGIES = ["queue", "reservation", "mig"]
MAIN_SCENARIOS = ["peak_16_users", "low_demand"]
PILOT_SCENARIOS = ["peak_8_users"]


def main() -> int:
    parser = argparse.ArgumentParser(description="Run pilot or main experiment matrix.")
    parser.add_argument("--matrix", choices=["pilot", "main"], default="pilot")
    parser.add_argument("--repetitions", type=int, default=None)
    parser.add_argument("--results-root", default=ROOT / "results", type=Path)
    parser.add_argument("--metrics-interval-seconds", default=1.0, type=float)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--time-scale", default=1.0, type=float)
    args = parser.parse_args()

    strategies = MAIN_STRATEGIES
    scenarios = PILOT_SCENARIOS if args.matrix == "pilot" else MAIN_SCENARIOS
    repetitions = args.repetitions if args.repetitions is not None else (1 if args.matrix == "pilot" else 3)

    for strategy in strategies:
        for scenario in scenarios:
            for run_index in range(1, repetitions + 1):
                result_dir = asyncio.run(
                    run_experiment(
                        ROOT / "configs" / "strategies" / f"{strategy}.yaml",
                        ROOT / "configs" / "scenarios" / f"{scenario}.yaml",
                        ROOT / "configs" / "hardware.yaml",
                        run_index,
                        args.results_root,
                        args.metrics_interval_seconds,
                        args.dry_run,
                        args.time_scale,
                    )
                )
                print(result_dir)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
