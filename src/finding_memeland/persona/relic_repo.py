"""Supabase-backed RelicRepo + WalletDirectory (package 6).

Implements the ports from package 1/2 against the `relics` table, following the
conventions of db/client.py: the supabase client is INJECTED, values are cleaned
(datetimes -> ISO), and nothing here imports supabase at module level — so the
module stays importable and testable with a fake client.

Blind mode note: this layer moves `identity_ciphertext` around as an OPAQUE
string. It never decrypts, and there is no plaintext name/code column to read —
by schema design.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from .relic import Relic, RelicState


def _clean(fields: dict[str, Any]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for k, v in fields.items():
        if isinstance(v, datetime):
            out[k] = v.isoformat()
        elif isinstance(v, RelicState):
            out[k] = v.value
        else:
            out[k] = v
    return out


def _as_dt(value) -> datetime | None:
    if value is None or isinstance(value, datetime):
        return value
    try:
        from datetime import timezone

        dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
    except ValueError:
        return None


def row_to_relic(row: dict[str, Any]) -> Relic:
    """DB row -> Relic. Unknown/missing state degrades to DRAFT rather than
    raising: a malformed row must never crash a launch check (it simply won't be
    launchable)."""
    try:
        state = RelicState(str(row.get("state") or "draft"))
    except ValueError:
        state = RelicState.DRAFT
    return Relic(
        id=str(row["id"]),
        commitment=row.get("commitment"),
        state=state,
        chain=row.get("chain"),
        contract=row.get("contract"),
        token_id=(str(row["token_id"]) if row.get("token_id") is not None else None),
        mint_wallet_ref=row.get("mint_wallet_ref"),
        image_uri=row.get("image_uri"),
        minted_at=_as_dt(row.get("minted_at")),
        created_at=_as_dt(row.get("created_at")) or datetime.now(),
    )


class SupabaseRelicRepo:
    """The RelicRepo port over the `relics` table."""

    def __init__(self, client):
        self._db = client

    def add_relic(self, *, relic: Relic, identity_ciphertext: str) -> None:
        fields = {
            "id": relic.id,
            "state": relic.state,
            "commitment": relic.commitment,
            "identity_ciphertext": identity_ciphertext,
            "chain": relic.chain,
            "contract": relic.contract,
            "token_id": relic.token_id,
            "mint_wallet_ref": relic.mint_wallet_ref,
            "image_uri": relic.image_uri,
            "minted_at": relic.minted_at,
        }
        # Let the DB generate the id when the caller didn't pick one.
        if not fields["id"]:
            fields.pop("id")
        self._db.table("relics").insert(_clean(fields)).execute()

    def get_relic(self, relic_id: str) -> Relic | None:
        resp = self._db.table("relics").select("*").eq("id", str(relic_id)).execute()
        rows = resp.data or []
        return row_to_relic(rows[0]) if rows else None

    def get_identity_ciphertext(self, relic_id: str) -> str | None:
        resp = (
            self._db.table("relics").select("identity_ciphertext")
            .eq("id", str(relic_id)).execute()
        )
        rows = resp.data or []
        return rows[0].get("identity_ciphertext") if rows else None

    def set_relic(self, relic: Relic) -> None:
        """Upsert state + on-chain fields. The ciphertext is NEVER rewritten here
        — the identity is written once, at creation (R4-style immutability: a
        relic's identity must not drift after it exists)."""
        self._db.table("relics").update(_clean({
            "state": relic.state,
            "commitment": relic.commitment,
            "chain": relic.chain,
            "contract": relic.contract,
            "token_id": relic.token_id,
            "mint_wallet_ref": relic.mint_wallet_ref,
            "image_uri": relic.image_uri,
            "minted_at": relic.minted_at,
        })).eq("id", str(relic.id)).execute()

    def minted_relics(self) -> list[Relic]:
        """The pool: state='minted', OLDEST MINT FIRST (most aged = best
        anonymity — the selection rule)."""
        resp = (
            self._db.table("relics").select("*").eq("state", "minted")
            .order("minted_at").execute()
        )
        return [row_to_relic(r) for r in (resp.data or [])]

    def all_relics(self) -> list[Relic]:
        """Every relic, any state, oldest first — feeds the anti-repetition list
        (the bot must never invent the same character twice)."""
        resp = self._db.table("relics").select("*").order("created_at").execute()
        return [row_to_relic(r) for r in (resp.data or [])]

    # --- helpers used by the wallet directory / ops ---
    def used_wallet_refs(self) -> set[str]:
        """Every wallet ref already consumed by a relic (any state) — a wallet is
        used ONCE, forever, so two relics are never linkable through a shared
        minter."""
        resp = self._db.table("relics").select("mint_wallet_ref").execute()
        return {
            str(r["mint_wallet_ref"]) for r in (resp.data or [])
            if r.get("mint_wallet_ref")
        }

    def count_by_state(self, state: str) -> int:
        resp = self._db.table("relics").select("id").eq("state", str(state)).execute()
        return len(resp.data or [])


class ConfigWalletDirectory:
    """WalletDirectory over a configured list of refs + the repo's used set.

    `refs` comes from config (e.g. RELIC_WALLET_REFS="RW01,RW02,..."); the KEYS
    themselves live in Doppler and are resolved only at signing time. Keeping the
    ref LIST in config (not the DB) means the DB never even hints at how many
    wallets exist."""

    def __init__(self, refs, repo: SupabaseRelicRepo):
        self._refs = [str(r).strip() for r in refs if str(r).strip()]
        self._repo = repo

    def all_refs(self) -> list[str]:
        return list(self._refs)

    def used_refs(self) -> set[str]:
        return self._repo.used_wallet_refs()


class DopplerKeyResolver:
    """ref -> (address, private_key) from environment (Doppler injects it).

    Convention: `{REF}_ADDR` and `{REF}_PK`. The key is returned for immediate
    use by the signer and is never stored, logged or notified."""

    def __init__(self, env=None):
        import os

        self._env = env if env is not None else os.environ

    def resolve(self, wallet_ref: str) -> tuple[str, str]:
        ref = str(wallet_ref).strip().upper()
        addr = self._env.get(f"{ref}_ADDR", "")
        key = self._env.get(f"{ref}_PK", "")
        if not addr or not key:
            raise RuntimeError(
                f"wallet {ref} not configured — set {ref}_ADDR and {ref}_PK in "
                "Doppler (never in code or the DB)"
            )
        return addr, key
