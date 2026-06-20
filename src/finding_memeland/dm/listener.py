"""DM Listener — polls inbound DMs on the MAIN account only.

Personas have no DM access; all submissions arrive at @FindingMemeland. Polls
every 15-30s, only while a hunt is live. Uses a since_id marker for dedup, stops
when a winner is declared. Empty-inbox polls return nothing and are billed $0.
"""

from __future__ import annotations

import asyncio

from .validator import DMValidator, parse_dm

POLL_INTERVAL_SECONDS = 20


class DMListener:
    def __init__(self, *, x_client, validator: DMValidator, repo):
        self._x = x_client
        self._validator = validator
        self._repo = repo
        self._since_id: str | None = None

    async def run_for_hunt(self, hunt) -> dict:
        """Poll until the first valid submission wins. Returns winner info.

        TODO(step 26): loop —
          1. fetch DMs since self._since_id (cheap; empty = free)
          2. for each new DM in arrival order: parse_dm -> validator.validate
          3. log EVERY attempt to submissions (audit trail, published later)
          4. send the matching canned reply for non-winning outcomes
          5. on first 'won': record winner, stop polling, return
        Canned replies live in content.templates.
        """
        raise NotImplementedError("DM polling loop — implemented in step 26")

    async def _sleep(self) -> None:
        await asyncio.sleep(POLL_INTERVAL_SECONDS)
