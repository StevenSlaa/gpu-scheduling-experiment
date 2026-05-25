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
        if job.group == self.reserved_group:
            device = await self._reserved.get()
            source = self._reserved
        elif self.allow_borrow:
            # Try general pool first; fall back to reserved pool when general is idle
            try:
                device = self._general.get_nowait()
                source = self._general
            except asyncio.QueueEmpty:
                device = await self._reserved.get()
                source = self._reserved
        else:
            device = await self._general.get()
            source = self._general
        try:
            yield Assignment(device=device)
        finally:
            source.put_nowait(device)
