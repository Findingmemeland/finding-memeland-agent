"""Validated post templates (frozen 2026-05-23, updated for integrity hash).

Game posts (Clue 1, clues 2+, Winner Announcement) publish autonomously with no
human approval. Voice: playful, meme-native crypto Twitter; ironic; community
language. Avoid mystical/poetic tone.

Cost note: never put URLs in clues — X bills $0.20 per URL in a post. The
Winner Announcement's tx link is the one allowed exception (long-post).
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class WinnerData:
    hunt_n: int
    winner_handle: str
    time_to_win: str
    prize_amount: str          # formatted $FIND amount
    tx_link: str
    persona_handle: str
    # Integrity reveal:
    persona_user_id: str
    claim_code: str
    salt: str


# --------------------------------------------------------------------------
# Cold-traffic explainer (post-mortem P1b): the opening post assumed the reader
# already knew the game — 80 views, 0 organic reshares, a ~5-step funnel just
# to understand what was being asked. These are the first two lines a stranger
# reads; they must explain the game before anything else.
#
# ⚠️ TEXT IS PEDRO'S TO WRITE. While the placeholder marker below is present,
# preflight_check REFUSES to launch — so this can never be posted by accident.
# Replace the value of CLUE_ONE_EXPLAINER with the real two lines, e.g.:
#   "our AI invented a secret account hiding somewhere on X.\n"
#   "crack the clues, find it, DM it the code — first one wins the prize."
# --------------------------------------------------------------------------
_EXPLAINER_PLACEHOLDER_MARK = "<<EXPLAINER-PENDING>>"
CLUE_ONE_EXPLAINER = _EXPLAINER_PLACEHOLDER_MARK


def explainer_pending() -> bool:
    """True while the cold-traffic explainer is still the placeholder.
    Checked by preflight_check so a hunt can't launch with placeholder text."""
    return _EXPLAINER_PLACEHOLDER_MARK in CLUE_ONE_EXPLAINER


def clue_one(hunt_n: int, clue_text: str, prize: str, integrity_hash: str) -> str:
    """Opening post: cold-traffic explainer + announcement + clue 1 + reshare
    gate + integrity hash.

    The footer 'Check pinned for rules' appears ONLY on Clue 1.
    """
    return (
        f"{CLUE_ONE_EXPLAINER}\n\n"
        f"Hunt #{hunt_n} is live:\n"
        f"1st clue:\n\n"
        f"{clue_text}\n\n"
        f"The first to find me wins {prize} $FIND.\n"
        f"Reshare this post to enter.\n\n"
        f"integrity: {integrity_hash}\n\n"
        f"Check pinned for rules."
    )


def clue_followup(clue_index: int, clue_text: str, taunt: str) -> str:
    """Clues 2+: label + clue + a varying jeer. No footer, no integrity line."""
    return f"{_ordinal(clue_index)} Clue:\n\n{clue_text}\n\n{taunt}"


def winner_announcement(d: WinnerData) -> str:
    """Long-post (X Premium). Reveals winner + integrity ingredients + teaser."""
    winner = d.winner_handle.lstrip("@")
    persona = d.persona_handle.lstrip("@")
    return (
        f"Hunt #{d.hunt_n} is halted. We have a winner!\n\n"
        f"Congratulations @{winner} — solved in {d.time_to_win}.\n"
        f"{d.prize_amount} $FIND transferred to your wallet ({d.tx_link}).\n"
        # Truth in the reveal (post-mortem P3.1): production never undresses the
        # persona (undress_on_retire=False) — saying "dormant in 1 hour" was
        # false, three lines above the block asking people to VERIFY our honesty.
        f"The hidden persona was @{persona} — the profile stays up as a trophy. "
        f"It played once, and never again.\n\n"
        f"Integrity check — recompute SHA-256 of:\n"
        f"  user_id: {d.persona_user_id}\n"
        f"  claim_code: {d.claim_code}\n"
        f"  salt: {d.salt}\n"
        f"It matches the hash in Clue 1.\n\n"
        f"To the rest of you, keep trying. Turn notifications on. "
        f"The next hunt can begin at any time."
    )


# Canned DM auto-replies (cheap, deterministic — no LLM call).
DM_REPLY_NO_ADDRESS = "send your wallet address with the claim code to win."
DM_REPLY_BAD_CODE = "that code isn't this hunt's. find the persona, read the real one."
DM_REPLY_NO_HOLDING = "you found me, but your wallet doesn't meet the holding rule."
DM_REPLY_NO_RESHARE = "reshare this hunt's opening post, then try again if it's still open."
DM_REPLY_LATE = "someone beat you to it this time. next hunt drops soon — stay sharp."


def _ordinal(n: int) -> str:
    if 10 <= n % 100 <= 20:
        suffix = "th"
    else:
        suffix = {1: "st", 2: "nd", 3: "rd"}.get(n % 10, "th")
    return f"{n}{suffix}"
