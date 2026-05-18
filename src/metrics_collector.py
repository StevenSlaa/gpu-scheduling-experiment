from __future__ import annotations

import csv
import threading
from pathlib import Path
from typing import Callable

from src.result_writer import utc_now_iso


QueueStateProvider = Callable[[], dict[str, int | str]]


class QueueDepthCollector:
    def __init__(
        self,
        output_path: str | Path,
        state_provider: QueueStateProvider,
        interval_seconds: float = 1.0,
    ) -> None:
        self.output_path = Path(output_path)
        self.state_provider = state_provider
        self.interval_seconds = interval_seconds
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        self.output_path.parent.mkdir(parents=True, exist_ok=True)
        self._thread = threading.Thread(target=self._run, name="queue-depth", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=self.interval_seconds + 2)

    def _run(self) -> None:
        with self.output_path.open("w", newline="", encoding="utf-8") as handle:
            fieldnames = [
                "timestamp",
                "strategy",
                "scenario",
                "run_index",
                "queue_depth",
                "running_jobs",
                "completed_jobs",
                "failed_jobs",
            ]
            writer = csv.DictWriter(handle, fieldnames=fieldnames)
            writer.writeheader()
            while not self._stop.is_set():
                writer.writerow({"timestamp": utc_now_iso(), **self.state_provider()})
                handle.flush()
                self._stop.wait(self.interval_seconds)
