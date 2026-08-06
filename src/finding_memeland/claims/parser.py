"""Claim-post parsing — classify a public reply before the pipeline acts on it.

Two deliberately different bars:

- MATCHING is generous: any token of the expected length counts as a candidate
  (case-insensitive), so a player who types the code in lowercase still wins.
  Reuses the DM parser's extraction rules (dm.validator) — one set of truths.

- CODE-LIKE (the trigger for taunts / guess caps / wrong-door redirects) is
  strict: the token must LOOK like one of our codes (uppercase A-Z/digits, the
  claim-code alphabet). Without this, any 8-letter English word ("birthday",
  "personal") in a random mention would trigger replies — spam by our own hand.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime

from ..dm.validator import WALLET_RE, _normalize_wallet


@dataclass
class ClaimPost:
    """One post from the mentions stream / conversation sweep."""

    tweet_id: str
    author_id: str
    author_handle: str
    text: str
    created_at: datetime
    conversation_id: str | None = None
    replied_to_id: str | None = None   # the tweet this post replies to (if any)

    def sort_key(self) -> tuple:
        """Arrival order: created_at first, snowflake id as ms tiebreak."""
        tid = int(self.tweet_id) if str(self.tweet_id).isdigit() else 0
        return (self.created_at, tid)


_TOKEN_RE = re.compile(r"\b[A-Za-z0-9]+\b")
# Strict "looks like one of OUR codes": claim-code alphabet only (uppercase +
# digits — see content.integrity._SAFE_ALPHABET), checked on the ORIGINAL
# casing so ordinary words never qualify.
_CODE_LIKE_RE = re.compile(r"^[A-Z0-9]+$")


def extract_candidates(text: str, expected_code_len: int) -> tuple[str, ...]:
    """All tokens of the expected length (uppercased, deduped, in order) —
    the generous matching set, mirroring dm.validator.parse_dm."""
    out: list[str] = []
    for tok in _TOKEN_RE.findall(text or ""):
        if tok.lower().startswith("0x"):
            continue
        if len(tok) == expected_code_len:
            up = tok.upper()
            if up not in out:
                out.append(up)
    return tuple(out)


def code_like(text: str, expected_code_len: int) -> bool:
    """Strict trigger: does the post contain a token that LOOKS like a claim
    code (right length, claim-code alphabet, as typed)?"""
    for tok in _TOKEN_RE.findall(text or ""):
        if len(tok) == expected_code_len and _CODE_LIKE_RE.match(tok):
            return True
    return False


_GUESS_LIKE_RE = re.compile(r"^[A-Z0-9]{5,14}$")


def guess_like(text: str, expected_code_len: int) -> bool:
    """A token that LOOKS like a code attempt with the wrong shape: claim-code
    alphabet as typed, 5-14 chars, and BOTH a letter and a digit — 'TSU19'
    yes; 'WAGMI' and token amounts like '10000000' no. Exact-length tokens are
    code_like's business; this catches the drive-by guesses that got silence
    in Hunt #4 (post-mortem: the thread needs the oracle to bite back)."""
    for tok in _TOKEN_RE.findall(text or ""):
        if len(tok) == expected_code_len:
            continue
        if (
            _GUESS_LIKE_RE.match(tok)
            and any(c.isdigit() for c in tok)
            and any(c.isalpha() for c in tok)
        ):
            return True
    return False


def extract_wallet(text: str) -> str | None:
    """First EIP-55-valid wallet in the text (same rules as the DM parser:
    a bad mixed-case checksum parses as NO wallet, so the winner is asked to
    correct it instead of the prize burning in a typo'd address)."""
    m = WALLET_RE.search(text or "")
    return _normalize_wallet(m.group(0)) if m else None
