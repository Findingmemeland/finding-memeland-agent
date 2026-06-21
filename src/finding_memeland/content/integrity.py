"""Hunt Integrity Protocol — the cryptographic commitment published in Clue 1.

    integrity_hash = SHA-256(persona_user_id + claim_code + secret_salt)

The hash goes out in Clue 1 before any other clue exists, committing the agent
to its hidden target. At reveal, the Winner Announcement publishes user_id,
claim_code and salt so anyone can recompute and verify.

This logic is FROZEN by design (litepaper v0.3 §2). Do not change the
concatenation order or encoding without versioning the protocol publicly —
old hashes must stay verifiable forever.
"""

from __future__ import annotations

import hashlib
import secrets
import string

_CLAIM_CODE_ALPHABET = string.ascii_uppercase + string.digits
# Exclude visually ambiguous chars so players read the code off the profile cleanly.
_AMBIGUOUS = {"O", "0", "I", "1"}
_SAFE_ALPHABET = "".join(c for c in _CLAIM_CODE_ALPHABET if c not in _AMBIGUOUS)


def generate_claim_code(length: int = 8) -> str:
    """A short, human-readable code displayed on the persona's profile."""
    return "".join(secrets.choice(_SAFE_ALPHABET) for _ in range(length))


def generate_salt() -> str:
    """A fresh high-entropy salt per hunt; revealed in the Winner Announcement."""
    return secrets.token_hex(16)


def compute_integrity_hash(persona_user_id: str, claim_code: str, salt: str) -> str:
    """The committed value. Order and utf-8 encoding are part of the protocol."""
    payload = f"{persona_user_id}{claim_code}{salt}".encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def verify_integrity_hash(
    persona_user_id: str, claim_code: str, salt: str, published_hash: str
) -> bool:
    """Recompute and compare. Used in tests and by the public after reveal."""
    return secrets.compare_digest(
        compute_integrity_hash(persona_user_id, claim_code, salt),
        published_hash.strip().lower(),
    )
