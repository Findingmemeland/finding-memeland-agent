"""Supervisor — watchdog over the whole agent.

Monitors the other modules; on anomaly (rate-limit storms, contradictory state,
illegal state transition, suspected exploit) it pauses the agent and notifies
the admin over Telegram. Pauses are disclosed publicly. Also owns the /silence
kill switch state.
"""

from __future__ import annotations

import asyncio


class Supervisor:
    def __init__(self, *, notify_admin):
        self._notify_admin = notify_admin   # async callable(text)
        self._paused = asyncio.Event()

    @property
    def is_paused(self) -> bool:
        return self._paused.is_set()

    async def pause(self, reason: str) -> None:
        self._paused.set()
        await self._notify_admin(f"⏸️ agent paused: {reason}")

    async def resume(self) -> None:
        self._paused.clear()
        await self._notify_admin("▶️ agent resumed")

    async def guard(self) -> None:
        """Raise/await if paused so callers can cooperatively halt.

        TODO(step 25): add concrete anomaly detectors (rate-limit counters,
        transition validation hooks, balance sanity checks) that call pause().
        """
        if self.is_paused:
            await self._notify_admin("blocked: agent is paused")
            raise RuntimeError("agent is paused")
