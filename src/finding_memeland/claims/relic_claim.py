"""Relic claim validation — a public reply must carry the relic's NAME **and** its
CODE (decision 2026-08-22 §3.3).

Why both: search-by-code is dead (measured — BaseScan/Rarible/OpenSea return
nothing for a code), so a code alone can't be farmed by searching; requiring the
name too means a claimer must actually have LANDED on the right relic. Combined
with the existing 5-attempts-per-account cap and earliest-timestamp ordering, the
"submit every candidate" attack is not viable.

The CODE is verified CRYPTOGRAPHICALLY against the commitment published in Clue 1
(recompute hash(canonical_id + code + salt) and compare) — the same frozen
protocol as the persona hunts, so the reveal stays publicly verifiable. The NAME
is verified against the decrypted identity (bot-side).

Reuses claims.parser for token extraction — one set of truths for what counts as
a code-like token.
"""

from __future__ import annotations

from dataclasses import dataclass

from ..claims.parser import code_like, extract_candidates
from ..persona.relic import verify_relic_commitment


@dataclass(frozen=True)
class RelicClaimOutcome:
    """What a reply amounts to. `won` requires BOTH parts to be right."""

    name_ok: bool
    code_ok: bool
    code_like: bool          # did it look like a claim attempt at all (for caps/taunts)
    submitted_code: str | None = None

    @property
    def won(self) -> bool:
        return self.name_ok and self.code_ok

    @property
    def partial(self) -> str | None:
        """Which half is missing — drives the oracle's reply ('right relic, wrong
        code' / 'that code isn't from this relic')."""
        if self.won or not self.code_like:
            return None
        if self.code_ok and not self.name_ok:
            return "missing_name"
        if self.name_ok and not self.code_ok:
            return "wrong_code"
        return "wrong_both"


def _normalize(s: str) -> str:
    return " ".join((s or "").lower().split())


def name_present(text: str, relic_name: str) -> bool:
    """Is the relic's name in the reply? Case/spacing-insensitive, and tolerant of
    the words being separated by punctuation — a player who types the right name
    should never lose on formatting. ALL words of the name must appear."""
    hay = _normalize(text)
    # strip punctuation to spaces so "Maroon-Ledger!" still matches
    hay = "".join(c if c.isalnum() or c.isspace() else " " for c in hay)
    hay_words = set(hay.split())
    return all(w in hay_words for w in _normalize(relic_name).split())


def verify_relic_claim(
    text: str,
    *,
    relic_name: str,
    canonical_id: str,
    salt: str,
    commitment: str,
    expected_code_len: int = 8,
) -> RelicClaimOutcome:
    """Validate one public reply.

    The code is accepted only if it reproduces the COMMITMENT — no plaintext code
    comparison, so the check is the same cryptographic statement the public can
    verify after the reveal."""
    candidates = extract_candidates(text, expected_code_len)
    looks_like = code_like(text, expected_code_len)

    winning: str | None = None
    for cand in candidates:
        if verify_relic_commitment(canonical_id, cand, salt, commitment):
            winning = cand
            break

    return RelicClaimOutcome(
        name_ok=name_present(text, relic_name),
        code_ok=winning is not None,
        code_like=looks_like,
        submitted_code=winning,
    )
