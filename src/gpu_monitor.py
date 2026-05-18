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
