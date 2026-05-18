from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import AsyncIterator

from src.job_generator import JobSpec


@dataclass(frozen=True)
class Assignment:
    device: str


class ReservationScheduler:
    def __init__(self, strategy: dict) -> None:
        self.reserved_group = strategy["reserved_pool"]["group"]
        self.allow_borrow = bool(strategy.get("allow_general_to_use_reserved_when_idle", False))
        self._reserved: asyncio.Queue[str] = asyncio.Queue()
        self._general: asyncio.Queue[str] = asyncio.Queue()
        for device in strategy["reserved_pool"]["devices"]:
            self._reserved.put_nowait(device)
        for device in strategy["general_pool"]["devices"]:
            self._general.put_nowait(device)

    @asynccontextmanager
    async def acquire(self, job: JobSpec) -> AsyncIterator[Assignment]:
        pool = self._reserved if job.group == self.reserved_group else self._general
        device = await pool.get()
        try:
            yield Assignment(device=device)
        finally:
            pool.put_nowait(device)
