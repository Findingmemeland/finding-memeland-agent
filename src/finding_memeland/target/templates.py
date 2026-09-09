"""Target-hunt post templates (Option A) — the public face of the v2 protocol.

Sits beside content/templates.py (frozen for persona/relic hunts) instead of
editing it: those templates are the record of hunts already played, and the
persona/relic reveal wording is verified against v1 hashes forever. A target
hunt says different things in the same voice:

  · the target is an EXISTING NFT that belongs to somebody else — "the prize
    is always on Base, the treasure can be anywhere onchain" (brand line,
    Pedro 05/09). We never touch it; the winner gets $FIND, the token stays
    with its owner.
  · the claim is the token's IDENTITY — chain:contract:tokenId, or a
    marketplace link — never a code
  · the commitment is v2: SHA-256(target_id + metadata_sha256 + salt),
    labelled "commitment v2" in Clue 1 so a verifier knows which formula
  · a VOID is as verifiable as a win (commitment.py): the void post
    publishes every ingredient, including the live hash that failed
  · a mutation/burn AFTER a valid claim is NOTED, never used against the
    winner (hunt.ACT_PAY_NOTED)

Same house rules as the frozen templates: one cashtag per post (X 403),
never a URL in a clue ($0.20/post), the tx link is the one exception, no
engagement-bait phrases, "declared fiction" vocabulary never "fake". And
the X link-render trap measured 27/08: the joined chain:contract:tokenId
renders as a cashtag link with a "$" glued on — so the reveal prints the
three parts SPLIT and tells people to assemble them.
"""

from __future__ import annotations

from dataclasses import dataclass

from ..content.templates import _ordinal

# --------------------------------------------------------------------------- #
# Clue 1 — cold-traffic explainer for the target game                          #
# --------------------------------------------------------------------------- #

TARGET_CLUE_ONE_EXPLAINER = (
    "every hunt i pick an NFT that already exists, made by someone else, "
    "hidden in plain sight. \U0001F438\n"
    "the prize is always on Base. the treasure can be anywhere onchain.\n"
    "decode the clues, work out its name, find the exact token — reply to "
    "this post with its chain:contract:tokenId (or the marketplace link). "
    "first one wins the prize. the NFT stays with its owner."
)

CLAIM_FORMAT_EXAMPLE = "ethereum:0xabc…def:42"


def target_clue_one(hunt_n: int, clue_text: str, prize: str, commitment: str,
                    non_holder_pct: int | None = 10) -> str:
    """Opening post: announcement + explainer + clue 1 + reshare gate +
    commitment v2. The footer 'Check pinned for rules' appears ONLY here.
    non_holder_pct=None: holding floor OFF, the split line is omitted."""
    return (
        f"Hunt #{hunt_n} is live.\n\n"
        f"{TARGET_CLUE_ONE_EXPLAINER}\n\n"
        f"1st clue:\n\n"
        f"{clue_text}\n\n"
        f"The first to find it wins {prize} $FIND.\n"
        + (
            f"hold FIND to win the full prize — non-holders win {non_holder_pct}%.\n"
            if non_holder_pct is not None else ""
        )
        + "Quote or repost this post to enter.\n\n"
        f"commitment v2: {commitment}\n\n"
        f"Check pinned for rules."
    )


# Clues 2+: the line late joiners need — where and in what shape to claim.
TARGET_CLUE_FOLLOWUP_CLAIM_HINT = (
    "found it? reply to the Clue 1 post with chain:contract:tokenId or the "
    "marketplace link."
)


def claim_hint_due(clue_index: int) -> bool:
    """Where the claim line goes (Opus, dry-run 09/09): clues 2 and 3, then
    every fifth. Twenty near-identical closers in one thread read as spam
    and X down-ranks near-duplicate content — the opposite of what a long
    hunt needs. Late joiners still meet the line within a few clues."""
    return clue_index in (2, 3) or (clue_index >= 5 and clue_index % 5 == 0)


def target_clue_followup(clue_index: int, clue_text: str, taunt: str) -> str:
    body = f"{_ordinal(clue_index)} Clue:\n\n{clue_text}\n\n{taunt}"
    if claim_hint_due(clue_index):
        body += f"\n\n{TARGET_CLUE_FOLLOWUP_CLAIM_HINT}"
    return body


# --------------------------------------------------------------------------- #
# Reveal blocks — the verification text, shared by win and void               #
# --------------------------------------------------------------------------- #


def _split_id(target_id: str) -> tuple[str, str, str]:
    parts = (target_id or "").split(":")
    if len(parts) == 3 and all(parts):
        return parts[0], parts[1], parts[2]
    return "", "", ""


def commitment_block(*, target_id: str, metadata_sha256: str, salt: str,
                     token_uri: str = "") -> str:
    """How anyone recomputes the v2 commitment. The id is printed SPLIT
    (X link-render trap); the metadata hash is explained so the second
    check — the mutation check — can be run against the live token. The
    tokenURI sealed at launch is printed when known (v3): it is what the
    live check compared by content id."""
    chain, contract, token = _split_id(target_id)
    id_lines = (
        f"  chain: {chain}\n  contract: {contract}\n  tokenId: {token}\n"
        if contract else f"  target_id: {target_id}\n"
    )
    if token_uri:
        id_lines += f"  tokenURI at launch: {token_uri}\n"
    return (
        "Commitment check (v2) — recompute SHA-256 of "
        "target_id + metadata_sha256 + salt, one string, utf-8.\n"
        "target_id is chain:contract:tokenId joined with ':' (lowercase "
        "contract) — assemble it yourself; posted split because X links the "
        "joined form:\n"
        f"{id_lines}"
        f"  metadata_sha256: {metadata_sha256}\n"
        f"  salt: {salt}\n"
        "It matches the commitment in Clue 1.\n"
        "metadata_sha256 is SHA-256 of the token's metadata JSON (keys "
        "sorted, no ASCII escaping, utf-8) — fetch its tokenURI and check it "
        "yourself."
    )


# --------------------------------------------------------------------------- #
# Winner announcement                                                          #
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class TargetWinnerData:
    hunt_n: int
    winner_handle: str
    time_to_win: str
    prize_amount: str          # formatted $FIND amount
    tx_link: str
    target_name_onchain: str   # public from here on
    target_id: str
    metadata_sha256: str
    salt: str
    holder: bool = True
    non_holder_pct: int = 10
    # hunt.ACT_PAY_NOTED: the token changed after the claim — noted, paid.
    live_metadata_sha256: str | None = None
    live_hash_status: str = "unavailable"   # resolved | unresolvable | unavailable (R8)
    mutated_after_claim: bool = False
    token_uri: str = ""                   # sealed at launch (v3)
    live_token_uri: str | None = None     # what the chain answered later
    # The reveal SHOWS the treasure (Opus, dry-run 09/09): the artwork is
    # attached as media by the caller when its bytes are an image, and the
    # item page is the post's ONE URL (OpenSea renders the art card —
    # measured Hunt #9; the tx hash stays plain text). Format confirmed by
    # Pedro's own post that rendered: opensea.io/item/<chain>/<contract>/<id>.
    item_link: str | None = None
    artist: str = ""                  # R9: credited when the metadata names one


def _treasure_line(name_onchain: str, artist: str) -> str:
    """R9 — 'The treasure was “X”, by Y' when the metadata names the author;
    the title alone when it does not (nothing invented)."""
    return (f"The treasure was “{name_onchain}”, by {artist}" if artist
            else f"The treasure was “{name_onchain}”")


def artwork_alt_text(name_onchain: str, artist: str, hunt_n: int) -> str:
    """Alt-text for the attached artwork (R9: accessibility + credit)."""
    who = f", by {artist}" if artist else ""
    return f"“{name_onchain}”{who} — the NFT that was Hunt #{hunt_n}'s treasure."[:1000]


def target_winner_announcement(d: TargetWinnerData) -> str:
    """Long-post (X Premium). Winner + what the treasure was + the v2
    verification block + (if it happened) the post-claim mutation note."""
    winner = d.winner_handle.lstrip("@")
    return (
        f"Hunt #{d.hunt_n} is halted. We have a winner!\n\n"
        f"Congratulations @{winner} — solved in {d.time_to_win}.\n"
        f"{d.prize_amount} $FIND transferred to your wallet ({d.tx_link}).\n"
        + (
            f"heads up: this wallet isn't holding FIND — non-holders win "
            f"{d.non_holder_pct}% of the pot. hold on to your tokens and the "
            f"full bounty is yours next time.\n"
            if not d.holder else ""
        )
        + "\n" + _treasure_line(d.target_name_onchain, d.artist)
        + (" — an NFT that was already out there." if d.artist else
           " — an NFT that was already out there, made by someone else.")
        + " It stays with its owner; we never touched it. The prize was on "
        "Base; the treasure was wherever it was.\n\n"
        + commitment_block(target_id=d.target_id,
                           metadata_sha256=d.metadata_sha256, salt=d.salt,
                           token_uri=d.token_uri)
        + (
            "\n\nnote: the token's on-chain state changed AFTER the winning "
            f"claim ({_live_hash_phrase(d.live_hash_status, d.live_metadata_sha256)}"
            + (f"; live tokenURI {d.live_token_uri}" if d.live_token_uri else "")
            + "). "
            "the winner found the right token — the commitment binds the "
            "token's identity and its metadata at launch, never who owns it "
            "or what a third party does to it later. prize paid in full."
            if d.mutated_after_claim else ""
        )
        + (f"\n\nsee it: {d.item_link}" if d.item_link else "")
        + "\n\nTo the rest of you: keep your eyes open. "
        "The next hunt can begin at any time."
    )


def item_link_for(target_id: str) -> str | None:
    """opensea.io/item/<chain>/<contract>/<tokenId> — the one URL of the
    reveal. None when the id does not split (nothing invented)."""
    chain, contract, token = _split_id(target_id)
    return f"opensea.io/item/{chain}/{contract}/{token}" if contract else None


# --------------------------------------------------------------------------- #
# Void — as verifiable as a win                                                #
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class VoidRevealData:
    hunt_n: int
    cause: str                 # "mutated" | "burned" | "unclaimed"
    target_name_onchain: str
    target_id: str
    metadata_sha256: str
    salt: str
    live_metadata_sha256: str | None = None   # only meaningful when status=resolved
    relaunching: bool = False                 # puzzle-phase: fresh hunt follows
    token_uri: str = ""                       # sealed at launch (v3)
    live_token_uri: str | None = None         # what the chain answered at the void
    live_hash_status: str = "unavailable"     # resolved | unresolvable | unavailable (R8)
    artist: str = ""                          # R9


# R8 (Opus audit, 09/09): PUBLISH ONLY WHAT WAS MEASURED. The cause lines say
# what the chain answered over RPC — a tokenURI now pointing at different
# content, an ownerOf that no longer resolves — never who did it or why. We
# never observed "the owner" changing anything, and "burned" is our reading
# of a revert; the post states the observation, and readers can check it.
_CAUSE_LINE = {
    "mutated": "the target's on-chain tokenURI now points at different "
               "content than the one sealed at launch, so the commitment can "
               "no longer be verified live",
    "burned": "the target's ownerOf no longer resolves on-chain (the token "
              "appears burned or removed), so the live check cannot be "
              "computed",
    "unclaimed": "nobody found it before the deadline",
}


def _live_hash_phrase(status: str, sha256: str | None) -> str:
    """Tri-state, never conflated (R8): resolved → the hash; unresolvable →
    the chain serves nothing to hash; unavailable → WE could not fetch it,
    and the post says so instead of blaming the token."""
    if status == "resolved" and sha256:
        return f"live metadata_sha256: {sha256}"
    if status == "unresolvable":
        return ("live metadata_sha256: none — the chain no longer serves a "
                "tokenURI for this token")
    return ("live metadata_sha256: not resolved at posting time (our gateway "
            "could not fetch it — the live tokenURI above is what the chain "
            "answered; anyone can hash it)")


def void_reveal(d: VoidRevealData) -> str:
    """The void post publishes EVERYTHING the winner post would — plus the
    live hash that failed — so anyone can verify both that the commitment
    was honest and that the void was real. Prize back to the vault; never
    'trust us'."""
    cause = _CAUSE_LINE.get(d.cause, d.cause)
    live = ""
    if d.live_token_uri:
        live += f"  live tokenURI: {d.live_token_uri}\n"
    live += "  " + _live_hash_phrase(d.live_hash_status, d.live_metadata_sha256) + "\n"
    return (
        f"Hunt #{d.hunt_n} is void — {cause}.\n"
        "The prize goes back to the vault. Nobody loses anything they had.\n\n"
        + _treasure_line(d.target_name_onchain, d.artist)
        + ". Here is everything, so you can check us:\n\n"
        + commitment_block(target_id=d.target_id,
                           metadata_sha256=d.metadata_sha256, salt=d.salt,
                           token_uri=d.token_uri)
        + ("\n" + live if d.cause != "unclaimed" else "\n")
        + (
            "\na new hunt, with a new treasure, starts shortly. same prize."
            if d.relaunching else
            "\nThe next hunt can begin at any time."
        )
    )


# --------------------------------------------------------------------------- #
# Claim-by-post system replies specific to the target game                     #
# --------------------------------------------------------------------------- #

# A paste with contract:tokenId but no chain — the public format rule. The
# chain is part of the answer (clues never state it), so this can't match.
POST_REPLY_FORMAT = (
    "almost — a claim needs the chain too. reply with chain:contract:tokenId "
    f"(like {CLAIM_FORMAT_EXAMPLE}) or the marketplace link, and it counts."
)

# A marketplace link we could not resolve to a token (slug-only page, or the
# resolver is down): ask for the identity instead of guessing.
POST_REPLY_UNRESOLVED_LINK = (
    "can't read that link as a token. reply with chain:contract:tokenId "
    f"(like {CLAIM_FORMAT_EXAMPLE}) and it counts."
)

# More than one token in a reply (P0, 06/09): malformed — no guess spent,
# never a match. Public rule: name one token per reply.
POST_REPLY_ONE_TOKEN = (
    "one token per reply. name exactly one chain:contract:tokenId (or one "
    "link) and it counts — this one didn't."
)

POST_REPLY_WRONG_DOOR_TARGET = (
    "the claim goes in the replies of the Clue 1 post — drop the "
    "chain:contract:tokenId (or link) there and it counts. \U0001F438"
)

# The consequence of the spray detector, worded for the pinned rules —
# the detector's numbers are reserved (hunt.PUBLIC_SPRAY_RULE is the source).
