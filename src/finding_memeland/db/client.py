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

    def set_hunt_paused(self, hunt_id: int, paused: bool) -> None:
        """Persisted kill switch (/silence): the pause lives ON the hunt row, so
        it survives restarts, is shared across overlapping deploy instances,
        and is never inherited by the next hunt (post-mortem P3.7)."""
        self._db.table("hunts").update({"paused": paused}).eq("id", hunt_id).execute()

    def get_hunt(self, hunt_id: int) -> dict[str, Any] | None:
        resp = self._db.table("hunts").select("*").eq("id", hunt_id).execute()
        rows = resp.data or []
        return rows[0] if rows else None

    def update_hunt(self, hunt_id: int, **fields: Any) -> None:
        """Update hunt fields WITHOUT touching state (operator controls like
        abort_prep / golive_due_at — persisted, per the post-mortem doctrine)."""
        self._db.table("hunts").update(_clean(fields)).eq("id", hunt_id).execute()

    def active_hunts(self) -> list[dict[str, Any]]:
        """Hunts a dead process left in a non-terminal state (crash resume)."""
        resp = (
            self._db.table("hunts").select("*")
            .in_("state", ["preparing", "prepped", "live", "resolving", "paying",
                           "pending_cleanup", "retiring"])
            .execute()
        )
        return resp.data or []

    def recent_persona_identities(self, n: int = 10) -> list[dict[str, Any]]:
        """Newest-first (display_name, persona_identity) of the last hunts.
        Feeds the generator's avoid_recent (post-mortem P1a): the anti-repeat
        parameter existed and the identities were stored, but nothing ever
        read them back — so every hunt got an identical prompt and the model
        converged on the same archetype (the Penelope repeat)."""
        resp = (
            self._db.table("hunts")
            .select("persona_display_name,persona_identity")
            .order("id", desc=True).limit(n).execute()
        )
        return resp.data or []

    def next_hunt_number(self) -> int:
        """Public hunt numbering, DB-derived: max(hunt_number)+1 (post-mortem
        P3.2 — it was frozen at 1 in code while resume printed the DB id; one
        source of truth now, stored on the row at create time). Voided hunts
        keep their number: the public already saw it."""
        resp = (
            self._db.table("hunts").select("hunt_number")
            .not_.is_("hunt_number", "null")
            .order("hunt_number", desc=True).limit(1).execute()
        )
        rows = resp.data or []
        top = rows[0].get("hunt_number") if rows else 0
        return int(top or 0) + 1

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
    def log_submission(self, **fields: Any) -> int | None:
        """Insert one submission row and return its id — record_winner links
        the winner to this row (P1). None only if the DB returns no row."""
        resp = self._db.table("submissions").insert(_clean(fields)).execute()
        rows = resp.data or []
        return rows[0].get("id") if rows else None

    def set_submission_outcome(self, submission_id: int, outcome: str, **fields: Any) -> None:
        """Update a submission's outcome after the fact (claim-by-post: a
        'pending' claim resolves to won/timed_out/late/... once the public
        wallet flow plays out; the winning row also gets the wallet)."""
        self._db.table("submissions").update(
            _clean({"outcome": outcome, **fields})
        ).eq("id", submission_id).execute()

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

    # --- persona prep posts (P2: anchor posts published in the prep window) ---
    def create_persona_post(self, **fields: Any) -> int:
        resp = self._db.table("persona_posts").insert(_clean(fields)).execute()
        return resp.data[0]["id"]

    def set_persona_post(self, post_id: int, **fields: Any) -> None:
        self._db.table("persona_posts").update(_clean(fields)).eq("id", post_id).execute()

    def persona_posts_for_hunt(self, hunt_id: int) -> list[dict[str, Any]]:
        resp = (
            self._db.table("persona_posts").select("*").eq("hunt_id", hunt_id)
            .order("scheduled_at").execute()
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

    def update_persona(self, persona_id: str, **fields: Any) -> None:
        """Update persona fields WITHOUT touching state (descriptor bookkeeping
        — e.g. anchor_posts as they publish)."""
        self._db.table("personas").update(_clean(fields)).eq("id", persona_id).execute()

    def persona_by_ref_or_handle(self, key: str) -> dict[str, Any] | None:
        """Find a persona by oauth_ref ('07') or handle ('@X' / 'X') — the two
        names the operator knows. oauth_ref first (it's what authorize prints)."""
        key = str(key).strip()
        resp = self._db.table("personas").select("*").eq("oauth_ref", key).execute()
        rows = resp.data or []
        if rows:
            return rows[0]
        handle = key if key.startswith("@") else f"@{key}"
        resp = self._db.table("personas").select("*").eq("handle", handle).execute()
        rows = resp.data or []
        return rows[0] if rows else None

    def dressed_personas(self) -> list[dict[str, Any]]:
        """The pre-dressed pool, oldest dress first (launch picks the most
        indexed; /dress uses the identities for anti-repetition)."""
        resp = (
            self._db.table("personas").select("*").eq("state", "dressed")
            .order("dressed_at").execute()
        )
        return resp.data or []

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
