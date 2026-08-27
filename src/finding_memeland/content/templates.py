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
    # Holder reward split (2026-07-31): non-holder winners get pct% of the pot.
    holder: bool = True
    non_holder_pct: int = 10
    # Hunt relic: o nome da relíquia (público a partir daqui) e o link on-chain.
    # Com isto o reveal deixa de falar de "persona" e de "profile", que numa
    # hunt relic é linguagem do jogo antigo publicada no post mais lido de todos
    # (auditoria v3).
    relic_name: str | None = None
    relic_link: str | None = None

    @property
    def user_id_chain(self) -> str:
        return self._user_id_parts[0]

    @property
    def user_id_contract(self) -> str:
        return self._user_id_parts[1]

    @property
    def user_id_token(self) -> str:
        return self._user_id_parts[2]

    @property
    def _user_id_parts(self) -> tuple[str, str, str]:
        """chain, contract, token of a relic user_id ('base:0x…:1'), or empty
        strings when the id doesn't have that exact shape — the reveal then
        falls back to printing it whole."""
        parts = (self.persona_user_id or "").split(":")
        if len(parts) == 3 and all(parts):
            return (parts[0], parts[1], parts[2])
        return ("", "", "")


# --------------------------------------------------------------------------
# Cold-traffic explainer (post-mortem P1b): the opening post assumed the reader
# already knew the game — 80 views, 0 organic reshares, a ~5-step funnel just
# to understand what was being asked. These are the first two lines a stranger
# reads; they must explain the game before anything else.
#
# Original text set by Pedro (2026-07-20); claim channel updated 2026-07-25:
# the DM API only reads virgin conversations, so submissions moved to PUBLIC
# replies on this very post ("reply to this post with the code" — Pedro's
# claim-by-post ruleset). Wording rules that still shape it: declared fiction,
# never "fake person/account" (platform-manipulation vocabulary); ask for the
# CODE, never the wallet (that lives in the pinned rules); no engagement-bait
# phrases. preflight_check still refuses to launch if this ever reverts to the
# placeholder marker.
# --------------------------------------------------------------------------
_EXPLAINER_PLACEHOLDER_MARK = "<<EXPLAINER-PENDING>>"
CLUE_ONE_EXPLAINER = (
    "every hunt i invent someone who doesn't exist, "
    "and hide their account somewhere on X. \U0001F438\n"
    "decode the clues, find it, reply to this post with the code in its bio — "
    "first one wins the prize."
)


# O explainer acima descreve o jogo ANTIGO: esconder uma conta no X e ler o
# código da bio. Numa hunt relic isso é instrução ERRADA publicada na Clue 1 —
# manda toda a gente procurar contas que não existem (auditoria v3, P0-A).
RELIC_CLUE_ONE_EXPLAINER = (
    "every hunt i invent someone who doesn't exist, "
    "and hide them onchain on Base. \U0001F438\n"
    "decode the clues, work out the two-word name, find it on a marketplace — "
    "reply to this post with the code in its description. first one wins the "
    "prize AND keeps the relic."
)


def explainer_pending() -> bool:
    """True while the cold-traffic explainer is still the placeholder.
    Checked by preflight_check so a hunt can't launch with placeholder text."""
    return _EXPLAINER_PLACEHOLDER_MARK in CLUE_ONE_EXPLAINER


def clue_one(
    hunt_n: int, clue_text: str, prize: str, integrity_hash: str,
    non_holder_pct: int | None = 10, relic: bool = False,
) -> str:
    """Opening post: cold-traffic explainer + announcement + clue 1 + reshare
    gate + integrity hash.

    The footer 'Check pinned for rules' appears ONLY on Clue 1.
    non_holder_pct=None means the holding floor is OFF for this hunt (floor 0):
    the split line is omitted — never advertise a rule that isn't enforced.
    """
    # Order (Pedro, 2026-07-20): the announcement leads, the explainer follows
    # — regulars get the signal first, strangers get the context immediately
    # after, and "1st clue:" stays glued to the clue itself.
    explainer = RELIC_CLUE_ONE_EXPLAINER if relic else CLUE_ONE_EXPLAINER
    return (
        f"Hunt #{hunt_n} is live.\n\n"
        f"{explainer}\n\n"
        f"1st clue:\n\n"
        f"{clue_text}\n\n"
        f"The first to find me wins {prize} $FIND.\n"
        # Holder reward split (Pedro, 2026-07-31): the split is public from the
        # first post — nobody discovers the 10% rule only after winning.
        + (
            # No second cashtag (X 403, 2026-08-10: API posts are limited to
            # ONE cashtag) — the prize line above already carries $FIND.
            f"hold FIND to win the full prize — non-holders win {non_holder_pct}%.\n"
            if non_holder_pct is not None else ""
        )
        # Wording (Pedro, 2026-08-13, X algorithm weights release): a quote is
        # a 10× ranking event vs 2× for a repost — lead with it. Validation is
        # unchanged: has_reshared always accepted retweeted AND quoted.
        + f"Quote or repost this post to enter.\n\n"
        f"integrity: {integrity_hash}\n\n"
        f"Check pinned for rules."
    )


def clue_followup(
    clue_index: int, clue_text: str, taunt: str, claim_hint: str | None = None
) -> str:
    """Clues 2+: label + clue + a varying jeer. No footer, no integrity line.
    claim_hint (claim-by-post channel): one fixed line pointing players back to
    the Clue 1 thread — the single claim window."""
    body = f"{_ordinal(clue_index)} Clue:\n\n{clue_text}\n\n{taunt}"
    if claim_hint:
        body += f"\n\n{claim_hint}"
    return body


# Claim-by-post: the line clues 2+ carry so late joiners know where to claim.
CLUE_FOLLOWUP_CLAIM_HINT = "found it? reply to the Clue 1 post with the code."


def winner_announcement(d: WinnerData) -> str:
    """Long-post (X Premium). Reveals winner + integrity ingredients + teaser."""
    winner = d.winner_handle.lstrip("@")
    persona = d.persona_handle.lstrip("@")
    return (
        f"Hunt #{d.hunt_n} is halted. We have a winner!\n\n"
        f"Congratulations @{winner} — solved in {d.time_to_win}.\n"
        f"{d.prize_amount} $FIND transferred to your wallet ({d.tx_link}).\n"
        + (
            # Text set by Pedro (2026-07-31) — the reduced share is stated
            # plainly, right under the transfer it explains.
            # Same one-cashtag rule: the transfer line above already has $FIND.
            f"heads up: this wallet isn't holding FIND — non-holders win "
            f"{d.non_holder_pct}% of the pot. hold on to your tokens and the "
            f"full bounty is yours next time.\n"
            if not d.holder else ""
        )
        # Truth in the reveal (post-mortem P3.1): production never undresses the
        # persona (undress_on_retire=False) — saying "dormant in 1 hour" was
        # false, three lines above the block asking people to VERIFY our honesty.
        + (
            # Hunt relic: o troféu é o próprio NFT e já mudou de dono.
            (
                f"The relic was {d.relic_name} — it's yours now, transferred "
                f"onchain."
                + (f" {d.relic_link}" if d.relic_link else "")
                + "\nOne of a kind. There is no second one.\n\n"
            )
            if d.relic_name else
            f"The hidden persona was @{persona} — the profile stays up as a "
            f"trophy. It played once, and never again.\n\n"
        )
        + (
            # Hunt relic: o user_id é `chain:contract:token` — e o X transforma
            # essa forma montada num link cashtag COM UM "$" À FRENTE (medido
            # 27/08 na conta de teste: o texto cru não tem $, o ecrã tem).
            # Quem copiasse o texto renderizado levava o $ e o hash não batia —
            # no único bloco que pede às pessoas para verificar. Componentes
            # separados não são tocados pelo renderer (mesmo teste); o valor
            # hasheado é o MESMO, só a apresentação muda. O protocolo congelado
            # (integrity.py) fica intacto.
            (
                f"Integrity check — recompute SHA-256 of user_id + claim_code "
                f"+ salt.\n"
                f"user_id is chain:contract:tokenId joined with ':' (one "
                f"string, lowercase) — assemble it yourself; posted split "
                f"because X links the joined form:\n"
                f"  chain: {d.user_id_chain}\n"
                f"  contract: {d.user_id_contract}\n"
                f"  tokenId: {d.user_id_token}\n"
                f"  claim_code: {d.claim_code}\n"
                f"  salt: {d.salt}\n"
                f"It matches the hash in Clue 1.\n\n"
            )
            if d.relic_name and d.user_id_contract else
            f"Integrity check — recompute SHA-256 of:\n"
            f"  user_id: {d.persona_user_id}\n"
            f"  claim_code: {d.claim_code}\n"
            f"  salt: {d.salt}\n"
            f"It matches the hash in Clue 1.\n\n"
        )
        # "Turn notifications on" was one of the exact engagement-bait phrases
        # that got the operator account flagged by X (2026-07-15). It sat in the
        # ONE post that matters most — the winner reveal, carrying the tx link
        # and the integrity proof — where a deboost costs the most.
        + "To the rest of you: keep your eyes open. "
        "The next hunt can begin at any time."
    )


# --------------------------------------------------------------------------
# Claim-by-post public replies (2026-07-25). System messages — deterministic,
# no LLM. NEVER a URL in any of these ($0.20/post with URL vs $0.015 without).
# Caps live in the orchestrator: max 1 of each TYPE per profile; taunts are a
# separate engine (claims.taunts) with its own cap.
# --------------------------------------------------------------------------
def post_reply_win(minutes: int) -> str:
    """Public reply to the winning code post: congrats + the wallet ask.
    The timeout clock starts at THIS reply (Pedro: the winner only knows they
    won when we answer), and only the same account may answer."""
    return (
        "you found me. \U0001F438\n"
        f"drop your Base wallet (0x…) in a reply to THIS post — from this same "
        f"account — within {minutes} minutes, and the prize is yours."
    )


POST_REPLY_MISSING_REPOST = (
    "claim invalid — missing repost. quote or repost the Clue 1 post, then "
    "post the code again (add a word or two — X blocks identical posts)."
)
POST_REPLY_WRONG_DOOR = (
    "the code goes in the replies of the Clue 1 post — drop it there and it "
    "counts. \U0001F438"
)
POST_REPLY_INVALID_WALLET = (
    "that address doesn't check out. reply with a valid Base wallet (0x…) — "
    "same account, the clock is still ticking."
)
POST_REPLY_TIMED_OUT = (
    "submission timed out. failed to send wallet.\n"
    "miss your window → next in line."
)
POST_REPLY_LATE = (
    "right code, but someone beat you to it — you're in line. "
    "if they miss their window, you're up."
)
POST_REPLY_EARLY = (
    "easy there — the hunt hasn't started. no clue 1, no game yet."
)
POST_REPLY_NO_HOLDING = (
    "you found me, but that wallet doesn't meet the holding rule. "
    "the hunt is back on."
)

# Canned DM auto-replies (cheap, deterministic — no LLM call).
DM_REPLY_NO_ADDRESS = "send your wallet address with the claim code to win."
# Assembler replies (Hunt #2 rule: code and wallet may arrive in separate
# messages — tell the player exactly what's still missing).
DM_REPLY_NEED_WALLET = "got your code. now send the wallet address (0x…) and you're in."
DM_REPLY_NEED_CODE = "got the wallet. now send the claim code from the persona's bio."
# Prep-window gate (P2): submissions before Clue 1 are rejected, never ignored.
DM_REPLY_EARLY = "easy there — the hunt hasn't started. no clue 1, no game yet. keep the code warm."
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
