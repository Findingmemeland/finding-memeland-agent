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
_LONE_CAPS_RE = re.compile(r"^[A-Z]{5,14}$")
# Common crypto-Twitter shouts that a lone-caps reply is NOT guessing with.
_CAPS_SHOUT_STOPLIST = frozenset({"WAGMI", "BULLISH", "BEARISH", "LETSGO"})
# Hostility/accusation guard (Opus, oracle review 13/08): a lone-caps
# accusation must NEVER take the direct-jeer path — jeering at "SCAMMER"
# invites the heavy algorithm negatives (report −468 likes, mute −118,
# block −62). These fall through to the humor judge, whose NO for genuine
# hostility is the shield. (5-14 letters only — shorter words like SCAM
# never match the lone-caps rule in the first place.)
_CAPS_HOSTILE_STOPLIST = frozenset({
    "SCAMMER", "SCAMMERS", "RUGPULL", "RUGGED", "FRAUD", "FRAUDS",
    "PONZI", "HONEYPOT", "GRIFT", "GRIFTER", "GRIFTERS", "REPORT",
    "REPORTED", "REPORTING", "BLOCKED", "BLOCKING", "THIEF", "THIEVES",
    "LIARS", "TRASH", "GARBAGE", "PATHETIC", "DISGUSTING", "CRIMINAL",
    "CRIMINALS", "STOLEN", "STEALING",
})


def guess_like(text: str, expected_code_len: int) -> bool:
    """A reply that LOOKS like a guess with the wrong shape — the oracle jeers
    without the humor judge. Two patterns:

    - a token in the claim-code alphabet as typed, 5-14 chars, with BOTH a
      letter and a digit — 'TSU19' yes; 'i hold 10000000' no (Hunt #4
      post-mortem: drive-by guesses got silence).
    - a LONE all-caps word, 5-14 letters ('MEWTWO') — Hunt #5 post-mortem:
      shouted name guesses have no digit, weren't caught here, and the strict
      judge stayed silent. Lone only: 'MEWTWO' jeers directly; 'MEWTWO is the
      answer' goes to the (recalibrated) judge. Common CT shouts ('WAGMI')
      are stoplisted.

    Exact-length tokens are code_like's business."""
    tokens = _TOKEN_RE.findall(text or "")
    for tok in tokens:
        if len(tok) == expected_code_len:
            continue
        if (
            _GUESS_LIKE_RE.match(tok)
            and any(c.isdigit() for c in tok)
            and any(c.isalpha() for c in tok)
        ):
            return True
    if len(tokens) == 1:
        tok = tokens[0]
        if (
            len(tok) != expected_code_len
            and _LONE_CAPS_RE.match(tok)
            and tok not in _CAPS_SHOUT_STOPLIST
            and tok not in _CAPS_HOSTILE_STOPLIST
        ):
            return True
    return False


_CONTRACT_PASTE_RE = re.compile(r"\b0x[a-fA-F0-9]{8,}\b")


def contract_paste_like(text: str) -> bool:
    """A pasted hex address/contract offered as an 'answer' (Hunt #5
    post-mortem: the token contract got pasted in the claim thread and the
    oracle stayed silent). These are mechanical engagement with the game —
    they jeer directly, no judge needed."""
    return bool(_CONTRACT_PASTE_RE.search(text or ""))


def extract_wallet(text: str) -> str | None:
    """First EIP-55-valid wallet in the text (same rules as the DM parser:
    a bad mixed-case checksum parses as NO wallet, so the winner is asked to
    correct it instead of the prize burning in a typo'd address)."""
    m = WALLET_RE.search(text or "")
    return _normalize_wallet(m.group(0)) if m else None
