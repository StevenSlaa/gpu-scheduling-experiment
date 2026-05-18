from __future__ import annotations

import argparse
import asyncio
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.config_loader import ROOT
from src.experiment_runner import run_experiment


def main() -> int:
    parser = argparse.ArgumentParser(description="Run one strategy/scenario experiment.")
    parser.add_argument("--strategy", required=True, help="Strategy name or YAML path.")
    parser.add_argument("--scenario", required=True, help="Scenario name or YAML path.")
    parser.add_argument("--run-index", default=1, type=int)
    parser.add_argument("--hardware", default=ROOT / "configs" / "hardware.yaml", type=Path)
    parser.add_argument("--results-root", default=ROOT / "results", type=Path)
    parser.add_argument("--metrics-interval-seconds", default=1.0, type=float)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--time-scale", default=1.0, type=float)
    args = parser.parse_args()

    strategy_path = resolve_config("strategies", args.strategy)
    scenario_path = resolve_config("scenarios", args.scenario)
    result_dir = asyncio.run(
        run_experiment(
            strategy_path,
            scenario_path,
            args.hardware,
            args.run_index,
            args.results_root,
            args.metrics_interval_seconds,
            args.dry_run,
            args.time_scale,
        )
    )
    print(result_dir)
    return 0


def resolve_config(kind: str, value: str) -> Path:
    path = Path(value)
    if path.exists():
        return path
    if not value.endswith(".yaml"):
        value = f"{value}.yaml"
    return ROOT / "configs" / kind / value


if __name__ == "__main__":
    raise SystemExit(main())
