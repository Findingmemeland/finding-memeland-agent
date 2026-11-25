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
# Clue 1 — the opening: a rotating hook, then rules that never change         #
# --------------------------------------------------------------------------- #

# THE OPENING ROTATES (Pedro, 24/11): one fixed text opened every hunt from #11
# to #41, and the reach of the opening post fell hunt after hunt. The first
# part — the line that says the hunt is on and what the game is — now comes
# from these, in the oracle's voice (first person, lower case, dry, one 🐸),
# drawn at random and never the one the previous hunt used. Everything a
# player needs to ACT stays fixed below the clue (target_clue_one).
#
# Every opening says the same four things, so a newcomer reading any of them
# knows the game: the hunt number; an NFT that already exists, made by
# someone else; the brand line (Pedro, 05/09), word for word — and "Base"
# nowhere else, so nobody reads the treasure as being on Base; and the way
# in (clues → name → the exact token). No cashtag (one per post, X 403: the
# prize line has it), no URL, no @, nothing asking for likes. Each one fits in
# 280 characters as X counts them.
#
# The id goes on the hunt row (hunts.opening_variant) so the reach can be
# compared per opening. NEVER renumber: retire an id, never reuse it.

BRAND_LINE = "the prize is always on Base. the treasure can be anywhere onchain."


@dataclass(frozen=True)
class Opening:
    id: str           # hunts.opening_variant — stable forever
    text: str         # "{n}" = the hunt number

    def render(self, hunt_n: int) -> str:
        return self.text.replace("{n}", str(hunt_n))


_FROG = "\U0001F438"
OPENINGS: tuple[Opening, ...] = (
    Opening("o01", f"hunt #{{n}}. i've hidden something again. {_FROG}\n\n"
                   "an NFT that already exists, made by someone else, sitting in "
                   f"plain sight.\n{BRAND_LINE}\n"
                   "read the clues, work out its name, find the exact token."),
    Opening("o02", f"hunt #{{n}} is open. {_FROG}\n\n"
                   "out of everything ever minted, i picked one NFT. someone else "
                   f"made it. it's still out there.\n{BRAND_LINE}\n"
                   "the clues give you the name. the name gives you the token."),
    Opening("o03", f"hunt #{{n}}. i'm thinking of an NFT. {_FROG}\n\n"
                   "it already exists. someone else made it. i never touched it.\n"
                   f"{BRAND_LINE}\n"
                   "decode the clues, name it, find the exact token."),
    Opening("o04", f"hunt #{{n}}. i found something at the bottom of the pond. {_FROG}\n\n"
                   "one NFT, already minted by someone else, hiding in plain sight.\n"
                   f"{BRAND_LINE}\n"
                   "the clues lead to its name. the name leads to the token."),
    Opening("o05", f"hunt #{{n}}. i already know who won't find it. {_FROG}\n\n"
                   "an NFT someone else made, sitting where it has always been. i "
                   f"chose it. you find it.\n{BRAND_LINE}\n"
                   "clues first, then the name, then the exact token."),
    Opening("o06", f"hunt #{{n}}. patience is cheaper than guessing. {_FROG}\n\n"
                   "there's an NFT out there, made by someone else, and i've picked it "
                   f"for you.\n{BRAND_LINE}\n"
                   "work out its name from the clues, then find the exact token."),
    Opening("o07", f"hunt #{{n}}. one NFT, one winner. {_FROG}\n\n"
                   "the piece already exists and someone else made it. i only point "
                   f"at it.\n{BRAND_LINE}\n"
                   "read the clues, find the name, then the exact token."),
    Opening("o08", f"hunt #{{n}}. you may have scrolled past it already. {_FROG}\n\n"
                   "an NFT made by someone else, minted before i wrote a word of this.\n"
                   f"{BRAND_LINE}\n"
                   "decode the clues, work out its name, find the exact token."),
    Opening("o09", f"hunt #{{n}}. i've chosen. {_FROG}\n\n"
                   "one NFT that already exists, made by someone else. no name yet, "
                   f"just the first clue.\n{BRAND_LINE}\n"
                   "the clues point to its name. the name points to the token."),
    Opening("o10", f"hunt #{{n}}. quiet start. {_FROG}\n\n"
                   "i picked an NFT that someone else made. it has no idea.\n"
                   f"{BRAND_LINE}\n"
                   "work out the name from the clues and find the exact token first."),
)


def pick_opening(previous: str | None, rng) -> Opening:
    """A random opening, never `previous` (the id the last hunt used). An
    unknown or missing `previous` leaves all of them in the draw."""
    pool = [o for o in OPENINGS if o.id != previous]
    return rng.choice(pool or list(OPENINGS))


# The fixed half: how to answer (it used to sit in the explainer, above the
# clue — now it is the first line below it), the prize, the holder line, the
# reshare gate, the commitment and the pinned rules. The same on every hunt.
TARGET_CLUE_ONE_REPLY = (
    "found it? reply to this post with its chain:contract:tokenId (or the "
    "marketplace link). the NFT stays with its owner."
)

# The published example of the format (Pedro, 14/10): it names NO chain. The
# old one ("ethereum:0xabc…def:42") could be read as "only Ethereum counts",
# and the treasure can be anywhere onchain.
CLAIM_FORMAT_EXAMPLE = "<chain>:0xabc…def:42"


def target_clue_one(hunt_n: int, clue_text: str, prize: str, commitment: str,
                    non_holder_pct: int | None = 10, *,
                    opening: Opening | None = None) -> str:
    """Opening post: the rotating opening (OPENINGS) + clue 1 + the fixed
    rules (how to reply, the prize, the holder line, the reshare gate, the
    commitment v2). The footer 'Check pinned for rules' appears ONLY here.
    non_holder_pct=None: holding floor OFF, the split line is omitted."""
    opening = opening or OPENINGS[0]
    return (
        f"{opening.render(hunt_n)}\n\n"
        f"1st clue:\n\n"
        f"{clue_text}\n\n"
        f"{TARGET_CLUE_ONE_REPLY}\n"
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


# The first clue of the reveal phase also shows the SMALL WORDS of the name —
# the function words, a lone letter, a number, an "&" — with every content
# word blanked (10/10, Pedro: "numa linha fixa"). They were never the puzzle
# and never on the never-write list; from here on the game is handing the
# name over. A fixed line, not the writer's: it is the same in every hunt.
TARGET_SMALL_WORDS_LINE = "the small words of the name are free: {skeleton}"


def target_clue_followup(clue_index: int, clue_text: str, taunt: str, *,
                         skeleton: str | None = None) -> str:
    body = f"{_ordinal(clue_index)} Clue:\n\n{clue_text}"
    if skeleton:
        body += "\n\n" + TARGET_SMALL_WORDS_LINE.format(skeleton=skeleton)
    body += f"\n\n{taunt}" if taunt else ""
    if claim_hint_due(clue_index):
        body += f"\n\n{TARGET_CLUE_FOLLOWUP_CLAIM_HINT}"
    return body


# --------------------------------------------------------------------------- #
# Reveal blocks — the verification text, shared by win and void               #
# --------------------------------------------------------------------------- #


# X's limit for a long post (Premium), the number content/guardrails.py
# already checks clues against. Counted as X counts it (twitter-text): one
# for Latin and most punctuation, two for anything else (CJK, emoji). A URL
# is counted by its length, which for the item link (longer than X's 23) is
# more than X counts — the safe side.
X_LONG_POST_MAX = 25_000
_X_LIGHT = ((0x0000, 0x10FF), (0x2000, 0x200D), (0x2010, 0x201F), (0x2032, 0x2037))


def x_weighted_len(text: str) -> int:
    return sum(1 if any(lo <= ord(ch) <= hi for lo, hi in _X_LIGHT) else 2
               for ch in text or "")


def fit_x_post(text: str, limit: int = X_LONG_POST_MAX) -> str:
    """Never over X's limit (Pedro, after hunt #34). The fields are capped
    before they get here, so a real post is never touched. If one ever must
    be cut, it is cut at a line, says so, and keeps its last two paragraphs
    — in the reveal, the item link (the card) and the closing line."""
    if x_weighted_len(text) <= limit:
        return text
    paras = text.split("\n\n")
    keep = 2 if len(paras) >= 3 else 1
    head, last = "\n\n".join(paras[:-keep]), "\n\n".join(paras[-keep:])
    marker = "\n…\n\n"
    budget = limit - x_weighted_len(marker + last)
    if not head or budget <= 0:
        return _cut(text, limit // 2)            # no paragraph to keep: half, cut
    kept = []
    used = 0
    for line in head.split("\n"):
        w = x_weighted_len(line) + 1
        if used + w > budget:
            break
        kept.append(line)
        used += w
    return "\n".join(kept).rstrip() + marker + last


def _split_id(target_id: str) -> tuple[str, str, str]:
    parts = (target_id or "").split(":")
    if len(parts) == 3 and all(parts):
        return parts[0], parts[1], parts[2]
    return "", "", ""


# NO URI GOES OUT RAW WHEN IT IS THE DATA ITSELF (after hunt #34). The #34
# reveal printed its tokenURI whole: a `data:` URI of 13 005 characters of
# base64 (a JSON whose image is another 9 142 of SVG). It is not part of the
# commitment, and anyone reads it at the source with tokenURI(tokenId) — so
# in its place goes the short line below. The same for the live tokenURI of
# a void and of a mutation note.
DATA_URI_LINE = "fully on-chain (data: URI)"
# How much of anything that is NOT the data itself a post prints, and how
# much of the name and of the credit: never reached by a real target, there
# so that the post's length has a ceiling whatever the metadata says.
MAX_URI_CHARS = 300
MAX_NAME_CHARS = 300
MAX_CREDIT_CHARS = 120


def _is_data_uri(uri: str) -> bool:
    return (uri or "").lstrip()[:5].lower() == "data:"


def _cut(text: str, limit: int) -> str:
    """At most `limit` characters, cut at a space when there is one near,
    with "…" — the elegant cut, for fields no real target fills."""
    text = text or ""
    if len(text) <= limit:
        return text
    head = text[:limit - 1]
    space = head.rfind(" ")
    if space >= limit // 2:
        head = head[:space]
    return head.rstrip() + "…"


def _safe_uri(uri: str) -> str:
    """O mesmo princípio para a nota de mutação: nada de um segundo link a
    disputar o cartão com a página do item. Um ipfs:// passa; um https vira
    a frase que manda ler on-chain; um data: vira a linha curta (#34)."""
    if uri and uri.lower().startswith(("http://", "https://")):
        return "an https URL — read it on-chain with tokenURI(tokenId)"
    if _is_data_uri(uri):
        return DATA_URI_LINE
    return _cut(uri, MAX_URI_CHARS)


def _token_uri_lines(uri: str) -> str:
    """O tokenURI impresso de forma a que o X não o transforme em link.

    MESMA armadilha, mesmo remédio que o target_id logo acima (medido
    27/08). O reveal tem direito a UM único URL clicável — a página do item
    — porque o X constrói o cartão a partir de um URL do post, e um JSON de
    metadata não tem cartão nenhum para construir. O reveal do hunt #11 saiu
    sem a arte (Pedro, 17/09: "não renderizou o NFT"), e este é o único
    outro URL que lá está.

    Um `ipfs://` é seguro — o X não liga um esquema que não conhece — e é
    impresso tal e qual.

    Um `http(s)://` não tem salvação tipográfica: partir o esquema não
    chega, porque o X também liga domínios nus (`gateway.exemplo.com/…`).
    Então não vai. O que vai no lugar dele é melhor: o tokenURI NÃO faz
    parte do commitment — este é SHA-256(target_id + metadata_sha256 +
    salt) e verifica-se sem ele. O tokenURI é um extra (v3), e qualquer
    pessoa o lê da fonte em vez de o ler de nós, chamando tokenURI(tokenId)
    no contrato que está impresso três linhas acima. Trocamos uma linha
    copiada de nós por uma leitura on-chain, e ficamos com o cartão."""
    if uri.lower().startswith(("http://", "https://")):
        return ("  tokenURI at launch: an https URL, left out so this post "
                "carries exactly one link (the item page) — read it at the "
                "source instead: call tokenURI(tokenId) on the contract "
                "above. It is not part of the commitment.\n")
    if _is_data_uri(uri):
        # the #34 dump (Pedro): the data itself, never printed
        return (f"  tokenURI at launch: {DATA_URI_LINE} — call tokenURI(tokenId) "
                "on the contract above to read it.\n")
    return f"  tokenURI at launch: {_cut(uri, MAX_URI_CHARS)}\n"


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
        id_lines += _token_uri_lines(token_uri)
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
    name = _cut(name_onchain, MAX_NAME_CHARS)
    return (f"The treasure was “{name}”, by {_cut(artist, MAX_CREDIT_CHARS)}" if artist
            else f"The treasure was “{name}”")


def artwork_alt_text(name_onchain: str, artist: str, hunt_n: int) -> str:
    """Alt-text for the attached artwork (R9: accessibility + credit)."""
    who = f", by {artist}" if artist else ""
    return f"“{name_onchain}”{who} — the NFT that was Hunt #{hunt_n}'s treasure."[:1000]


def target_winner_announcement(d: TargetWinnerData) -> str:
    """Long-post (X Premium). Winner + what the treasure was + the v2
    verification block + (if it happened) the post-claim mutation note.
    Never over X's limit (`fit_x_post`)."""
    winner = d.winner_handle.lstrip("@")
    return fit_x_post(
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
            + (f"; live tokenURI {_safe_uri(d.live_token_uri)}"
               if d.live_token_uri else "")
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
        # as it was, an https one included — but never the data itself (#34)
        uri = (f"{DATA_URI_LINE} — call tokenURI(tokenId) on the contract above"
               if _is_data_uri(d.live_token_uri) else _cut(d.live_token_uri, MAX_URI_CHARS))
        live += f"  live tokenURI: {uri}\n"
    live += "  " + _live_hash_phrase(d.live_hash_status, d.live_metadata_sha256) + "\n"
    return fit_x_post(
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
    "almost — a claim needs the exact token: chain, contract AND tokenId. "
    f"reply with chain:contract:tokenId (like {CLAIM_FORMAT_EXAMPLE}) or "
    "the marketplace link, and it counts. this one cost you nothing."
)

# A marketplace link we could not resolve to a token (slug-only page, or the
# resolver is down): ask for the identity instead of guessing.
# NEVER "wrong" — we could not READ it, which is our limit, not the
# player's mistake (Pedro, 17/09). Hunt #11 jeered at link claims all
# afternoon because X had shortened every URL to t.co; the winner got
# through by typing the triple by hand. The wording says whose fault it
# is and what to send, and repeats that nothing was spent.
POST_REPLY_UNRESOLVED_LINK = (
    "i can't read that link — my side, not yours, and it cost you nothing. "
    "reply with chain:contract:tokenId "
    f"(like {CLAIM_FORMAT_EXAMPLE}) and it counts."
)

# Um link com CONTRATO mas sem tokenId — a página da colecção em vez da peça.
# Aconteceu duas vezes no hunt #12 (@Bode5t e @0xThalyn, ambos no Rarible e no
# OpenSea) e os dois ficaram-se pela colecção sem descer até ao token. Nesse
# caso sabemos exactamente o que falta, e "não consigo ler esse link" é uma
# verdade inútil: manda a pessoa adivinhar o que nós já sabemos. Continua a
# não acusar ninguém de nada e continua a não gastar tentativa.
POST_REPLY_COLLECTION_LINK = (
    "that's the collection, not the token — one piece short. open the exact "
    "one and paste its link, or reply with chain:contract:tokenId (like "
    f"{CLAIM_FORMAT_EXAMPLE}). this one cost you nothing."
)

# A Zora COIN link (Pedro, 29/10, after hunt #27): an ERC-20 has no pieces, so
# the collection reply above taught the wrong thing. No "zora" in the text
# (Pedro, 30/10): it is also the name of a chain we read, and no public reply
# names one (09/10). The player knows where the link came from. No guess spent.
POST_REPLY_COIN_LINK = (
    "that's a coin, not an nft — the treasure is an nft. paste the piece's "
    f"link, or reply with chain:contract:tokenId (like {CLAIM_FORMAT_EXAMPLE}). "
    "this one cost you nothing."
)

# More than one token in a reply (P0, 06/09): malformed — no guess spent,
# never a match. Public rule: name one token per reply.
POST_REPLY_ONE_TOKEN = (
    "one token per reply. name exactly one chain:contract:tokenId (or one "
    "link) and it counts — this one didn't."
)

# An address that is not an address (09/10, hunt #17): one character that is
# not hex — an "S" typed for a "5" — or one character too many or too few.
# It used to fall to the humour judge and earn "a name is not a claim", and a
# second player copied the same slip. We cannot judge what we cannot read, so
# this says nothing about right or wrong: it says what is wrong with the
# ADDRESS, which is the player's own text and tells nobody anything about
# the treasure. It names the character — that is the whole help.
_ADDRESS_RULE = "an address is 0x + 40 of 0-9 and a-f."
_BAD_ADDRESS_SHOWN = 3


def post_reply_bad_address(bad_chars, length: int) -> str:
    """`bad_chars`: the characters that are not hex (letters and digits only,
    as typed); `length`: characters after the 0x. Built from the post, so
    the text differs between players — which X prefers anyway."""
    shown = [f"'{c}'" for c in list(bad_chars)[:_BAD_ADDRESS_SHOWN]]
    more = " and more" if len(bad_chars) > _BAD_ADDRESS_SHOWN else ""
    if len(shown) == 1 and not more:
        chars = f"has a character that isn't hex — the {shown[0]}"
    elif shown:
        chars = f"has characters that aren't hex — {', '.join(shown)}{more}"
    else:
        chars = ""
    if length != 40 and chars:
        return (f"that address {chars} — and it is {length} characters after 0x "
                "where it takes 40. check it against the source and send it "
                "again. this one cost you nothing.")
    if length != 40:
        return (f"that address is {length} characters after 0x — it takes 40. "
                "check it and send it again. this one cost you nothing.")
    return (f"that address {chars}. {_ADDRESS_RULE} check it against the source "
            "and send it again. this one cost you nothing.")


# A token on a chain, or in a format, this game does not read: a chain word we
# do not know in front of a 0x contract, a Tezos / Solana / Bitcoin id, a link
# to a marketplace of one of those. ONE text for all three, and it LISTS NO
# CHAIN (Pedro, 09/10): the chain is part of the answer, and a public reply of
# the oracle must never narrow it. The example is the published format
# example, the same one every other reply carries.
#
# 14/10 (Pedro): a chain the LARDER can never produce is a wrong guess now —
# the treasure is never there (claims.matcher). This reply is left for the
# one case where "can't read" is still the truth: a chain the larder CAN
# produce and the claim parser does not read yet. Ours to fix, never theirs.
POST_REPLY_OTHER_CHAIN = (
    "i can't read that one — my side, not yours, and it cost you nothing. "
    f"reply with chain:contract:tokenId (like {CLAIM_FORMAT_EXAMPLE}) or "
    "the marketplace link."
)

# chain:contract:tokenId with a chain NAME nobody here knows (14/10, Pedro's
# text) — `arb`, `op`, `basechain`. It may be ours written another way: a
# format reply, no guess spent, and it lists no chain.
POST_REPLY_UNKNOWN_CHAIN = (
    "almost — i don't know that chain name. write it out in full: "
    f"chain:contract:tokenId (like {CLAIM_FORMAT_EXAMPLE}). this one cost you nothing."
)

# Appended to the jeer when a reply named something but no token. It is the
# line the eventual winner of Hunt #11 never got: he wrote the right name,
# was told he was wrong, and worked the format out on his own.
#
# NO "cost you nothing" here, deliberately (Pedro, 17/09): nobody who shouts
# a name thinks they spent an attempt, so the line would be noise. It belongs
# where someone could reasonably believe they had — an address, a broken
# link, contract:tokenId without the chain.
POST_REPLY_NAME_ONLY = (
    f"a name is not a claim — i need the exact token. reply with the "
    f"marketplace link, or chain:contract:tokenId (like {CLAIM_FORMAT_EXAMPLE})."
)

POST_REPLY_WRONG_DOOR_TARGET = (
    "the claim goes in the replies of the Clue 1 post — drop the "
    "chain:contract:tokenId (or link) there and it counts. \U0001F438"
)

# The consequence of the spray detector, worded for the pinned rules —
# the detector's numbers are reserved (hunt.PUBLIC_SPRAY_RULE is the source).
