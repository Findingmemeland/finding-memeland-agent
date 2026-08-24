"""Relic clue engine — the hunt's clues when the target is an on-chain RELIC.

EVOLUTION, not a rewrite: `RelicClueEngine` subclasses `ClueEngine` and inherits
the whole guardrail loop (regeneration with feedback, anti-parroting, count
stripping, JSON parsing). Only three things are relic-specific and live here:

1. the RAMP — a relic has no @handle, no bio, no pinned/anchor posts. Its real,
   observable attributes are: the two words of its NAME (the only search vector),
   its ARTWORK, and its on-chain LORE (description). Last resort is the SURFACE
   ("it's an NFT on Base — search the name on an explorer"), which replaces the
   old handle phase;
2. the SYSTEM PROMPT — the target is an NFT whose claim code sits in its
   DESCRIPTION, found by NAME search on a neutral explorer, not an X account;
3. the USER MESSAGE — only the attributes a relic actually has.

Multi-angle clues (indirect real-world facts) are deliberately NOT here: the
debut runs DIRECT clues on name and image (decision 2026-08-22 §3.4). A
hallucinated fact would make a hunt unsolvable — that is the worst outcome, so
it waits for a low-risk hunt and a verification step.
"""

from __future__ import annotations

import random
from dataclasses import dataclass, field

from .clue_engine import (
    HARD_CLUE_FLOOR,
    RAMP_NAME_EASY,
    RAMP_NAME_HARD,
    ClueDraft,
    ClueEngine,
    _name_facets,
    _ordinal,
    _parse_clue,
)

# --------------------------------------------------------------------------- #
# The relic ramp (Pedro, 2026-08-22, after the first live clue sample).         #
#
# ONLY name and image facets exist. There is no lore facet (an NFT description
# is not searchable — a lore clue can only CONFIRM a relic you already found,
# never help you find it) and no "where to search" facet (the pinned rules
# already say it: NFT marketplaces, with examples).
#
# PUZZLE PHASE (clues 1..PUZZLE_CLUES): every clue is a PIECE, all of them hard.
# Players solve with AI help, so a piece is not meant to be solvable alone — it
# is a CONSTRAINT that narrows the space, and the pieces INTERSECT to a single
# name. This is why the hunt can open brutally hard and still be fair.
#
# REVEAL PHASE (after that): straightforward clues on each name word, getting
# easier every time, alternating between the two words until someone wins.
# --------------------------------------------------------------------------- #
PUZZLE_CLUES = 7               # clues 1..7 are puzzle pieces
PUZZLE_OBLIQUENESS = (0.9, 0.9, 0.85, 0.8, 0.75, 0.7, 0.65)
# The artwork gets TWO pieces: one HARD (an oblique detail) and one EASY (a plain
# description). Rationale (Pedro, 2026-08-22): you cannot SEARCH an image, so the
# art never leads you to the name — it lets you RECOGNISE the right relic once
# you're there. Spending hard slots on it would waste the puzzle.
PUZZLE_IMAGE_PIECES = 2
IMAGE_EASY_OBLIQUENESS = 0.25  # the plain-description art piece
# Every name word keeps at least this many puzzle pieces. A player needs BOTH
# words to search the relic, so a word with one clue is a dead hunt.
MIN_PIECES_PER_WORD = 2
REVEAL_START = 0.4             # first easy clue after the puzzle phase
REVEAL_FLOOR = 0.05
_REVEAL_STEP = 0.05

# The ANGLES a piece can attack a word from. The generator is told which angle to
# use and which are already spent, so nine clues can't collapse into three
# rephrasings of the same idea (measured failure, 2026-08-22 clue sample).
PUZZLE_ANGLES = (
    "SOUND/RHYTHM: how the word sounds — syllables' feel, what it rhymes or "
    "half-rhymes with, its cadence (never a letter or syllable COUNT).",
    # NOTE: ORIGIN/ETYMOLOGY is deliberately NOT a puzzle angle. Measured
    # 2026-08-22: an etymology clue EXPLAINS the word ("latin roots meaning
    # 'severe'"), which is exactly the job of the reveal phase — a hard piece
    # using it stole the easy clue's work and half-gave the answer. Meaning
    # belongs after the puzzle phase, and the standing hard-clue doctrine
    # already bans it ("never an etymology, dictionary meaning or 'the name
    # means…'").
    "SEMANTIC FIELD: the world the word belongs to — its neighbours, what it sits "
    "between, what it is emphatically NOT.",
    "CULTURAL USE: where a person actually meets this word — a saying, a job, an "
    "object, a scene it belongs to.",
    "STRUCTURE: how the word is BUILT — a compound, a suffix that does work, two "
    "halves that each mean something.",
    "RELATION: how this word sits against the OTHER word of the name — the "
    "contrast, the joke, or the image the pair makes together.",
    # Pedro's angle (2026-08-23). Turns a riddle into a SEARCH: instead of
    # circling what the word means, point at one specific place in the real world
    # where the answer physically appears, and let players go and look. It cannot
    # be a definition by construction, it is checkable, and it rewards persistence
    # rather than vocabulary — which widens who can win.
    "CONCRETE ANCHOR: name ONE specific real-world artefact where the answer "
    "physically shows up — a scene in a named film, a line in a known book, a "
    "moment in a documented event, an object in a museum, a lyric — and describe "
    "the SPOT without naming the answer ('the colour of the sofa in the basement "
    "scene of <film>'). Only use an anchor you are CERTAIN of: a wrong one makes "
    "players eliminate the RIGHT answer, which is worse than a clue that is too "
    "hard. It must be reachable in English, and durable — an encyclopedia entry "
    "or a famous scene, never an ephemeral post that can be deleted.",
)


@dataclass
class RelicClueContext:
    """What the clue engine reasons over for a relic hunt.

    Duck-types the fields `check_clue`/`ClueEngine.next_clue` read from a
    PersonaContext (display_name/handle/bio/solution_terms/handle_hint), with the
    account-only ones EMPTY — a relic has no handle or bio, and empty strings
    make the guardrail's leak check a no-op for them."""

    display_name: str          # the relic name (2 words) — the only search vector
    image_description: str     # what the artwork shows (from the image prompt)
    lore: str                  # the on-chain description, WITHOUT the code line
    backstory: str             # theme/flavour only — never to be guessed
    solution_terms: list[str] = field(default_factory=list)

    # Present so inherited machinery works unchanged; always empty for a relic.
    handle: str = ""
    bio: str = ""
    handle_hint: str = ""

    # Words in the name a player could ENUMERATE (kinship, colours, days, crypto
    # jargon). Derived, never stored: the encrypted identity keeps no such field.
    # This is the fix for mini hunt #1 (2026-08-23) — the failure was never the
    # word "uncle", it was the clue "a title that skips a generation", which hands
    # over the category and leaves ten candidates. The word stays legal; the clue
    # is what has to change.
    enumerable_words: tuple[str, ...] = ()

    clue_facet_plan: list = field(default_factory=list)
    # Rotates which ANGLE each piece uses, so two hunts don't attack their names
    # in the same order. Derived from the name, so it's stable within a hunt
    # (clue N always gets the same angle, even after a crash-resume).
    angle_offset: int = 0

    @classmethod
    def from_identity(cls, identity, *, backstory: str = "") -> "RelicClueContext":
        """Build from a RelicIdentity (package 1). `identity.description` is the
        lore WITHOUT the appended code line (the code is added only at mint)."""
        return cls(
            display_name=identity.name,
            image_description=identity.image_prompt,
            lore=identity.description,
            backstory=backstory or identity.description,
            solution_terms=list(identity.solution_terms),
            enumerable_words=enumerable_words_in(identity.name),
            clue_facet_plan=relic_ramp_plan(identity.name),
            angle_offset=sum(ord(c) for c in identity.name) % len(PUZZLE_ANGLES),
        )


def enumerable_words_in(name: str) -> tuple[str, ...]:
    """Which words of the name belong to a set a player can list out loud.

    Deliberately NOT a rejection anywhere (Pedro, 2026-08-23): knowing one of the
    two words gets nobody closer, because marketplace search needs both. It is a
    constraint handed to the clue writer instead."""
    import re

    from ..persona.relic_generator import CLOSED_CATEGORY_WORDS, CRYPTO_JARGON_WORDS

    runs = re.findall(r"[a-z]+", str(name).lower())
    return tuple(
        sorted({r for r in runs if r in CLOSED_CATEGORY_WORDS or r in CRYPTO_JARGON_WORDS})
    )


def relic_ramp_plan(name: str) -> list:
    """The PUZZLE PHASE: `PUZZLE_CLUES` hard pieces, as (facet, obliqueness).

    Facets cycle through the name words so both get several independent angles,
    with `PUZZLE_IMAGE_PIECES` of the slots reassigned to the artwork. The image
    is NEVER at clue 1 (the hunt always opens on a name piece) and the image
    pieces are placed at random positions, so the order isn't predictable across
    hunts."""
    words = _name_facets(name)
    slots = [words[i % len(words)] for i in range(PUZZLE_CLUES)]

    # Reassign slots to the artwork — never position 0, and NEVER starving a name
    # word. Measured failure (2026-08-22, Uncle Pump): both art slots landed on
    # word-2 positions, leaving word 2 with a SINGLE puzzle clue. Since a player
    # needs BOTH words to search the relic at all, starving one word makes the
    # hunt unwinnable no matter how good the other clues are.
    art_at: list[int] = []
    spots = list(range(1, PUZZLE_CLUES))
    random.shuffle(spots)
    for pos in spots:
        if len(art_at) >= max(0, PUZZLE_IMAGE_PIECES):
            break
        facet = slots[pos]
        remaining = sum(1 for i, f in enumerate(slots) if f == facet and i not in art_at)
        if remaining <= MIN_PIECES_PER_WORD:
            continue          # taking this slot would starve that word
        art_at.append(pos)
    for pos in art_at:
        slots[pos] = "image"
    plan = []
    for i, facet in enumerate(slots):
        if facet == "image":
            # One art piece stays HARD (an oblique detail); the rest are EASY
            # plain descriptions — the art is for recognition, not for searching.
            hard_art = i == min(art_at)
            obl = PUZZLE_OBLIQUENESS[i] if hard_art else IMAGE_EASY_OBLIQUENESS
        else:
            obl = PUZZLE_OBLIQUENESS[i]
        plan.append((facet, obl))
    return plan


def relic_slot_for(clue_index: int, ctx: RelicClueContext) -> tuple:
    """(facet, obliqueness) for this clue.

    PUZZLE PHASE (1..PUZZLE_CLUES): hard pieces on the name words and the
    artwork — each one a constraint, together decisive.

    REVEAL PHASE (after that): plain clues on each name word, alternating
    between the two and getting easier every time, forever (a hunt nobody solves
    keeps easing until the void timeout closes it)."""
    plan = ctx.clue_facet_plan or relic_ramp_plan(ctx.display_name)
    n = len(plan)
    if clue_index <= n:
        return tuple(plan[clue_index - 1])
    words = _name_facets(ctx.display_name)
    step = clue_index - n - 1                      # 0-based inside the reveal phase
    facet = words[step % len(words)]               # alternate the two name words
    obl = round(max(REVEAL_FLOOR, REVEAL_START - _REVEAL_STEP * step), 3)
    return (facet, obl)


RELIC_VECTOR_GUIDANCE = {
    "image": "the RELIC'S ARTWORK — give ONE angle on the picture. In the puzzle "
    "phase this is a PIECE, not a description: an oblique detail, a mood, an "
    "object in frame, something a solver can cross-reference against candidates "
    "(never a plain 'it shows an X'). Later, describe it plainly so players "
    "recognise the exact relic among look-alikes.",
}


def angle_for(clue_index: int, ctx: RelicClueContext) -> str | None:
    """Which ANGLE this puzzle piece must attack the word from, or None outside
    the puzzle phase / for art pieces.

    Assigned per (facet, occurrence): the first piece on a word uses the first
    angle, the second piece a different one, and so on — so two pieces on the
    same word can never rehash the same idea (the failure measured on
    2026-08-22: nine clues, three angles). Rotated per hunt so the sequence
    isn't identical every time."""
    facet, _ = relic_slot_for(clue_index, ctx)
    if clue_index > PUZZLE_CLUES or facet == "image":
        return None
    # How many earlier pieces already targeted this same word?
    seen = sum(
        1 for i in range(1, clue_index) if relic_slot_for(i, ctx)[0] == facet
    )
    offset = getattr(ctx, "angle_offset", 0)
    return PUZZLE_ANGLES[(seen + offset) % len(PUZZLE_ANGLES)]


def is_anchor_angle(angle: str | None) -> bool:
    return bool(angle) and angle.startswith("CONCRETE ANCHOR")


def angle_for_unverifiable(clue_index: int, ctx: RelicClueContext) -> str | None:
    """The angle to use when the CONCRETE ANCHOR one cannot be verified.

    An anchor nobody checked is the worst clue we can publish: a wrong artefact
    makes players eliminate the RIGHT answer after hours of work, which is far
    worse than a clue that is merely hard. So when no verifier is wired — or the
    model could not produce an artefact that checks out — we do NOT fall back to
    'write the anchor clue anyway'; we shift to the next angle that needs no
    external fact."""
    angle = angle_for(clue_index, ctx)
    if not is_anchor_angle(angle):
        return angle
    facet, _ = relic_slot_for(clue_index, ctx)
    if facet == "image" or clue_index > PUZZLE_CLUES:
        return None
    # A word's pieces take CONSECUTIVE angles: offset+0, offset+1, ... offset+m-1.
    # So the substitute must come from BEYOND that run — stepping by +1 would hand
    # this piece the next piece's angle and rebuild the very failure measured on
    # 2026-08-22 (nine clues, three angles). Verified: the naive +1 shift collided
    # on 7 of 10 sample names.
    n = len(PUZZLE_ANGLES)
    offset = getattr(ctx, "angle_offset", 0)
    total = sum(
        1 for i in range(1, PUZZLE_CLUES + 1) if relic_slot_for(i, ctx)[0] == facet
    )
    for step in range(total, n):
        candidate = PUZZLE_ANGLES[(offset + step) % n]
        if not is_anchor_angle(candidate):
            return candidate
    return None


def spent_angles(
    clue_index: int, ctx: RelicClueContext, *, allow_anchor: bool = True
) -> list[str]:
    """The angles already used on THIS word — listed in the prompt as forbidden.

    Must be computed under the SAME anchor rule as the clue being written, or the
    list is wrong twice over: it would name CONCRETE ANCHOR as spent on a path
    that never writes anchors (putting the idea in front of the model for no
    reason) while hiding the angle that was actually substituted in its place —
    so that one could be handed out a second time."""
    facet, _ = relic_slot_for(clue_index, ctx)
    pick = angle_for if allow_anchor else angle_for_unverifiable
    out = []
    for i in range(1, clue_index):
        if relic_slot_for(i, ctx)[0] == facet:
            a = pick(i, ctx)
            if a:
                out.append(a.split(":")[0])
    return out


def relic_guidance_for(facet: str, ctx: RelicClueContext) -> str:
    """Facet -> guidance, including the dynamic name-word facets."""
    if facet.startswith("name_word_"):
        n = int(facet.rsplit("_", 1)[1])
        words = ctx.display_name.split()
        word = words[n - 1] if 0 < n <= len(words) else ""
        which = "the only word" if len(words) <= 1 else f"the {_ordinal(n)} word"
        return (
            f"{which} of the relic's NAME (the word '{word}') — hint at THAT EXACT "
            "word (its meaning, a synonym, or wordplay on it) so a player decoding "
            "the hint arrives at the literal word and can search it. Do NOT "
            "substitute a theme-related word. Never write the word itself. "
            "CRITICAL: signal this is about a WORD of the NAME, and do NOT "
            "describe the artwork or any visual element — a visually framed clue "
            "misreads as a picture clue."
        )
    return RELIC_VECTOR_GUIDANCE[facet]


RELIC_SYSTEM_PROMPT = """You are the game master of "Finding Memeland", writing \
CLUES for the current treasure hunt, posted on the main @FindingMemeland account. \
The hidden target is a RELIC: a 1/1 NFT minted on Base. Players WIN by working out \
the relic's NAME, searching that name on a block explorer or NFT marketplace, \
reading the claim code from the relic's DESCRIPTION, and posting the relic's name \
and that code as a REPLY to the Clue 1 post (claim-by-post). There are NO DMs in \
this game — never tell players to DM anyone or mention DMs at all.

Your clues point at exactly TWO things: the words of its NAME and its ARTWORK. \
Nothing else. The NAME is the only searchable thing, so the name carries the hunt.

THE PUZZLE DOCTRINE (this is the heart of the game). Early clues are PIECES of a \
puzzle, not steps of a staircase. Players solve these with AI help, cross-\
referencing every clue against candidate answers — so a piece is NOT meant to be \
solvable on its own. Each piece is a CONSTRAINT that narrows the space of possible \
words (a sound, an origin, a semantic field, a rhythm, a relation between the two \
words, a detail of the art), and the pieces INTERSECT on exactly one answer. Write \
each clue so that: (a) alone it is genuinely hard and could fit several words; \
(b) combined with the earlier clues it eliminates almost everything else; \
(c) it is CHECKABLE — a solver who guesses the right word can confirm the clue \
fits, and one who guesses wrong can rule it out. A piece that cannot be checked \
against a candidate is a bad piece. Never repeat an angle you have already used: \
each clue must add NEW information, or the intersection never tightens.

The relic is themed around a concept/figure (the 'theme' below) ONLY for coherence \
and flavour — never make players guess the theme; make them FIND the relic by its \
real attributes.

Voice: playful, ironic, meme-native crypto Twitter. Community language, cheeky, \
lowercase is fine, the occasional emoji. NOT mystical or poetic. A smug oracle \
enjoying the struggle.

Hard rules for the clue text:
- One short post, max ~200 characters. Standalone clue text only.
- NEVER write verbatim: the relic's name or any of its words, the claim code, the \
theme/solution terms, any URL, or hashtags. You HINT at them; you never spell them \
out — that is the puzzle.
- Obliqueness. You are writing clue #{index}; target obliqueness {obliqueness} \
(1.0 = maximally subtle; lower = clearer). The difficulty of EACH clue is set by \
the game's ramp — obey the number, not the clue's position (an early clue can be \
deliberately obvious). Never just write the name.
- HARD clues (obliqueness {hard_floor} or higher) are ONE oblique angle: a single \
sideways reference that rewards knowledge or lateral thinking. NEVER a list of \
synonyms, NEVER a retelling of the figure's story, NEVER more than one identifying \
fact (if your clue splits into two facts, delete one), and NEVER an etymology, \
dictionary meaning or "the name means…" — a meaning is a lookup, save it for the \
easy revisit. The test is RECOGNITION vs INFERENCE: if the clue DESCRIBES the thing \
and the reader merely recognises it, that is a lookup — too easy, rewrite; a hard \
clue gives a lateral angle the reader has to JUMP to. The example lines you may \
have seen elsewhere are CALIBRATION ONLY — never reuse their wording or imagery; \
invent a completely original angle every time.
- THE ONE-MINUTE TEST for every hard clue, applied before you answer: would a \
sharp player WITH AN AI, holding ONLY this clue and nothing else, land on the word \
in under a minute? If yes, it is not hard — rewrite it. This kills the most common \
failure: the SIDEWAYS DEFINITION. Rephrasing what a word MEANS is still a \
definition, however cleverly you angle it — "a title that skips a generation" and \
"not your dad, not a stranger" are both just "uncle" with extra steps, and both \
lost a whole hunt in twelve minutes. A hard clue must attack from a direction that \
is NOT the word's meaning at all: how it sounds, how it is built, where culture \
puts it to work, how it leans on the other word. Meaning is the easy revisit — \
never the opening.
- Each clue must add a NEW angle, roughly 30% clearer than the previous one. Do \
not repeat earlier clues.
- NEVER build a clue on counting: do not state how many syllables, letters, \
characters, vowels or consonants anything has — you miscount them, and a wrong \
count makes players eliminate the RIGHT answer. The only number you may state \
about the name is its word count, and only if you are certain it is exact.
- FACET TARGETING: each clue focuses on ONE real attribute (given below) and must \
CRYPTICALLY signal which one — so players know whether to look at the name or the \
artwork. Signal it indirectly, naming the facet outright only when obviousness is \
high (obliqueness 0.4 or lower).
- WHERE to search is NOT your job: the pinned rules already tell players the relic \
is an NFT and which marketplaces to look on. Never spend a clue on that.
- SPELLING WARNING (important): marketplace search is UNFORGIVING — a player who \
types the expected spelling of a misspelt name finds NOTHING and concludes they \
were wrong. So if the relic's name is spelt in a non-standard way (a letter \
dropped or swapped, a deliberate misspelling, an odd compound), you MUST say so \
PLAINLY in the reveal phase — e.g. "it's spelt wrong on purpose" or "drop a letter \
from what you'd expect". A sly hint like "almost" is NOT enough. In the puzzle \
phase you may hint at it obliquely; in the reveal phase it must be unmissable.

For clue #1 only, set taunt to "". For clue #2 and later, also write a short, \
varying jeer that pokes fun at players for not solving it yet. If the jeer mentions \
how many clues are out, the number is exactly {index} — this one included. Clue \
counts and jeering belong in the taunt ONLY: the clue text never opens with "N \
clues in…" or any jab. Never call a clue "final" or "last": the ramp may continue.

Respond with ONLY a JSON object: {{"clue": "...", "taunt": "..."}}"""


def build_relic_user_message(
    ctx: RelicClueContext,
    clue_index: int,
    prior_clues: list[str],
    *,
    allow_anchor: bool = True,
) -> str:
    """`allow_anchor=False` swaps the CONCRETE ANCHOR angle for one that needs no
    external fact — used whenever the anchor cannot be verified."""
    prior = "\n".join(f"- {c}" for c in prior_clues) if prior_clues else "(none — this is the first clue)"
    vector, obliqueness = relic_slot_for(clue_index, ctx)
    angle = angle_for(clue_index, ctx) if allow_anchor else angle_for_unverifiable(clue_index, ctx)
    spent = spent_angles(clue_index, ctx, allow_anchor=allow_anchor)
    return (
        "The relic's REAL attributes (point clues AT these; never write them verbatim):\n"
        f"- name: {ctx.display_name}\n"
        f"- artwork: {ctx.image_description}\n"
        "\n"
        f"Theme (FLAVOUR ONLY — do NOT make players guess this, do not write it): "
        f"{ctx.backstory}\n"
        f"Terms to NEVER write: {ctx.solution_terms}\n\n"
        f"This is clue #{clue_index}. Target obliqueness: {obliqueness}.\n"
        + (
            f"PUZZLE PIECE {clue_index} of {PUZZLE_CLUES}: this clue is one piece "
            "of the puzzle, not the answer. Give ONE new constraint on the target "
            "— a single angle nobody could turn into the word by itself, but which "
            "a solver can CHECK against a candidate. No synonym lists, no "
            "explanations, no 'the word means…'. If an average reader gets the "
            "word on first read, it is too easy.\n"
            if clue_index <= PUZZLE_CLUES else ""
        )
        + (
            f"ANGLE FOR THIS PIECE (use THIS one, not another): {angle}\n"
            if angle else ""
        )
        + (
            "ALREADY SPENT on this word — do NOT repeat these angles: "
            + ", ".join(spent) + "\n"
            if spent else ""
        )
        + (
            "ENUMERABLE WORD(S) IN THIS NAME: "
            + ", ".join(ctx.enumerable_words)
            + ". These belong to a set a player can list out loud (kinship terms, "
            "colours, days, crypto jargon). NEVER let a clue gesture at the "
            "CATEGORY — the moment you say 'a family title' or 'a colour', ten "
            "candidates remain and the hunt is over. This exact mistake cost a "
            "whole hunt: 'a title that skips a generation' is just 'uncle' with "
            "extra steps. Attack these words ONLY by concrete anchor, cultural "
            "use, sound, structure, or their relation to the other word.\n"
            if clue_index <= PUZZLE_CLUES and ctx.enumerable_words else ""
        )
        + f"FACET for this clue: {vector} — {relic_guidance_for(vector, ctx)}\n"
        + (
            "NOTE: this facet was already hinted at in an earlier clue — give a "
            "COMPLETELY NEW, noticeably CLEARER angle on the same target (do not "
            "rephrase the earlier hint).\n"
            if _relic_is_second_visit(vector, clue_index, ctx) else ""
        )
        + f"Previous clues:\n{prior}\n\n"
        f"Write clue #{clue_index}."
    )


def _relic_is_second_visit(vector: str, clue_index: int, ctx: RelicClueContext) -> bool:
    """True when an earlier clue already targeted this facet (so the prompt asks
    for a new, clearer angle instead of a rephrase)."""
    return any(
        relic_slot_for(i, ctx)[0] == vector for i in range(1, clue_index)
    )


class RelicClueEngine(ClueEngine):
    """ClueEngine with the relic prompt/ramp.

    `next_clue` is overridden ONLY to try a TRAIL clue first (package 3b) when the
    policy allows it; the direct path — including the inherited guardrail loop,
    regeneration with feedback and anti-parroting — is untouched and is always the
    fallback, so a hunt can never stall on the trail machinery."""

    def __init__(self, anthropic_client, model: str, *, trail_verifier=None, trail_policy=None):
        super().__init__(anthropic_client, model)
        self._trail_verifier = trail_verifier
        self._trail_policy = trail_policy

    def next_clue(self, persona, clue_index, prior_clues, *, max_attempts: int = 4):
        """Trail first (verified + guardrail-clean), else the normal direct clue."""
        trail = self._try_trail(persona, clue_index, prior_clues)
        if trail is not None:
            return trail
        return super().next_clue(persona, clue_index, prior_clues, max_attempts=max_attempts)

    def _try_trail(self, persona, clue_index, prior_clues):
        """A verified trail clue, or None to fall back. Any failure — no verifier,
        policy off, generation error, unverified artifact, guardrail rejection —
        returns None. Never raises: the direct path must always remain available."""
        if self._trail_verifier is None or self._trail_policy is None:
            return None
        try:
            from .guardrails import check_clue
            from .relic_trail import generate_trail_clue

            if not self._trail_policy.allows(clue_index, angle_for(clue_index, persona)):
                return None
            draft = generate_trail_clue(
                self, persona, clue_index, prior_clues,
                verifier=self._trail_verifier, policy=self._trail_policy,
            )
            if draft is None or not draft.verified:
                return None
            # A verified trail still must not leak the name/answer.
            result = check_clue(
                draft.text, clue_index=clue_index,
                persona_display_name=persona.display_name,
                persona_handle=persona.handle, persona_bio=persona.bio,
                solution_terms=list(persona.solution_terms),
            )
            if not result.ok:
                return None
            return ClueDraft(text=draft.text, taunt=draft.taunt)
        except Exception:  # noqa: BLE001 — trails are a bonus, never a blocker
            return None

    def generate(self, persona, clue_index, prior_clues, *, feedback=None):
        obliqueness = relic_slot_for(clue_index, persona)[1]
        system = RELIC_SYSTEM_PROMPT.format(
            index=clue_index, obliqueness=obliqueness, hard_floor=HARD_CLUE_FLOOR
        )
        # The DIRECT path never writes an anchor clue: an artefact nobody checked
        # is the one failure worse than a clue that is too hard. Anchors only
        # reach players through the verified trail path.
        user = build_relic_user_message(
            persona, clue_index, prior_clues, allow_anchor=False
        )
        if feedback:
            user += "\n\n" + feedback
        resp = self._client.messages.create(
            model=self._model,
            max_tokens=512,
            system=system,
            messages=[{"role": "user", "content": user}],
        )
        text = "".join(b.text for b in resp.content if getattr(b, "type", "") == "text")
        return _parse_clue(text)
