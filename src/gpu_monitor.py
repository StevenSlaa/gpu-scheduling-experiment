from __future__ import annotations

import csv
import subprocess
import threading
import time
from pathlib import Path

from src.result_writer import utc_now_iso


NVIDIA_SMI_QUERY = [
    "nvidia-smi",
    "--query-gpu=timestamp,index,utilization.gpu,memory.used,memory.total,power.draw,temperature.gpu",
    "--format=csv,noheader,nounits",
]

# Hardware MIG profile 1g.24gb gives 4 instances per RTX PRO 6000 GPU.
# utilization.gpu reports [N/A] in MIG mode, so we substitute slot-occupancy
# (active compute processes ÷ total MIG slots × 100).
MIG_SLOTS_PER_GPU = 4


class GpuMonitor:
    def __init__(self, output_path: str | Path, interval_seconds: float = 1.0) -> None:
        self.output_path = Path(output_path)
        self.interval_seconds = interval_seconds
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        self.output_path.parent.mkdir(parents=True, exist_ok=True)
        self._thread = threading.Thread(target=self._run, name="gpu-monitor", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=self.interval_seconds + 2)

    def _run(self) -> None:
        with self.output_path.open("w", newline="", encoding="utf-8") as handle:
            writer = csv.DictWriter(
                handle,
                fieldnames=[
                    "timestamp",
                    "device",
                    "gpu_util_percent",
                    "memory_used_mb",
                    "memory_total_mb",
                    "power_watts",
                    "temperature_c",
                ],
            )
            writer.writeheader()
            while not self._stop.is_set():
                for row in sample_gpu_metrics():
                    writer.writerow(row)
                handle.flush()
                self._stop.wait(self.interval_seconds)


def sample_gpu_metrics() -> list[dict[str, str]]:
    try:
        completed = subprocess.run(
            NVIDIA_SMI_QUERY,
            check=True,
            capture_output=True,
            text=True,
            timeout=10,
        )
    except (FileNotFoundError, subprocess.CalledProcessError, subprocess.TimeoutExpired):
        return [
            {
                "timestamp": utc_now_iso(),
                "device": "unavailable",
                "gpu_util_percent": "",
                "memory_used_mb": "",
                "memory_total_mb": "",
                "power_watts": "",
                "temperature_c": "",
            }
        ]

    rows: list[dict[str, str]] = []
    for line in completed.stdout.splitlines():
        parts = [part.strip() for part in line.split(",")]
        if len(parts) != 7:
            continue
        _, index, util, memory_used, memory_total, power, temperature = parts
        # MIG-mode GPUs return [N/A] for utilization.gpu.
        # Substitute slot-occupancy: active compute processes / MIG slots * 100.
        if util == "[N/A]":
            util = str(_mig_slot_occupancy(int(index)))
        rows.append(
            {
                "timestamp": utc_now_iso(),
                "device": f"cuda:{index}",
                "gpu_util_percent": util,
                "memory_used_mb": memory_used,
                "memory_total_mb": memory_total,
                "power_watts": power,
                "temperature_c": temperature,
            }
        )
    return rows


def _mig_slot_occupancy(gpu_index: int, slots: int = MIG_SLOTS_PER_GPU) -> float:
    """
    Count active CUDA processes on a MIG-mode physical GPU (each process occupies
    exactly one MIG instance) and return occupancy as 0–100 %.
    """
    try:
        result = subprocess.run(
            [
                "nvidia-smi", "-i", str(gpu_index),
                "--query-compute-apps=pid",
                "--format=csv,noheader,nounits",
            ],
            check=True,
            capture_output=True,
            text=True,
            timeout=5,
        )
    except (FileNotFoundError, subprocess.CalledProcessError, subprocess.TimeoutExpired):
        return 0.0
    active = sum(1 for line in result.stdout.splitlines() if line.strip())
    return min(round(active / slots * 100, 1), 100.0)
