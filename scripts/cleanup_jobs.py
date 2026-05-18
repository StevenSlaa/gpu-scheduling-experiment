from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.config_loader import ROOT


TARGET_MARKERS = [
    str(ROOT / "workloads" / "gpu_job.py"),
    str(ROOT / "scripts" / "run_single_experiment.py"),
]


def main() -> int:
    parser = argparse.ArgumentParser(description="Terminate toolkit-managed experiment jobs.")
    parser.add_argument("--include-runner", action="store_true", help="Also stop run_single_experiment.py processes.")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    current_pid = str(subprocess.run(
        ["powershell", "-NoProfile", "-Command", "$PID"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip())
    targets = find_target_processes(include_runner=args.include_runner, current_pid=current_pid)
    for process in targets:
        print(f"{'Would stop' if args.dry_run else 'Stopping'} PID {process['ProcessId']}: {process['CommandLine']}")
        if not args.dry_run:
            stop_process(process["ProcessId"])
    print(f"Matched processes: {len(targets)}")
    return 0


def find_target_processes(include_runner: bool, current_pid: str) -> list[dict[str, str]]:
    completed = subprocess.run(
        [
            "powershell",
            "-NoProfile",
            "-Command",
            (
                "Get-CimInstance Win32_Process | "
                "Where-Object { $_.Name -match 'python' } | "
                "Select-Object ProcessId,CommandLine | ConvertTo-Json -Compress"
            ),
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    import json

    if not completed.stdout.strip():
        return []
    payload = json.loads(completed.stdout)
    processes = payload if isinstance(payload, list) else [payload]
    markers = TARGET_MARKERS if include_runner else TARGET_MARKERS[:1]
    targets: list[dict[str, str]] = []
    for process in processes:
        command_line = str(process.get("CommandLine") or "")
        process_id = str(process.get("ProcessId") or "")
        if process_id == current_pid:
            continue
        normalized = command_line.replace("/", "\\").lower()
        if any(marker.replace("/", "\\").lower() in normalized for marker in markers):
            targets.append({"ProcessId": process_id, "CommandLine": command_line})
    return targets


def stop_process(process_id: str) -> None:
    subprocess.run(
        ["powershell", "-NoProfile", "-Command", f"Stop-Process -Id {int(process_id)} -Force"],
        check=False,
        capture_output=True,
        text=True,
    )


if __name__ == "__main__":
    raise SystemExit(main())
