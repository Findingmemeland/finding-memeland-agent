"""Target clue engine — Option A clues for an EXISTING third-party NFT.

EVOLUTION of the relic engine, not a rewrite: `TargetClueEngine` subclasses
`RelicClueEngine` and inherits the whole machinery that survived ten hunts —
the seeded puzzle ramp (7 hard pieces on name words + artwork, then the
reveal phase easing towards the name), the angle deck with its no-collision
rules, the guardrail loop with regeneration-on-feedback, the trail path and
the blind solver. What is target-specific lives here, and only this:

1. THE CONTEXT — built from a sealed Target: the BASE name (serial stripped;
   2+ words, not exactly 2), the artwork described by a vision model, and
   the on-chain description as flavour. The chain, contract and token id
   NEVER enter the context the writer sees: they are not attributes to hint
   at, they are the answer's address.

2. THE SYSTEM PROMPT — the target is somebody else's NFT, somewhere onchain.
   The old prompt said "a 1/1 minted on Base" with "a claim code in its
   description": both false now, and the first is a leak (decision: clues
   never state the chain — the chain is part of the answer).

3. TWO MECHANICAL GUARDS in `_post_guardrail_reasons`, before the solver:
   · NAME-OF-A-CHAIN-OR-PLATFORM: the clue must not contain ethereum/base/
     polygon/solana/…, nor foundation/superrare/opensea/…, as words. A
     platform name collapses the candidate set to one search box; a chain
     name is the address. Regex, word-boundary, case-insensitive — a prompt
     instruction is how emoji were "banned" and appeared anyway.
   · THE SEARCH GUARD (search_guard.ClueSearchGuard), puzzle phase only:
     the clue text is used as a marketplace query on the target's chain;
     if the target surfaces, the piece IS a search and is rejected. The
     guard runs its own canary first; an UNVERIFIABLE guard (blind canary,
     transport dead) raises immediately — fail-closed on every clue, not
     just clue 1: a leaked piece is forever, a delayed one is a non-event.

The ARTWORK description is produced once, at prepare time, by an injected
vision callable — and the image bytes are fetched through the same batched,
decoy-padded generic path as the live check (hunt.LiveCheck), never by a
lone request that tells a gateway which token we care about.
"""

from __future__ import annotations

import logging
import random
import re
from dataclasses import dataclass
from typing import Callable, Sequence

from ..content.clue_engine import HARD_CLUE_FLOOR, _parse_clue
from ..content.relic_clues import (
    PUZZLE_ANGLES,
    PUZZLE_CLUES,
    PUZZLE_PHASE_RULES,
    REVEAL_PHASE_RULES,
    RelicClueContext,
    RelicClueEngine,
    angle_for_unverifiable,
    enumerable_words_in,
    relic_guidance_for,
    relic_ramp_plan,
    relic_slot_for,
    spent_angles,
)
from .search_guard import ClueSearchGuard
from .selector import Target

# --------------------------------------------------------------------------- #
# Forbidden words — the address of the answer, never in a clue                  #
# --------------------------------------------------------------------------- #

CHAIN_WORDS = (
    "ethereum", "eth", "mainnet", "base", "polygon", "matic", "arbitrum",
    "optimism", "zora", "solana", "sol", "avalanche", "avax", "bnb", "bsc",
    "l1", "l2", "layer2", "layer-2", "rollup", "sidechain", "evm",
)
PLATFORM_WORDS = (
    "foundation", "superrare", "super rare", "makersplace", "makers place",
    "manifold", "opensea", "rarible", "knownorigin", "known origin",
    "async art", "asyncart", "blur", "looksrare", "looks rare", "magic eden",
    "objkt", "artblocks", "art blocks", "nifty gateway", "niftygateway",
)
_FORBIDDEN_RE = re.compile(
    r"\b(" + "|".join(re.escape(w) for w in CHAIN_WORDS + PLATFORM_WORDS) + r")\b",
    re.IGNORECASE,
)


def forbidden_address_words(text: str) -> list[str]:
    """Chain or platform names present in the clue text, as typed."""
    return sorted({m.group(1).lower() for m in _FORBIDDEN_RE.finditer(text or "")})


# --------------------------------------------------------------------------- #
# Context                                                                      #
# --------------------------------------------------------------------------- #

_ARTIST_KEYS = ("artist", "created_by", "creator", "author", "createdBy")


@dataclass
class TargetClueContext(RelicClueContext):
    """RelicClueContext plus what the target engine needs and the writer must
    never see. `target_id` and `name_onchain` feed the SEARCH GUARD only —
    they are never formatted into a prompt."""

    target_id: str = ""
    name_onchain: str = ""

    @classmethod
    def from_target(cls, target: Target, *, image_description: str,
                    metadata: dict | None = None) -> "TargetClueContext":
        """`image_description` comes from the vision step (see
        describe_image_batched); `metadata` (the full token metadata, if the
        caller has it) contributes the artist/creator name to the never-write
        list — a creator name is a search box too."""
        words = [w for w in re.findall(r"[A-Za-zÀ-ÿ]{2,}", target.name)]
        terms = [w.lower() for w in words]
        for k in _ARTIST_KEYS:
            v = (metadata or {}).get(k)
            if isinstance(v, str) and v.strip() and not v.startswith("0x"):
                terms.extend(t.lower() for t in re.findall(r"[A-Za-zÀ-ÿ]{3,}", v))
        return cls(
            display_name=target.name,
            image_description=image_description,
            lore=(target.description or "")[:400],
            backstory=(target.description or "")[:400],
            solution_terms=sorted(set(terms)),
            enumerable_words=enumerable_words_in(target.name),
            clue_facet_plan=relic_ramp_plan(target.name),
            angle_offset=sum(ord(c) for c in target.name) % len(PUZZLE_ANGLES),
            target_id=target.id(),
            name_onchain=target.name_onchain,
        )


# --------------------------------------------------------------------------- #
# Vision — batched image fetch, so the gateway never learns which one          #
# --------------------------------------------------------------------------- #


class ImageUnavailable(RuntimeError):
    """The artwork could not be fetched or described. The preparer treats
    this as launch-refusing (fail-closed): two puzzle pieces and the reveal
    description depend on it, and a hunt without them is a weaker hunt we
    never launch on hope."""


class ContentRefused(RuntimeError):
    """The image failed the content guard (NSFW, gore, hate symbols, stolen
    famous art, scam). Carries the target id so the caller EXCLUDES it and
    draws again — the message never carries a name."""

    def __init__(self, target_id: str, reason: str = ""):
        super().__init__("content guard refused the artwork")
        self.target_id = target_id
        self.reason = reason[:160]


def describe_image_batched(*, target_image_url: str,
                           decoy_image_urls: Sequence[str],
                           fetch_bytes_generic: Callable[[str], bytes | None],
                           describe: Callable[[bytes], str],
                           decoys: int = 7,
                           rng: random.Random | None = None,
                           content_ok: Callable[[bytes], "bool | None"] | None = None,
                           target_id: str = "") -> str:
    """Fetch the target's image inside a shuffled batch of decoy images (from
    snapshot entries) through a generic public gateway, then describe ONLY
    the target's bytes with the vision callable (our own provider, our own
    key — that is fine; it is the marketplace/gateway that must not learn
    the target). Decoy fetch results are discarded; a decoy failure is
    noise. Raises ImageUnavailable when the target's bytes cannot be
    fetched or the description is empty.

    CONTENT GUARD (Opus, 06/09): text cannot see NSFW or stolen art, and
    this is the one place the target's IMAGE is already in hand, inside a
    batch that already exists — so `content_ok(bytes)` runs here, on the
    same bytes, zero extra reads. False → ContentRefused(target_id) (the
    caller excludes and redraws); None (vision unreachable) → fail-closed,
    ImageUnavailable. The batched text judge keeps writability."""
    rng = rng or random.SystemRandom()
    from .refresh import uri_is_content_addressed
    others = [u for u in decoy_image_urls
              if u and u != target_image_url and uri_is_content_addressed(u)]
    # P1-5: the batch is EXACTLY decoys + 1 through the batch's gateway, or
    # it is not a batch. A missing or https:// image silently shrank it.
    if len(others) < decoys or not uri_is_content_addressed(target_image_url):
        raise ImageUnavailable(
            f"image batch would have {len(others) + 1} gateway reads, not "
            f"{decoys + 1} — a decoy without a content-addressed image "
            "shrinks the anonymity set; refusing")
    batch = rng.sample(others, decoys) + [target_image_url]
    rng.shuffle(batch)
    target_bytes: bytes | None = None
    for url in batch:
        try:
            data = fetch_bytes_generic(url)
        except Exception:  # noqa: BLE001 — a decoy's failure is noise
            data = None
        if url == target_image_url:
            target_bytes = data
    if not target_bytes:
        raise ImageUnavailable("artwork bytes unavailable via the generic "
                               "gateway — not launching without the art")
    if content_ok is not None:
        verdict = content_ok(target_bytes)
        if verdict is None:
            raise ImageUnavailable("content guard unreachable — not launching "
                                   "on an unchecked artwork (fail-closed)")
        if verdict is False:
            raise ContentRefused(target_id)
    text = (describe(target_bytes) or "").strip()
    if not text:
        raise ImageUnavailable("vision returned an empty description")
    return text


# --------------------------------------------------------------------------- #
# Prompt                                                                       #
# --------------------------------------------------------------------------- #

TARGET_SYSTEM_PROMPT = """You are the game master of "Finding Memeland", writing \
CLUES for the current treasure hunt, posted on the main @FindingMemeland account. \
The hidden target is an EXISTING NFT that belongs to somebody else, somewhere \
onchain. Players WIN by working out the NFT's NAME, finding that exact token on a \
marketplace, and posting its identity (chain:contract:tokenId, or the marketplace \
link) as a REPLY to the Clue 1 post. There are NO DMs in this game — never tell \
players to DM anyone or mention DMs at all.

Your clues point at exactly TWO things: the words of the NFT's NAME and its \
ARTWORK. Nothing else. The NAME is the only searchable thing, so the name carries \
the hunt.

ABSOLUTE SECRECY OF THE ADDRESS: never say or hint WHICH blockchain the NFT lives \
on, WHICH marketplace or platform it was minted or listed on, WHO made it, or \
WHEN. Not as a word, not as a nickname, not as an allusion ("the chain everyone \
moved to", "the blue marketplace"). The chain and the platform are part of the \
ANSWER — a player must work them out by finding the token, and a clue that names \
them collapses the game into one search box. A mechanical guard rejects any clue \
containing a chain or platform name.

THE PUZZLE DOCTRINE (the heart of the game). Early clues are PIECES of a puzzle, \
not steps of a staircase. Players solve these with AI help, cross-referencing \
every clue against candidate answers — so a piece is NOT meant to be solvable on \
its own. Each piece is a CONSTRAINT that narrows the space of possible words (a \
semantic field, a cultural use, how the word is built, a relation between the \
words, a detail of the art), and the pieces INTERSECT on exactly one answer. \
Write each clue so that: (a) alone it is genuinely hard and could fit several \
words; (b) combined with the earlier clues it eliminates almost everything else; \
(c) it is CHECKABLE — a solver who guesses the right word can confirm the clue \
fits, and one who guesses wrong can rule it out. Never repeat an angle you have \
already used: each clue must add NEW information, or the intersection never \
tightens.

A SEARCH GUARD also reads every puzzle-phase clue: it types your clue into a \
marketplace search box, and if the target comes up, the clue is rejected. A \
puzzle piece must never work as a search query — no literal description of the \
picture, no phrase that appears in the title.

The on-chain description (the 'lore' below) is FLAVOUR only — never make players \
guess it, and never quote it: a quoted phrase is a search query.

Voice: playful, ironic, meme-native crypto Twitter. Community language, cheeky, \
lowercase is fine. NOT mystical or poetic. A smug oracle enjoying the struggle. \
Emoji only where the phase rules below allow them.

Hard rules for the clue text:
- One short post, max ~200 characters. Standalone clue text only.
- NEVER write verbatim: the NFT's name or any of its words, the creator's name, \
any URL, or hashtags. You HINT at them; you never spell them out — that is the \
puzzle.
{phase_rules}
- Obliqueness. You are writing clue #{index}; target obliqueness {obliqueness} \
(1.0 = maximally subtle; lower = clearer). The difficulty of EACH clue is set by \
the game's ramp — obey the number, not the clue's position. Never just write the \
name.
- HARD clues (obliqueness {hard_floor} or higher) are ONE oblique angle: a single \
sideways reference that rewards knowledge or lateral thinking. NEVER a list of \
synonyms, NEVER more than one identifying fact (if your clue splits into two \
facts, delete one), and NEVER an etymology, dictionary meaning or "the name \
means…" — a meaning is a lookup, save it for the easy revisit. The test is \
RECOGNITION vs INFERENCE: if the clue DESCRIBES the thing and the reader merely \
recognises it, that is a lookup — too easy, rewrite.
- THE ONE-MINUTE TEST for every hard clue: would a sharp player WITH AN AI, \
holding ONLY this clue, land on the word in under a minute? If yes, rewrite it. \
The SIDEWAYS DEFINITION is the most common failure: rephrasing what a word MEANS \
is still a definition. A hard clue attacks from a direction that is NOT the \
word's meaning: how it is built, where culture puts it to work, which world it \
lives in, how it leans on the other words.
- Each clue must add a NEW angle, roughly 30% clearer than the previous one.
- NEVER build a clue on counting: do not state how many syllables, letters, \
characters, vowels or consonants anything has. The only number you may state \
about the name is its word count, and only if you are certain it is exact.
- FACET TARGETING: each clue focuses on ONE real attribute (given below) and must \
CRYPTICALLY signal which one — name or artwork — naming the facet outright only \
when obviousness is high (obliqueness 0.4 or lower).
- WHERE to search is NOT your job: the pinned rules tell players it is an NFT \
and how to claim. Never spend a clue on that.
- SPELLING WARNING: marketplace search is UNFORGIVING. If the name is spelt in a \
non-standard way, you MUST say so PLAINLY in the reveal phase ("it's spelt wrong \
on purpose", "drop a letter from what you'd expect"). A sly hint is NOT enough.

For clue #1 only, set taunt to "". For clue #2 and later, also write a short, \
varying jeer that pokes fun at players for not solving it yet. If the jeer \
mentions how many clues are out, the number is exactly {index} — this one \
included. Never call a clue "final" or "last": the ramp may continue.

Respond with ONLY a JSON object: {{"clue": "...", "taunt": "..."}}"""


def build_target_user_message(ctx: TargetClueContext, clue_index: int,
                              prior_clues: list[str]) -> str:
    """Mirrors build_relic_user_message on the DIRECT path (no anchor angle —
    an unverified artefact is the one clue worse than a hard one). The
    target's address never appears here."""
    prior = ("\n".join(f"- {c}" for c in prior_clues) if prior_clues
             else "(none — this is the first clue)")
    vector, obliqueness = relic_slot_for(clue_index, ctx)
    angle = angle_for_unverifiable(clue_index, ctx)
    spent = spent_angles(clue_index, ctx, allow_anchor=False)
    n_words = len(ctx.display_name.split())
    return (
        "The NFT's REAL attributes (point clues AT these; never write them verbatim):\n"
        f"- name ({n_words} words): {ctx.display_name}\n"
        f"- artwork: {ctx.image_description}\n"
        f"- lore (on-chain description, FLAVOUR ONLY, never quote it): {ctx.lore or '(none)'}\n"
        "\n"
        f"Terms to NEVER write: {ctx.solution_terms}\n\n"
        f"This is clue #{clue_index}. Target obliqueness: {obliqueness}.\n"
        + (
            f"PUZZLE PIECE {clue_index} of {PUZZLE_CLUES}: this clue is one piece "
            "of the puzzle, not the answer. Give ONE new constraint on the target "
            "— a single angle nobody could turn into the word by itself, but which "
            "a solver can CHECK against a candidate. No synonym lists, no "
            "explanations, no 'the word means…'.\n"
            if clue_index <= PUZZLE_CLUES else ""
        )
        + (f"ANGLE FOR THIS PIECE (use THIS one, not another): {angle}\n"
           if angle else "")
        + ("ALREADY SPENT on this word — do NOT repeat these angles: "
           + ", ".join(spent) + "\n" if spent else "")
        + (
            "ENUMERABLE WORD(S) IN THIS NAME: " + ", ".join(ctx.enumerable_words)
            + ". These belong to a set a player can list out loud. NEVER let a "
            "clue gesture at the CATEGORY — attack these words ONLY by cultural "
            "use, structure, or their relation to the other words.\n"
            if clue_index <= PUZZLE_CLUES and ctx.enumerable_words else ""
        )
        + f"FACET for this clue: {vector} — {relic_guidance_for(vector, ctx, clue_index)}\n"
        + f"Previous clues:\n{prior}\n\n"
        f"Write clue #{clue_index}."
    )


# --------------------------------------------------------------------------- #
# Engine                                                                       #
# --------------------------------------------------------------------------- #


class SearchGuardUnavailable(RuntimeError):
    """The search guard could not verify a puzzle-phase clue (blind canary or
    transport). Raised, not returned as feedback: fail-closed on EVERY clue
    — the operator alert reaches Telegram at once, and nothing is published
    on the text rules alone. The message never carries the clue."""


class TargetClueEngine(RelicClueEngine):
    """RelicClueEngine with the target prompt/context and two extra guards.

    `search_guard`: a ClueSearchGuard (required — the puzzle phase does not
    run without it; pass `search_guard=False` ONLY in offline simulations,
    never in production wiring)."""

    def __init__(self, anthropic_client, model: str, *, search_guard,
                 trail_verifier=None, trail_policy=None, solver=None):
        super().__init__(anthropic_client, model, trail_verifier=trail_verifier,
                         trail_policy=trail_policy, solver=solver)
        if search_guard is None:
            raise ValueError("TargetClueEngine needs a ClueSearchGuard "
                             "(search_guard=False only for offline simulation)")
        self._search_guard: ClueSearchGuard | None = (
            None if search_guard is False else search_guard)
        self._forbidden_hits: dict[str, int] = {}

    def next_clue(self, persona, clue_index, prior_clues, *, max_attempts: int = 6):
        self._forbidden_hits = {}
        return super().next_clue(persona, clue_index, prior_clues,
                                 max_attempts=max_attempts)

    def _post_guardrail_reasons(self, draft, persona, clue_index, prior_clues):
        # 1. the address of the answer, mechanically, every phase
        words = forbidden_address_words(draft.text)
        if words:
            # Opus (06/09, Q3): a ramp fighting the same term attempt after
            # attempt ("the base of the…") must be visible in the log, not
            # discovered by accident when the generator exhausts its tries.
            for w in words:
                self._forbidden_hits[w] = self._forbidden_hits.get(w, 0) + 1
                if self._forbidden_hits[w] >= 2:
                    logging.getLogger(__name__).warning(
                        "clue #%s: generator hit forbidden address word %r "
                        "%s times in this clue's attempts", clue_index, w,
                        self._forbidden_hits[w])
            return ["the clue names a blockchain or a platform (" +
                    ", ".join(words) + ") — the chain and the platform are part "
                    "of the ANSWER; remove every such word and any allusion to "
                    "them"]
        # 2. the search guard, puzzle phase only
        if self._search_guard is not None and clue_index <= PUZZLE_CLUES:
            v = self._search_guard.check(
                draft.text, target_item_id=persona.target_id,
                target_name_onchain=persona.name_onchain)
            if not v.ok and v.found is None:
                raise SearchGuardUnavailable(
                    f"search guard unverifiable for clue #{clue_index}: "
                    f"{v.detail} — not publishing (fail-closed)")
            if not v.ok:
                return ["a SEARCH GUARD typed this clue into a marketplace search "
                        "and the target came up — the piece IS a search. Remove "
                        "any literal description of the picture or phrase that "
                        "could appear in a title; attack from the assigned angle "
                        "only"]
        # 3. the blind solver (inherited)
        return super()._post_guardrail_reasons(draft, persona, clue_index, prior_clues)

    def generate(self, persona, clue_index, prior_clues, *, feedback=None):
        obliqueness = relic_slot_for(clue_index, persona)[1]
        system = TARGET_SYSTEM_PROMPT.format(
            index=clue_index, obliqueness=obliqueness, hard_floor=HARD_CLUE_FLOOR,
            phase_rules=(PUZZLE_PHASE_RULES if clue_index <= PUZZLE_CLUES
                         else REVEAL_PHASE_RULES),
        )
        user = build_target_user_message(persona, clue_index, prior_clues)
        if feedback:
            user += "\n\n" + feedback
        resp = self._client.messages.create(
            model=self._model, max_tokens=512, system=system,
            messages=[{"role": "user", "content": user}],
        )
        text = "".join(b.text for b in resp.content if getattr(b, "type", "") == "text")
        return _parse_clue(text)
