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
    prize_amount: str          # formatted $FMML amount
    tx_link: str
    persona_handle: str
    # Integrity reveal:
    persona_user_id: str
    claim_code: str
    salt: str


def clue_one(hunt_n: int, clue_text: str, prize: str, integrity_hash: str) -> str:
    """Opening post: announcement + clue 1 + reshare gate + integrity hash.

    The footer 'Check pinned for rules' appears ONLY on Clue 1.
    """
    return (
        f"Hunt #{hunt_n} is live:\n"
        f"1st clue:\n\n"
        f"{clue_text}\n\n"
        f"The first to find me wins {prize} $FMML.\n"
        f"Reshare this post to enter.\n\n"
        f"integrity: {integrity_hash}\n\n"
        f"Check pinned for rules."
    )


def clue_followup(clue_index: int, clue_text: str, taunt: str) -> str:
    """Clues 2+: label + clue + a varying jeer. No footer, no integrity line."""
    return f"{_ordinal(clue_index)} Clue:\n\n{clue_text}\n\n{taunt}"


def winner_announcement(d: WinnerData) -> str:
    """Long-post (X Premium). Reveals winner + integrity ingredients + teaser."""
    return (
        f"Hunt #{d.hunt_n} is halted. We have a winner!\n\n"
        f"Congratulations @{d.winner_handle} — solved in {d.time_to_win}.\n"
        f"{d.prize_amount} $FMML transferred to your wallet ({d.tx_link}).\n"
        f"The hidden persona was @{d.persona_handle} (dormant in 1 hour).\n\n"
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
