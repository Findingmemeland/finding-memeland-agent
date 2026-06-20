"""Supabase client + thin repository over the game tables.

Repositories keep SQL/Supabase calls in one place so the rest of the code works
with plain dicts/dataclasses. Methods are stubbed; the schema is in db/schema.sql.
"""

from __future__ import annotations

from typing import Any


def make_client(url: str, service_role_key: str):
    """Create a Supabase client (server-side, service role).

    TODO: `from supabase import create_client; return create_client(url, key)`.
    """
    raise NotImplementedError("supabase client init")


class Repo:
    def __init__(self, client):
        self._db = client

    # --- personas pipeline ---
    def next_ready_persona(self) -> dict[str, Any] | None:
        """Oldest 'ready' persona, or None if the pipeline is empty."""
        raise NotImplementedError

    def set_persona_state(self, persona_id: str, state: str, **fields: Any) -> None:
        raise NotImplementedError

    # --- hunts ---
    def create_hunt(self, **fields: Any) -> int:
        raise NotImplementedError

    def set_hunt_state(self, hunt_id: int, state: str, **fields: Any) -> None:
        raise NotImplementedError

    # --- clues ---
    def record_clue(self, **fields: Any) -> int:
        raise NotImplementedError

    # --- submissions (audit log, published per hunt) ---
    def log_submission(self, **fields: Any) -> int:
        raise NotImplementedError

    def submissions_for_hunt(self, hunt_id: int) -> list[dict[str, Any]]:
        raise NotImplementedError

    # --- winners / payouts ---
    def record_winner(self, **fields: Any) -> int:
        raise NotImplementedError

    def record_payout(self, **fields: Any) -> int:
        raise NotImplementedError

    # --- holdings ---
    def add_holding_sample(self, wallet: str, balance: int) -> None:
        raise NotImplementedError

    def holding_samples(self, wallet: str, since) -> list[dict[str, Any]]:
        raise NotImplementedError
