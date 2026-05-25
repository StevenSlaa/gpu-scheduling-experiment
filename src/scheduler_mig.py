from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import AsyncIterator

from src.errors import JobRejectedError
from src.job_generator import JobSpec


@dataclass(frozen=True)
class Assignment:
    device: str


class MigScheduler:
    def __init__(self, strategy: dict) -> None:
        self.partition_memory_gb = int(strategy["partition_memory_gb"])
        self._devices: asyncio.Queue[str] = asyncio.Queue()
        for device in strategy["devices"]:
            self._devices.put_nowait(device)

    @asynccontextmanager
    async def acquire(self, job: JobSpec) -> AsyncIterator[Assignment]:
        if int(job.memory_gb) > self.partition_memory_gb:
            raise JobRejectedError(
                f"{job.job_id} requests {job.memory_gb} GB, exceeds MIG partition size "
                f"{self.partition_memory_gb} GB"
            )
        device = await self._devices.get()
        try:
            yield Assignment(device=device)
        finally:
            self._devices.put_nowait(device)
