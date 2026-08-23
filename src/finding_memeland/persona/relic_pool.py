"""Relic pool — blind storage + selection (package 1, offline, testable).

Blind mode (decision 2026-08-22): a relic's identity (name/description/code/salt)
is stored ENCRYPTED; only the commitment hash is ever in the clear. The key lives
in Doppler; only the bot decrypts, and only at launch (to write clues) and at
reveal (to publish salt+code). The operator never sees plaintext — so we can
honestly say "generated blind, committed on-chain", and NEVER "cryptographically
impossible" (whoever holds the Doppler key could decrypt — overclaim is banned).

This module owns:
- PoolCipher: the encrypt/decrypt port. FernetPoolCipher is the real adapter
  (needs `cryptography` — the ONE new dependency, flagged in the LEIA-ME);
  NullPoolCipher is TEST/LOCAL ONLY and refuses to run in production.
- RelicRepo: the persistence port (real Supabase adapter + migration land in
  package 2). FakeRelicRepo is the in-memory implementation for tests/simulation.
- RelicPool: selection + blind read/write, analogous to DBPersonaSource.
"""

from __future__ import annotations

import json
from dataclasses import asdict
from datetime import datetime, timezone
from typing import Protocol, runtime_checkable

from .relic import Relic, RelicIdentity, RelicState
from .relic_generator import name_words

# --------------------------------------------------------------------------- #
# Cipher                                                                       #
# --------------------------------------------------------------------------- #


@runtime_checkable
class PoolCipher(Protocol):
    def encrypt(self, plaintext: str) -> str: ...
    def decrypt(self, token: str) -> str: ...


def _identity_to_json(identity: RelicIdentity) -> str:
    return json.dumps(asdict(identity), separators=(",", ":"), ensure_ascii=False)


def _identity_from_json(blob: str) -> RelicIdentity:
    d = json.loads(blob)
    return RelicIdentity(
        name=d["name"],
        description=d["description"],
        image_prompt=d["image_prompt"],
        claim_code=d["claim_code"],
        salt=d["salt"],
        solution_terms=list(d.get("solution_terms", [])),
    )


class NullPoolCipher:
    """Identity 'cipher' for tests and local runs ONLY — stores plaintext. It
    refuses to instantiate in production so a misconfig can never ship the pool
    in the clear."""

    def __init__(self, *, allow_production: bool = False):
        if not allow_production:
            try:
                from ..config import get_settings

                if get_settings().is_production:
                    raise RuntimeError(
                        "NullPoolCipher must never run in production — the relic "
                        "pool would be stored unencrypted. Configure a real key."
                    )
            except ImportError:
                pass

    def encrypt(self, plaintext: str) -> str:
        return plaintext

    def decrypt(self, token: str) -> str:
        return token


class FernetPoolCipher:
    """Real authenticated encryption (AES-128-CBC + HMAC) via `cryptography`'s
    Fernet. Key is a urlsafe-base64 32-byte value from Doppler (config
    `relic_pool_key`). Import is lazy so this module loads without the dep; only
    constructing the real cipher requires it."""

    def __init__(self, key: str):
        try:
            from cryptography.fernet import Fernet
        except ImportError as e:  # pragma: no cover - environment dependent
            raise RuntimeError(
                "FernetPoolCipher needs the `cryptography` package (add to "
                "requirements). Generate a key with Fernet.generate_key()."
            ) from e
        if not key:
            raise RuntimeError("relic_pool_key is empty — set it in Doppler")
        self._f = Fernet(key.encode() if isinstance(key, str) else key)

    def encrypt(self, plaintext: str) -> str:
        return self._f.encrypt(plaintext.encode("utf-8")).decode("ascii")

    def decrypt(self, token: str) -> str:
        return self._f.decrypt(token.encode("ascii")).decode("utf-8")


# --------------------------------------------------------------------------- #
# Repo port + fake                                                             #
# --------------------------------------------------------------------------- #


@runtime_checkable
class RelicRepo(Protocol):
    """Persistence for the pool. The identity blob is opaque ciphertext to the
    repo — it never sees plaintext. Real Supabase adapter + SQL migration: pkg 2."""

    def add_relic(self, *, relic: Relic, identity_ciphertext: str) -> None: ...
    def get_relic(self, relic_id: str) -> Relic | None: ...
    def get_identity_ciphertext(self, relic_id: str) -> str | None: ...
    def set_relic(self, relic: Relic) -> None: ...  # upsert state + on-chain fields
    def minted_relics(self) -> list[Relic]: ...     # state=minted, oldest minted_at first
    def all_relics(self) -> list[Relic]: ...        # every relic, any state (anti-repetition)


class FakeRelicRepo:
    """In-memory RelicRepo for tests and the local simulation."""

    def __init__(self):
        self.relics: dict[str, Relic] = {}
        self.ciphertext: dict[str, str] = {}

    def add_relic(self, *, relic: Relic, identity_ciphertext: str) -> None:
        self.relics[relic.id] = relic
        self.ciphertext[relic.id] = identity_ciphertext

    def get_relic(self, relic_id: str) -> Relic | None:
        return self.relics.get(relic_id)

    def get_identity_ciphertext(self, relic_id: str) -> str | None:
        return self.ciphertext.get(relic_id)

    def set_relic(self, relic: Relic) -> None:
        self.relics[relic.id] = relic

    def minted_relics(self) -> list[Relic]:
        rows = [r for r in self.relics.values() if r.state == RelicState.MINTED]
        # oldest mint first = most aged in the stream = best anonymity.
        return sorted(rows, key=lambda r: (r.minted_at or r.created_at))

    def all_relics(self) -> list[Relic]:
        return sorted(self.relics.values(), key=lambda r: r.created_at)


# --------------------------------------------------------------------------- #
# Pool service                                                                 #
# --------------------------------------------------------------------------- #


class RelicPool:
    """Blind read/write over a RelicRepo. The identity is encrypted on the way in
    and decrypted only on explicit read (launch/reveal). Selection returns the
    OLDEST launchable relic — most aged in the decoy stream, hardest to enumerate.

    A live findability check (BaseScan, fail-closed) gates the launch in package
    2; it is NEVER a stored flag here."""

    def __init__(self, repo: RelicRepo, cipher: PoolCipher):
        self._repo = repo
        self._cipher = cipher

    # writes -------------------------------------------------------------- #
    def add(self, relic: Relic, identity: RelicIdentity) -> None:
        """Store a new pool entry with its identity encrypted at rest."""
        blob = self._cipher.encrypt(_identity_to_json(identity))
        self._repo.add_relic(relic=relic, identity_ciphertext=blob)

    def mark_minted(
        self, relic_id: str, *, chain: str, contract: str, token_id: str,
        mint_wallet_ref: str, image_uri: str, commitment: str,
        minted_at: datetime | None = None,
    ) -> None:
        """Record the on-chain coordinates + the commitment computed at mint.
        (Called by package 2 after the real mint; here so the model transition
        lives in one place.)"""
        relic = self._repo.get_relic(relic_id)
        if relic is None:
            raise RuntimeError(f"relic {relic_id!r} not found")
        relic.chain = chain
        relic.contract = contract
        relic.token_id = token_id
        relic.mint_wallet_ref = mint_wallet_ref
        relic.image_uri = image_uri
        relic.commitment = commitment
        relic.minted_at = minted_at or datetime.now(timezone.utc)
        relic.state = RelicState.MINTED
        self._repo.set_relic(relic)

    def set_state(self, relic_id: str, state: RelicState) -> None:
        relic = self._repo.get_relic(relic_id)
        if relic is None:
            raise RuntimeError(f"relic {relic_id!r} not found")
        relic.state = state
        self._repo.set_relic(relic)

    # reads --------------------------------------------------------------- #
    def reveal_identity(self, relic_id: str) -> RelicIdentity:
        """Decrypt a specific relic's identity (launch/reveal only)."""
        blob = self._repo.get_identity_ciphertext(relic_id)
        if blob is None:
            raise RuntimeError(f"no identity stored for relic {relic_id!r}")
        return _identity_from_json(self._cipher.decrypt(blob))

    def avoid_recent(self, limit: int = 40) -> list[str]:
        """Names/themes of relics that ALREADY EXIST, to feed the generator's
        `avoid_recent` — the bot must never invent a character twice (Pedro,
        2026-08-22).

        Costs one decrypt per relic, which is why it's capped: the identities are
        encrypted at rest, so there is no plaintext name column to read. Failures
        are skipped rather than raised — a pool that can't be fully read must
        still allow a new relic to be created (worst case: less variety, never a
        blocked pipeline).

        The returned strings NEVER reach an operator message; they go straight
        into the generator prompt."""
        out: list[str] = []
        try:
            rows = self._repo.all_relics()
        except AttributeError:      # older repo: fall back to the minted pool
            rows = self._repo.minted_relics()
        except Exception:  # noqa: BLE001
            return out
        for relic in list(rows)[-limit:]:
            try:
                ident = self.reveal_identity(relic.id)
            except Exception:  # noqa: BLE001 — unreadable row: skip, don't block
                continue
            bits = [ident.name, *(ident.solution_terms or [])]
            line = " / ".join(b for b in bits if b)
            if line:
                out.append(line)
        return out

    def spent_words(self, limit: int = 200) -> set[str]:
        """Every word ALREADY USED by a relic name, for the generator's
        `avoid_words`.

        Separate from `avoid_recent` on purpose: that one feeds the prompt with
        names AND solution terms (themes), while this one is the hard rule and
        must cover NAMES ONLY — reserving solution terms too would burn words
        like "whale" or "prophecy" that no relic actually spent.

        Why it exists (measured 2026-08-23): theme-level anti-repetition still let
        the model reuse its favourite textures — "brackish" in three samples,
        "sensei"/"hollow"/"stale"/"soggy"/"glitch" in two each. Two relics sharing
        a word also make marketplace search ambiguous.

        Same failure policy as `avoid_recent`: an unreadable row is skipped, never
        raised — less variety beats a blocked pipeline. Uncapped by default in
        practice (200) because unlike the prompt list this one costs nothing to
        carry, and skipping rows here would silently free a word for reuse.
        """
        out: set[str] = set()
        try:
            rows = self._repo.all_relics()
        except AttributeError:      # older repo: fall back to the minted pool
            rows = self._repo.minted_relics()
        except Exception:  # noqa: BLE001
            return out
        for relic in list(rows)[-limit:]:
            try:
                ident = self.reveal_identity(relic.id)
            except Exception:  # noqa: BLE001 — unreadable row: skip, don't block
                continue
            out |= name_words(ident.name)
        return out

    def peek_launchable(self) -> tuple[Relic, RelicIdentity]:
        """The oldest launchable relic + its (decrypted) identity, WITHOUT a state
        change — the caller runs the live findability check first, then reserves.
        Refuses (never silently substitutes) when the pool has none launchable."""
        candidates = [r for r in self._repo.minted_relics() if r.is_launchable()]
        if not candidates:
            raise RuntimeError(
                "no launchable relic in the pool — mint/age more (the stream is "
                "the anonymity set)."
            )
        relic = candidates[0]
        return relic, self.reveal_identity(relic.id)
