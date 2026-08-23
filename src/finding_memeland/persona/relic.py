"""Relic Core — the hidden target as an on-chain NFT ("relic"), the evolution of
the X persona (design 2026-08-22, probe green).

WHY this exists: the X persona was the game's biggest liability (7-day warming at
the mercy of X's search suppression) and its one trust hole (the operator always
knew the handle). A relic is a 1/1 NFT minted on Base by a throwaway wallet: it
indexes by name the second it is minted (probe 2026-08-21), it costs ~$0.01, and
in blind mode not even the operator can see which relic a hunt targets.

WHAT this module owns (package 1, OFFLINE — no chain, no DB, testable with fakes):
- the relic data model (identity = the SECRET content; the pool entry = state +
  on-chain coordinates that fill at mint);
- the integrity commitment, which REUSES the frozen Hunt Integrity Protocol
  (content/integrity.py) unchanged — the relic's canonical on-chain id takes the
  slot the X persona_user_id used to. Old hashes stay verifiable forever; a relic
  hash binds the SPECIFIC NFT (contract+token), not merely the code.

The claim code lives in the NFT description; the winner submits NAME + CODE and it
is checked against this commitment (search-by-code is dead — measured on BaseScan,
Rarible, OpenSea 2026-08-22 — so the code in the description is not a shortcut).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum

# Reuse the FROZEN protocol verbatim (do NOT re-implement the hash here — one
# construction, forever verifiable). generate_claim_code excludes O/0/I/1 so a
# human reads the code off the NFT cleanly; that carries over unchanged.
from ..content.integrity import (
    compute_integrity_hash,
    generate_claim_code,
    generate_salt,
    verify_integrity_hash,
)

CHAIN_BASE = "base"


class RelicState(str, Enum):
    """Lifecycle of a pool entry. Mirrors the persona states so the orchestrator
    reads the same shape (draft→minted→reserved→in_play→revealed / retired)."""

    DRAFT = "draft"          # identity generated + committed; not yet minted
    MINTED = "minted"        # on-chain, aging in the pool (real relics AND decoys)
    RESERVED = "reserved"    # picked for a hunt; pre-launch checks pending
    IN_PLAY = "in_play"      # live in a hunt
    REVEALED = "revealed"    # hunt won; identity revealed, absorbed into collection
    RETIRED = "retired"      # decoy/expired, out of rotation


# States a relic can be picked from for a hunt (aged, on-chain, not spent).
LAUNCHABLE_STATES = frozenset({RelicState.MINTED})


def relic_canonical_id(chain: str, contract: str, token_id: int | str) -> str:
    """The relic's stable, chain-anchored identity string — the ingredient that
    replaces persona_user_id in the integrity commitment.

    Lower-cased contract so checksum/case never changes the hash; the tuple
    (chain, contract, token) is globally unique, so committing to it binds the
    ONE NFT that is the target — the reveal proves which relic, not just which
    code."""
    return f"{chain}:{str(contract).strip().lower()}:{token_id}"


def build_relic_commitment(canonical_id: str, claim_code: str, salt: str) -> str:
    """The value published in Clue 1. Same frozen construction as the persona
    hash: SHA-256(canonical_id + claim_code + salt). Computable only once the
    relic is minted (canonical_id needs contract+token) — which is always true
    at launch, since relics are minted weeks ahead and aged in the stream."""
    return compute_integrity_hash(canonical_id, claim_code, salt)


def verify_relic_commitment(
    canonical_id: str, claim_code: str, salt: str, published_hash: str
) -> bool:
    """Public verification after reveal — recompute and compare."""
    return verify_integrity_hash(canonical_id, claim_code, salt, published_hash)


@dataclass
class RelicIdentity:
    """The generated identity of one relic — the SECRET content.

    In blind mode this whole object is encrypted at rest (see relic_pool.py); only
    the commitment hash is ever public. NOTHING here may be written to a readable
    log, a plaintext DB column, or a Telegram notification (honesty bar, decision
    2026-08-22: we announce "generated blind, committed on-chain", never
    "cryptographically impossible")."""

    name: str                  # EXACTLY two words, distinctive, non-googlable
    description: str           # meme lore shown in the NFT description
    image_prompt: str          # fed to the image generator at mint
    claim_code: str            # 8-char; lives in the NFT description
    salt: str                  # per-relic; revealed at reveal
    solution_terms: list[str]  # literal answer words a clue must NEVER contain

    def commitment_for(self, canonical_id: str) -> str:
        """The commitment binding THIS identity to a specific minted NFT."""
        return build_relic_commitment(canonical_id, self.claim_code, self.salt)


@dataclass
class Relic:
    """A pool entry: public/state fields in the clear, the identity held
    separately (encrypted). On-chain coordinates are None until package 2 mints
    it; the commitment is set at mint (needs the canonical id).

    Ladder exemption is NOT stored on the relic: it is a property of the HUNT
    (declared at the surprise /launch, `hunts.ladder_exempt`), honored by the
    jackpot streak via ladder_exempt_filter(). Keeping it off the relic avoids a
    second, unused source of truth (Fable review, obj. C, 2026-08-22)."""

    id: str
    commitment: str | None = None          # public once minted
    state: RelicState = RelicState.DRAFT

    # On-chain (filled at mint — package 2). mint_wallet_ref is a Doppler KEY
    # REFERENCE (e.g. "RELIC_WALLET_0007"), NEVER a private key.
    chain: str | None = None
    contract: str | None = None
    token_id: str | None = None
    mint_wallet_ref: str | None = None
    image_uri: str | None = None           # pinned/immutable (IPFS or on-chain)
    minted_at: datetime | None = None

    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    def canonical_id(self) -> str | None:
        """The commitment ingredient, or None before mint."""
        if not (self.chain and self.contract and self.token_id is not None):
            return None
        return relic_canonical_id(self.chain, self.contract, self.token_id)

    def is_launchable(self) -> bool:
        """Aged, on-chain, committed, not yet spent. (Findability is a live
        pre-launch check in package 2 — never a stored flag.)"""
        return (
            self.state in LAUNCHABLE_STATES
            and self.commitment is not None
            and self.canonical_id() is not None
        )


def ladder_exempt_filter(pairs_with_exempt):
    """Drop surprise/exempt hunts before the jackpot streak is computed.

    Pure helper for the jackpot package to call: it receives (paid, pot, exempt)
    triples (newest first) and yields the (paid, pot) pairs of NON-exempt hunts,
    so an exempt surprise win neither raises nor resets the ladder. Kept here so
    the relic feature owns its own coupling to the ladder; the jackpot package
    imports this rather than growing relic knowledge."""
    for row in pairs_with_exempt or []:
        paid, pot, exempt = row
        if exempt:
            continue
        yield (float(paid), float(pot))


def new_identity(
    *,
    name: str,
    description: str,
    image_prompt: str,
    solution_terms: list[str],
    code_length: int = 8,
) -> RelicIdentity:
    """Assemble a fresh identity with a new code + salt. Validation of name/lore
    lives in relic_generator.py; this is the pure constructor used once the
    generated fields have passed their checks."""
    return RelicIdentity(
        name=name,
        description=description,
        image_prompt=image_prompt,
        claim_code=generate_claim_code(code_length),
        salt=generate_salt(),
        solution_terms=list(solution_terms),
    )
