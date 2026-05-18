from __future__ import annotations

import argparse
import asyncio
import socket
import statistics
import subprocess
import sys
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from src.config_loader import ROOT, load_hardware, load_scenario, load_strategy
from src.gpu_monitor import GpuMonitor
from src.job_generator import JobSpec, generate_jobs
from src.metrics_collector import QueueDepthCollector
from src.result_writer import ResultWriter, utc_now_iso
from src.scheduler_mig import MigScheduler
from src.scheduler_queue import QueueScheduler
from src.scheduler_reservation import ReservationScheduler


JOB_FIELDS = [
    "experiment_id",
    "strategy",
    "scenario",
    "run_index",
    "job_id",
    "user_id",
    "group",
    "job_type",
    "requested_memory_gb",
    "requested_duration_seconds",
    "submit_time",
    "start_time",
    "end_time",
    "wait_time_seconds",
    "runtime_seconds",
    "status",
    "assigned_device",
    "exit_code",
]


@dataclass
class RunState:
    submitted_jobs: int = 0
    running_jobs: int = 0
    completed_jobs: int = 0
    failed_jobs: int = 0

    @property
    def queue_depth(self) -> int:
        return max(0, self.submitted_jobs - self.running_jobs - self.completed_jobs - self.failed_jobs)


def build_scheduler(strategy: dict) -> QueueScheduler | ReservationScheduler | MigScheduler:
    if strategy["name"] == "queue":
        return QueueScheduler(strategy)
    if strategy["name"] == "reservation":
        return ReservationScheduler(strategy)
    if strategy["name"] == "mig":
        return MigScheduler(strategy)
    raise ValueError(f"Unknown strategy {strategy['name']}")


async def run_experiment(
    strategy_path: Path,
    scenario_path: Path,
    hardware_path: Path,
    run_index: int,
    results_root: Path,
    metrics_interval_seconds: float,
    dry_run: bool,
    time_scale: float,
) -> Path:
    strategy = load_strategy(strategy_path)
    scenario = load_scenario(scenario_path)
    hardware = load_hardware(hardware_path)
    jobs = generate_jobs(scenario)
    scheduler = build_scheduler(strategy)

    experiment_id = (
        f"{datetime.now().strftime('%Y-%m-%d_%H%M%S')}_"
        f"{strategy['name']}_{scenario['name']}_run{run_index:02d}"
    )
    result_dir = results_root / experiment_id
    writer = ResultWriter(result_dir)
    writer.snapshot_configs([strategy_path, scenario_path, hardware_path])

    metadata = {
        "experiment_id": experiment_id,
        "strategy": strategy["name"],
        "scenario": scenario["name"],
        "run_index": run_index,
        "started_at": utc_now_iso(),
        "ended_at": None,
        "hostname": socket.gethostname(),
        "driver_version": read_nvidia_smi_value("--query-gpu=driver_version"),
        "cuda_version": read_nvidia_smi_value("--query-gpu=cuda_version"),
        "gpu_models": read_nvidia_smi_list("--query-gpu=name"),
        "hardware_config": hardware,
        "notes": "dry_run" if dry_run else "",
    }
    writer.write_json("metadata.json", metadata)

    state = RunState()
    job_rows: dict[str, dict[str, Any]] = {
        job.job_id: build_initial_job_row(experiment_id, strategy["name"], scenario["name"], run_index, job)
        for job in jobs
    }
    state_lock = asyncio.Lock()
    jobs_lock = asyncio.Lock()
    active_processes: set[asyncio.subprocess.Process] = set()
    run_started = asyncio.get_running_loop().time()

    monitor = GpuMonitor(result_dir / "gpu_metrics.csv", metrics_interval_seconds)
    queue_collector = QueueDepthCollector(
        result_dir / "queue_depth.csv",
        lambda: {
            "strategy": strategy["name"],
            "scenario": scenario["name"],
            "run_index": run_index,
            "queue_depth": state.queue_depth,
            "running_jobs": state.running_jobs,
            "completed_jobs": state.completed_jobs,
            "failed_jobs": state.failed_jobs,
        },
        metrics_interval_seconds,
    )
    monitor.start()
    queue_collector.start()

    async def write_jobs_snapshot() -> None:
        async with jobs_lock:
            rows = sorted(job_rows.values(), key=lambda row: row["job_id"])
            writer.write_csv("jobs.csv", rows, JOB_FIELDS)

    async def update_job(job_id: str, **updates: Any) -> None:
        async with jobs_lock:
            job_rows[job_id].update(updates)
            rows = sorted(job_rows.values(), key=lambda row: row["job_id"])
            writer.write_csv("jobs.csv", rows, JOB_FIELDS)

    await write_jobs_snapshot()

    async def run_one(job: JobSpec) -> None:
        submit_offset = max(0.0, job.submit_offset_seconds * time_scale)
        await asyncio.sleep(max(0, submit_offset - (asyncio.get_running_loop().time() - run_started)))
        submit_time = utc_now_iso()
        writer.append_event("job_submitted", job_id=job.job_id)
        await update_job(job.job_id, submit_time=submit_time, status="submitted")
        async with state_lock:
            state.submitted_jobs += 1

        start_time = ""
        end_time = ""
        assigned_device = ""
        exit_code = 1
        status = "failed"
        start_monotonic = 0.0
        try:
            async with scheduler.acquire(job) as assignment:
                assigned_device = assignment.device
                start_time = utc_now_iso()
                start_monotonic = asyncio.get_running_loop().time()
                writer.append_event("job_started", job_id=job.job_id, device=assigned_device)
                await update_job(
                    job.job_id,
                    start_time=start_time,
                    wait_time_seconds=seconds_between_iso(submit_time, start_time),
                    status="running",
                    assigned_device=assigned_device,
                )
                async with state_lock:
                    state.running_jobs += 1

                exit_code = await execute_job(job, assigned_device, dry_run, time_scale, active_processes)
                status = "completed" if exit_code == 0 else "failed"
        except asyncio.CancelledError:
            writer.append_event("job_cancelled", job_id=job.job_id)
            status = "failed"
            exit_code = 130
            raise
        except Exception as exc:
            writer.append_event("job_failed_to_start", job_id=job.job_id, error=str(exc))
            status = "failed"
            exit_code = 10
        finally:
            end_time = utc_now_iso()
            runtime = max(0.0, asyncio.get_running_loop().time() - start_monotonic) if start_monotonic else 0.0
            if status == "completed":
                state.completed_jobs += 1
            else:
                state.failed_jobs += 1
            if start_monotonic:
                state.running_jobs -= 1
            writer.append_event("job_finished", job_id=job.job_id, status=status, exit_code=exit_code)
            await update_job(
                job.job_id,
                submit_time=submit_time,
                start_time=start_time,
                end_time=end_time,
                wait_time_seconds=seconds_between_iso(submit_time, start_time) if start_time else "",
                runtime_seconds=round(runtime, 3),
                status=status,
                assigned_device=assigned_device,
                exit_code=exit_code,
            )

    try:
        await asyncio.gather(*(run_one(job) for job in jobs))
    finally:
        await terminate_active_processes(active_processes)
        monitor.stop()
        queue_collector.stop()

    final_rows = sorted(job_rows.values(), key=lambda row: row["job_id"])
    writer.write_csv("jobs.csv", final_rows, JOB_FIELDS)
    writer.write_json("summary.json", summarize_run(final_rows))
    metadata["ended_at"] = utc_now_iso()
    writer.write_json("metadata.json", metadata)
    return result_dir


def build_initial_job_row(
    experiment_id: str,
    strategy_name: str,
    scenario_name: str,
    run_index: int,
    job: JobSpec,
) -> dict[str, Any]:
    return {
        "experiment_id": experiment_id,
        "strategy": strategy_name,
        "scenario": scenario_name,
        "run_index": run_index,
        "job_id": job.job_id,
        "user_id": job.user_id,
        "group": job.group,
        "job_type": job.job_type,
        "requested_memory_gb": job.memory_gb,
        "requested_duration_seconds": job.duration_seconds,
        "submit_time": "",
        "start_time": "",
        "end_time": "",
        "wait_time_seconds": "",
        "runtime_seconds": "",
        "status": "scheduled",
        "assigned_device": "",
        "exit_code": "",
    }


async def execute_job(
    job: JobSpec,
    device: str,
    dry_run: bool,
    time_scale: float,
    active_processes: set[asyncio.subprocess.Process],
) -> int:
    if dry_run:
        await asyncio.sleep(max(0.01, job.duration_seconds * time_scale))
        return 0
    process = await asyncio.create_subprocess_exec(
        sys.executable,
        str(ROOT / "workloads" / "gpu_job.py"),
        "--duration-seconds",
        str(max(1, int(job.duration_seconds * time_scale))),
        "--memory-gb",
        str(job.memory_gb),
        "--device",
        device,
        "--job-id",
        job.job_id,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    active_processes.add(process)
    try:
        stdout, stderr = await process.communicate()
    except asyncio.CancelledError:
        await terminate_process(process)
        raise
    finally:
        active_processes.discard(process)
    if stdout:
        print(stdout.decode(errors="replace").strip())
    if stderr:
        print(stderr.decode(errors="replace").strip(), file=sys.stderr)
    return int(process.returncode or 0)


async def terminate_active_processes(processes: set[asyncio.subprocess.Process]) -> None:
    for process in list(processes):
        await terminate_process(process)
    processes.clear()


async def terminate_process(process: asyncio.subprocess.Process) -> None:
    if process.returncode is not None:
        return
    process.terminate()
    try:
        await asyncio.wait_for(process.wait(), timeout=5)
    except asyncio.TimeoutError:
        process.kill()
        await process.wait()


def summarize_run(rows: list[dict[str, Any]]) -> dict[str, Any]:
    waits = [float(row["wait_time_seconds"]) for row in rows if row["wait_time_seconds"] != ""]
    return {
        "total_jobs": len(rows),
        "completed_jobs": sum(1 for row in rows if row["status"] == "completed"),
        "failed_jobs": sum(1 for row in rows if row["status"] == "failed"),
        "median_wait": statistics.median(waits) if waits else None,
        "p95_wait": percentile(waits, 95) if waits else None,
        "mean_wait": statistics.mean(waits) if waits else None,
        "std_wait": statistics.pstdev(waits) if len(waits) > 1 else 0,
    }


def percentile(values: list[float], percent: float) -> float:
    ordered = sorted(values)
    index = (len(ordered) - 1) * percent / 100
    lower = int(index)
    upper = min(lower + 1, len(ordered) - 1)
    weight = index - lower
    return ordered[lower] * (1 - weight) + ordered[upper] * weight


def seconds_between_iso(start: str, end: str) -> float:
    return round((datetime.fromisoformat(end) - datetime.fromisoformat(start)).total_seconds(), 3)


def read_nvidia_smi_value(query_arg: str) -> str:
    values = read_nvidia_smi_list(query_arg)
    return values[0] if values else ""


def read_nvidia_smi_list(query_arg: str) -> list[str]:
    try:
        completed = subprocess.run(
            ["nvidia-smi", query_arg, "--format=csv,noheader"],
            check=True,
            capture_output=True,
            text=True,
            timeout=10,
        )
    except (FileNotFoundError, subprocess.CalledProcessError, subprocess.TimeoutExpired):
        return []
    return [line.strip() for line in completed.stdout.splitlines() if line.strip()]


def main() -> int:
    parser = argparse.ArgumentParser(description="Run one GPU scheduling experiment.")
    parser.add_argument("--strategy", required=True, type=Path)
    parser.add_argument("--scenario", required=True, type=Path)
    parser.add_argument("--hardware", default=ROOT / "configs" / "hardware.yaml", type=Path)
    parser.add_argument("--run-index", default=1, type=int)
    parser.add_argument("--results-root", default=ROOT / "results", type=Path)
    parser.add_argument("--metrics-interval-seconds", default=1.0, type=float)
    parser.add_argument("--dry-run", action="store_true", help="Exercise orchestration without GPU workload.")
    parser.add_argument("--time-scale", default=1.0, type=float, help="Scale submit offsets and durations.")
    args = parser.parse_args()
    result_dir = asyncio.run(
        run_experiment(
            args.strategy,
            args.scenario,
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


if __name__ == "__main__":
    raise SystemExit(main())
