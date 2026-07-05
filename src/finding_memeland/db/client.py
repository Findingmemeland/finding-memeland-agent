"""Supabase client + repository over the game tables.

Implements the Orchestrator's HuntRepo port (and the holdings sample methods)
against the schema in db/schema.sql. The supabase client is INJECTED, and the
import is lazy (inside make_client), so this module stays importable/testable
without supabase installed; tests drive the Repo with a fake client.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any


def make_client(url: str, service_role_key: str):
    """Create a Supabase client (server-side, service role / secret key)."""
    from supabase import create_client

    return create_client(url, service_role_key)


def _clean(fields: dict[str, Any]) -> dict[str, Any]:
    """Serialize values Supabase can't take directly (datetimes -> ISO)."""
    out: dict[str, Any] = {}
    for k, v in fields.items():
        out[k] = v.isoformat() if isinstance(v, datetime) else v
    return out


class Repo:
    def __init__(self, client):
        self._db = client

    # --- hunts ---
    def create_hunt(self, **fields: Any) -> int:
        resp = self._db.table("hunts").insert(_clean(fields)).execute()
        return resp.data[0]["id"]

    def set_hunt_state(self, hunt_id: int, state: str, **fields: Any) -> None:
        self._db.table("hunts").update(_clean({"state": state, **fields})).eq(
            "id", hunt_id
        ).execute()

    def active_hunts(self) -> list[dict[str, Any]]:
        """Hunts a dead process left in a non-terminal state (crash resume)."""
        resp = (
            self._db.table("hunts").select("*")
            .in_("state", ["preparing", "live", "resolving", "paying",
                           "pending_cleanup", "retiring"])
            .execute()
        )
        return resp.data or []

    # --- clues ---
    def record_clue(self, **fields: Any) -> None:
        self._db.table("clues_history").insert(_clean(fields)).execute()

    def clues_for_hunt(self, hunt_id: int) -> list[dict[str, Any]]:
        resp = (
            self._db.table("clues_history").select("*").eq("hunt_id", hunt_id)
            .order("clue_index").execute()
        )
        return resp.data or []

    # --- submissions (public audit log) ---
    def log_submission(self, **fields: Any) -> None:
        self._db.table("submissions").insert(_clean(fields)).execute()

    def submissions_for_hunt(self, hunt_id: int) -> list[dict[str, Any]]:
        resp = (
            self._db.table("submissions").select("*").eq("hunt_id", hunt_id)
            .order("x_created_at").execute()
        )
        return resp.data or []

    # --- winners / payouts ---
    def record_winner(self, **fields: Any) -> None:
        self._db.table("winners").insert(_clean(fields)).execute()

    def record_payout(self, **fields: Any) -> None:
        self._db.table("payouts").insert(_clean(fields)).execute()

    def create_payout_intent(self, *, hunt_id: int, wallet: str, amount_fmml: int) -> int:
        """Write the payout INTENT (status='sending') BEFORE broadcasting the tx.
        This is the idempotency anchor: if the process dies mid-send, the intent
        row proves a transfer may be in flight and blocks any blind retry."""
        resp = self._db.table("payouts").insert(_clean({
            "hunt_id": hunt_id, "wallet": wallet,
            "amount_fmml": amount_fmml, "status": "sending",
        })).execute()
        return resp.data[0]["id"]

    def set_payout_status(self, payout_id: int, status: str, **fields: Any) -> None:
        self._db.table("payouts").update(_clean({"status": status, **fields})).eq(
            "id", payout_id
        ).execute()

    def payouts_for_hunt(self, hunt_id: int) -> list[dict[str, Any]]:
        resp = self._db.table("payouts").select("*").eq("hunt_id", hunt_id).execute()
        return resp.data or []

    # --- holdings ---
    def add_holding_sample(self, wallet: str, balance: int) -> None:
        self._db.table("holding_samples").insert(
            {"wallet": wallet, "balance_fmml": balance}
        ).execute()

    def holding_samples(self, wallet: str, since) -> list[dict[str, Any]]:
        since_s = since.isoformat() if isinstance(since, datetime) else since
        resp = (
            self._db.table("holding_samples").select("*").eq("wallet", wallet)
            .gte("sampled_at", since_s).execute()
        )
        return resp.data or []

    # --- persona pipeline (for a future Supabase-backed PersonaSource) ---
    def next_ready_persona(self) -> dict[str, Any] | None:
        resp = (
            self._db.table("personas").select("*").eq("state", "ready")
            .order("ready_at").limit(1).execute()
        )
        rows = resp.data or []
        return rows[0] if rows else None

    def get_persona(self, persona_id: str) -> dict[str, Any] | None:
        resp = self._db.table("personas").select("*").eq("id", persona_id).execute()
        rows = resp.data or []
        return rows[0] if rows else None

    def set_persona_state(self, persona_id: str, state: str, **fields: Any) -> None:
        self._db.table("personas").update(_clean({"state": state, **fields})).eq(
            "id", persona_id
        ).execute()

    def create_persona(self, *, handle: str, x_user_id: str, oauth_ref: str, state: str = "ready", **fields: Any) -> str:
        resp = self._db.table("personas").insert(
            _clean({"handle": handle, "x_user_id": x_user_id, "oauth_ref": oauth_ref,
                    "state": state, **fields})
        ).execute()
        return resp.data[0]["id"]

    # --- approval queue (non-game posts) ---
    def create_approval(self, *, kind: str, draft_text: str, telegram_msg_id: str | None = None) -> int:
        resp = self._db.table("approval_queue").insert(
            {"kind": kind, "draft_text": draft_text, "telegram_msg_id": telegram_msg_id}
        ).execute()
        return resp.data[0]["id"]

    def get_approval(self, approval_id: int) -> dict[str, Any] | None:
        resp = self._db.table("approval_queue").select("*").eq("id", approval_id).execute()
        rows = resp.data or []
        return rows[0] if rows else None

    def set_approval_status(self, approval_id: int, status: str) -> None:
        from datetime import datetime, timezone
        self._db.table("approval_queue").update(
            {"status": status, "decided_at": datetime.now(timezone.utc).isoformat()}
        ).eq("id", approval_id).execute()
