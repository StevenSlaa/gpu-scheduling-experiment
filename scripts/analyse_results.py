from __future__ import annotations

"""Read summary_per_run.csv and produce a comparison table (stdout + results/analysis.md)."""

import argparse
import csv
import statistics
from collections import defaultdict
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.config_loader import ROOT
from src.experiment_runner import T_WAIT_SECONDS

SCENARIO_N = {
    "peak_8_users": 8,
    "peak_16_users": 16,
    "peak_32_users": 32,
    "low_demand": 8,
}

BASELINE_STRATEGY = "queue"


def main() -> int:
    parser = argparse.ArgumentParser(description="Analyse summarised experiment results.")
    parser.add_argument("--results-root", default=ROOT / "results", type=Path)
    parser.add_argument("--output", default=None, type=Path)
    args = parser.parse_args()
    summary_path = args.results_root / "summary_per_run.csv"
    if not summary_path.exists():
        print(f"ERROR: {summary_path} not found. Run summarize_results.py first.", file=sys.stderr)
        return 1

    rows = read_csv(summary_path)
    table_lines, interpretation = build_analysis(rows)

    output = "\n".join(table_lines) + "\n\n" + interpretation
    print(output)

    out_path = args.output or args.results_root / "analysis.md"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(output, encoding="utf-8")
    print(f"\nWrote analysis to {out_path}", file=sys.stderr)
    return 0


def build_analysis(rows: list[dict[str, str]]) -> tuple[list[str], str]:
    # Aggregate per (strategy, scenario): mean across runs
    groups: dict[tuple[str, str], list[dict[str, str]]] = defaultdict(list)
    for row in rows:
        groups[(row["strategy"], row["scenario"])].append(row)

    aggregated: list[dict[str, object]] = []
    for (strategy, scenario), run_rows in sorted(groups.items()):
        waits = [float(r["median_wait"]) for r in run_rows if r.get("median_wait") not in ("", None)]
        p95s = [float(r["p95_wait"]) for r in run_rows if r.get("p95_wait") not in ("", None)]
        stds = [float(r["std_wait"]) for r in run_rows if r.get("std_wait") not in ("", None)]
        utils = [float(r["mean_gpu_util"]) for r in run_rows if r.get("mean_gpu_util") not in ("", None)]
        t_pcts = [float(r["t_wait_exceeded_pct"]) for r in run_rows if r.get("t_wait_exceeded_pct") not in ("", None)]
        t_adeqs = [r.get("t_wait_adequate", "") for r in run_rows]
        aggregated.append({
            "strategy": strategy,
            "scenario": scenario,
            "n": SCENARIO_N.get(scenario, "?"),
            "median_wait": statistics.mean(waits) if waits else None,
            "p95_wait": statistics.mean(p95s) if p95s else None,
            "std_wait": statistics.mean(stds) if stds else None,
            "gpu_util": statistics.mean(utils) if utils else None,
            "t_wait_exceeded_pct": statistics.mean(t_pcts) if t_pcts else None,
            "t_wait_adequate": any(str(a).lower() == "true" for a in t_adeqs),
            "run_count": len(run_rows),
        })

    # Compute baseline (queue) median wait per scenario for delta column
    baseline: dict[str, float | None] = {}
    for agg in aggregated:
        if agg["strategy"] == BASELINE_STRATEGY:
            baseline[str(agg["scenario"])] = agg["median_wait"]

    header = (
        "| Strategy | Scenario | N | Runs | Median wait (s) | P95 wait (s) | "
        "Std wait (s) | GPU util (%) | T_wait exceeded (%) | vs baseline (s) |"
    )
    separator = "|---|---|---|---|---|---|---|---|---|---|"
    table_lines = ["## Results comparison table", "", header, separator]

    for agg in aggregated:
        scenario = str(agg["scenario"])
        base = baseline.get(scenario)
        if base is not None and agg["median_wait"] is not None:
            vs_base = f"{agg['median_wait'] - base:+.1f}"  # type: ignore[operator]
        else:
            vs_base = "—"

        flag = "" if agg["t_wait_adequate"] else " ⚠"
        table_lines.append(
            f"| {agg['strategy']}{flag} | {scenario} | {agg['n']} | {agg['run_count']} "
            f"| {_fmt(agg['median_wait'])} | {_fmt(agg['p95_wait'])} "
            f"| {_fmt(agg['std_wait'])} | {_fmt(agg['gpu_util'])} "
            f"| {_fmt(agg['t_wait_exceeded_pct'])} | {vs_base} |"
        )

    # Plain-English interpretation per scenario group
    scenario_groups: dict[str, list[dict[str, object]]] = defaultdict(list)
    for agg in aggregated:
        scenario_groups[str(agg["scenario"])].append(agg)

    interpretations: list[str] = ["", "## Interpretation"]
    for scenario, agg_list in sorted(scenario_groups.items()):
        best = min((a for a in agg_list if a["median_wait"] is not None), key=lambda a: a["median_wait"], default=None)  # type: ignore[arg-type]
        adequate = [a["strategy"] for a in agg_list if a["t_wait_adequate"]]
        interpretations.append(
            f"\n**{scenario}** (N={SCENARIO_N.get(scenario, '?')}): "
            + (
                f"Best median wait is {_fmt(best['median_wait'])} s ({best['strategy']}). "
                if best else ""
            )
            + (
                f"Strategies meeting T_wait < {T_WAIT_SECONDS} s: {', '.join(adequate) or 'none'}. "
                if adequate else f"No strategy met the T_wait threshold of {T_WAIT_SECONDS} s. "
            )
            + (
                f"Baseline (queue) median wait: {_fmt(baseline.get(scenario))} s."
                if baseline.get(scenario) is not None else ""
            )
        )

    return table_lines, "\n".join(interpretations)


def _fmt(value: object) -> str:
    if value is None:
        return "—"
    if isinstance(value, float):
        return f"{value:.1f}"
    return str(value)


def read_csv(path: Path) -> list[dict[str, str]]:
    with path.open("r", newline="", encoding="utf-8") as handle:
        return list(csv.DictReader(handle))


if __name__ == "__main__":
    raise SystemExit(main())
