from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import AsyncIterator

from src.job_generator import JobSpec


@dataclass(frozen=True)
class Assignment:
    device: str


class QueueScheduler:
    def __init__(self, strategy: dict) -> None:
        self.devices = list(strategy["devices"])
        self._queue: asyncio.Queue[str] = asyncio.Queue()
        for device in self.devices:
            self._queue.put_nowait(device)

    @asynccontextmanager
    async def acquire(self, job: JobSpec) -> AsyncIterator[Assignment]:
        device = await self._queue.get()
        try:
            yield Assignment(device=device)
        finally:
            self._queue.put_nowait(device)
