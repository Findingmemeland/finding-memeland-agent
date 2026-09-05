"""Integrity commitment v2 — the Option A successor to content/integrity.py.

v1 (FROZEN, litepaper v0.3 §2, kept verifiable forever):
    integrity_hash = SHA-256(relic_id + claim_code + secret_salt)

v2 (Option A: the target is an existing third-party NFT, no claim code):
    integrity_hash = SHA-256(target_id + metadata_sha256 + secret_salt)
    target_id       = chain:contract:tokenId       (Target.id())
    metadata_sha256 = hash of the token's FULL metadata at selection time
                      (selector.metadata_hash: canonical JSON, sort_keys,
                      ensure_ascii=False, utf-8)

Published in Clue 1 before any other clue exists. The Winner Announcement
reveals target_id, metadata_sha256 and the salt; anyone recomputes the
commitment AND recomputes metadata_sha256 from the token's live metadata.
If the owner mutated the metadata mid-hunt the second check fails publicly
— that hunt is VOIDED by rule, prize back to the vault.

⚠️ Why the metadata hash lives INSIDE the salted commitment and is NOT
published raw in Clue 1 (this corrects the 04/09 sketch, flagged to Opus):
a raw metadata hash is an ENUMERATION ORACLE. Anyone can hash candidate
metadata — and "the pool is probably art platforms" is a guessable
strategy, so an attacker enumerates exactly the platforms we curate,
hashes every item's metadata via the same public APIs, and matches the
published hash with ZERO clues solved. That is the Hunt #10 harvest
reborn one layer up. Salting it closes the oracle: nothing published in
Clue 1 can be tested against any candidate without the salt, and the salt
appears only at reveal, when the game is over.

Two Opus implementation notes (04/09), both part of the protocol:

VOIDING REVEALS. During the hunt the metadata hash is hidden inside the
commitment, so "voided: the metadata changed" would be an unverifiable
claim — a trust request, which this project does not issue. The void
announcement therefore publishes EVERYTHING the winner announcement would:
target_id, the committed metadata_sha256, the salt, and the live
metadata's hash, so anyone can verify both that the commitment was honest
and that the mutation really happened. A void is as verifiable as a win.

THE VOID RULE COVERS BURN, not just mutation (Opus, 05/09). A burned
token makes tokenURI/ownerOf revert, so the live hash is UNCOMPUTABLE —
same verification failure, same public void path, prize back to the
vault; the announcement reveals the ingredients and anyone can confirm
on-chain that the token no longer resolves. Burn is the one scenario
where the clues point at nothing, and it is the published rule — not a
selection filter — that closes it. A mid-hunt TRANSFER, by contrast,
voids NOTHING: the identity is chain:contract:tokenId and the commitment
seals metadata, not ownership (dormancy demotion reaffirmed 05/09).

CANONICALISATION. The hash is computed over the PARSED metadata,
re-serialised canonically (json.dumps with sort_keys=True,
ensure_ascii=False, default separators, utf-8 — selector.metadata_hash),
so key order and whitespace can never fire the void rule. Genuine
volatility (HTTP tokenURIs serving dynamic JSON) is excluded
STRUCTURALLY instead of field-by-field: only targets whose tokenURI is
content-addressed (ipfs:// or data:) enter the pool — see
refresh.uri_is_content_addressed. Content-addressed metadata can only
change if the contract's URI itself changes, which is exactly the
mutation the rule exists to catch.

Same discipline as v1: concatenation order and utf-8 encoding are part of
the protocol; never change them without publicly versioning. v1 hashes
from past hunts stay verifiable with the v1 functions, untouched.
"""

from __future__ import annotations

import hashlib
import secrets

COMMITMENT_VERSION = "v2"


def generate_salt() -> str:
    """Fresh high-entropy salt per hunt; revealed in the Winner
    Announcement (same contract as v1's)."""
    return secrets.token_hex(16)


def compute_commitment_v2(target_id: str, metadata_sha256: str,
                          salt: str) -> str:
    """The committed value. Order and utf-8 encoding are part of the
    protocol. `target_id` is Target.id() ('base:0x…:5', contract
    lower-cased); `metadata_sha256` is selector.metadata_hash of the full
    metadata at selection time."""
    payload = f"{target_id}{metadata_sha256}{salt}".encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def verify_commitment_v2(target_id: str, metadata_sha256: str, salt: str,
                         published_hash: str) -> bool:
    """Recompute and compare (constant-time). What the public runs at
    reveal — together with recomputing metadata_sha256 from the token's
    live metadata, which is the mutation check."""
    return secrets.compare_digest(
        compute_commitment_v2(target_id, metadata_sha256, salt),
        published_hash.strip().lower(),
    )
