"""Clue Engine — generates one clue at a time along a fixed difficulty ramp.

Ramp (Pedro, 2026-08-13 — replaces the two-round shuffled plan):
- Clues 1-3, in RANDOM order among themselves: name word 1 (HARD), name word 2
  (HARD), avatar (OBVIOUS — the photo matters least for search, so it gets ONE
  plain clue).
- Clues 4-5: name word 1 (EASY), name word 2 (EASY).
- Clues 6-7-8: the persona's POSTS, on a ladder — 6 harder, 7 clearer, 8
  easiest (locator post first; anchor posts join when they exist).
- Clue 9+: the @HANDLE, the last-resort locator, generated from the operator's
  handle_hint (pre-dressing descriptor), each one clearer than the last.
- Number of clues is NOT fixed — the ramp keeps going until someone wins.
- Clue 1 is special: it also carries the announcement + reshare gate + integrity
  hash (added by the orchestrator via templates.clue_one). The Clue Engine only
  produces the puzzle TEXT; templates wrap it.

Voice: the clues post on the MAIN @FindingMemeland account, so they use the game
master's playful, ironic, meme-native crypto-Twitter voice — NOT the persona's
own voice (that's for the persona's account). Cryptic but cheeky, never mystical.

Every generated clue is checked by guardrails before it can be returned, and
regenerated on failure — game posts publish with no human approval.
"""

from __future__ import annotations

import json
import random
import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

from .guardrails import check_clue

# Legacy easing (pre-ramp fallback): 1.0 easing by ~30% per clue.
EASING_FACTOR = 0.70
MIN_GAP_SECONDS = 60 * 60        # 1h
MAX_GAP_SECONDS = 3 * 60 * 60    # 3h

# Ramp obliqueness levels (Pedro, 2026-08-13). 1.0 = maximally subtle.
RAMP_NAME_HARD = 0.9    # the enigmatic first pass on each name word (clues 1-3)
RAMP_NAME_EASY = 0.4    # the obvious revisit (clues 4-5)
RAMP_AVATAR = 0.25      # the photo's ONE clue is plain-obvious (least searchable)
RAMP_POSTS = (0.5, 0.3, 0.15)   # the post ladder: 6 harder, 7 clearer, 8 easiest
RAMP_HANDLE_START = 0.1          # clue 9+: the handle, near-explicit
RAMP_HANDLE_FLOOR = 0.05
_RAMP_HANDLE_STEP = 0.01         # each extra handle clue gets slightly clearer
# Hard-clue calibration (Pedro, 2026-08-16, Cassandra dry run): at 0.9 the model
# still wrote synonym lists and story summaries. At or above this obliqueness a
# clue is ONE oblique angle — enforced in the system prompt and re-stated per call.
HARD_CLUE_FLOOR = 0.8


@dataclass
class PersonaContext:
    """The full identity the Clue Engine reasons over — including the secret
    backstory and the solution terms that must never appear in a clue."""
    display_name: str
    handle: str
    bio: str
    avatar_description: str
    voice: str
    backstory: str
    solution_terms: list[str] = field(default_factory=list)
    banner_description: str = ""
    findable_post: str = ""
    # The per-hunt ramp: (facet, obliqueness) pairs for the NAME+AVATAR phase
    # (head shuffled once per hunt; the easy revisits follow in fixed order).
    clue_facet_plan: list = field(default_factory=list)
    # The persona's own posts (anchors published at /dress time) — searchable
    # anchors the post-phase clues point players at.
    anchor_posts: list[str] = field(default_factory=list)
    # Operator's decomposition of the @ (pre-dressing descriptor) — feeds the
    # last-resort handle clues (9+). Internal, never published verbatim.
    handle_hint: str = ""

    @classmethod
    def from_generated(cls, generated, handle: str, *, handle_hint: str = "") -> "PersonaContext":
        """Build from a GeneratedPersona plus the account's actual @handle.
        The ramp's opening trio is shuffled per hunt (variety); everything
        after it is fixed by the ramp."""
        return cls(
            display_name=generated.display_name,
            handle=handle,
            bio=generated.bio,
            avatar_description=generated.avatar_prompt,
            voice=generated.voice,
            backstory=generated.backstory,
            solution_terms=list(generated.solution_terms),
            banner_description=getattr(generated, "banner_prompt", ""),
            findable_post=getattr(generated, "findable_post", ""),
            clue_facet_plan=ramp_plan(generated.display_name),
            handle_hint=handle_hint,
        )


@dataclass
class ClueDraft:
    text: str
    taunt: str | None = None    # None for clue 1; a jeer for clues 2+


def obliqueness_for(clue_index: int, persona: "PersonaContext | None" = None) -> float:
    """Target obliqueness for this clue. With a persona, it comes from the
    RAMP (difficulty is tied to the clue's ROLE, not its position — the avatar
    clue is obvious even though it lands in clues 1-3). Without a persona,
    the legacy exponential easing (kept for standalone callers)."""
    if persona is not None:
        return clue_slot_for(clue_index, persona)[1]
    return round(EASING_FACTOR ** (clue_index - 1), 3)


# Each clue targets one facet of the persona, cryptically signalled so players
# know whether to look at the name, the picture, the banner, or a pinned post.
# Progression: concept first → visual disambiguators → name → the searchable
# pinned post as the last-resort LOCATOR if the hunt drags on.
# Static facet guidance. Name words use dynamic per-word facets ("name_word_N")
# resolved by guidance_for(), so a name of ANY length gets a clue for EVERY word.
VECTOR_GUIDANCE = {
    "avatar": "the PROFILE PICTURE — describe a distinctive visual element of the "
    "avatar so players recognise the exact account among look-alikes. Signal "
    "UNMISTAKABLY that this clue is about the picture (it is the only "
    "picture clue of the hunt).",
    "banner": "the HEADER BANNER image — describe a distinctive visual element of "
    "the banner.",
    "bio": "the BIO — hint at the wording or attitude of the account's bio so "
    "players recognise it.",
    "signature_post": "the pinned LOCATOR POST — point players (cryptically, more "
    "directly as clues ease) toward the distinctive phrase in the pinned post, so a "
    "search lands them on the exact account.",
    "anchor_post": "one of the account's OWN POSTS — point players (cryptically, "
    "more directly as clues ease) toward a distinctive phrase from the post quoted "
    "in your context, so searching that phrase lands them on the exact account.",
    "handle": "the @HANDLE itself — the LAST-RESORT locator. Using the operator's "
    "handle hint in your context, hint at each PART of the @ (meaning, wordplay, "
    "definition) so a player who decodes the parts can type the exact handle into "
    "search. Signal clearly that this clue is about the @. Never write the handle "
    "or any of its parts.",
}


def _ordinal(n: int) -> str:
    if 10 <= n % 100 <= 20:
        return f"{n}th"
    return f"{n}{ {1: 'st', 2: 'nd', 3: 'rd'}.get(n % 10, 'th') }"


def _name_facets(display_name: str) -> list[str]:
    """One facet per word of the display name (so every word gets its own clue)."""
    words = display_name.split()
    if len(words) <= 1:
        return ["name_word_1"]
    return [f"name_word_{i + 1}" for i in range(len(words))]


def guidance_for(facet: str, persona: "PersonaContext") -> str:
    """Resolve a facet (including dynamic name-word facets) to its clue guidance."""
    if facet.startswith("name_word_"):
        n = int(facet.rsplit("_", 1)[1])
        words = persona.display_name.split()
        word = words[n - 1] if 0 < n <= len(words) else ""
        which = "the only word" if len(words) <= 1 else f"the {_ordinal(n)} word"
        return (
            f"{which} of the display NAME (the word '{word}') — hint at THAT EXACT "
            "word (its meaning, a synonym, or wordplay on it) so a player decoding "
            "the hint arrives at the literal word and can search it. Do NOT "
            "substitute a theme-related word. Never write the word itself. "
            # Cross-contamination rule (Hunt #5 post-mortem: name clues framed
            # with visual imagery read as PICTURE clues — 'Mirrored' vs a
            # mirrored avatar): a NAME clue must never describe the profile
            # picture or any visual element of the account.
            "CRITICAL: signal this is about a WORD of the NAME, and do NOT "
            "describe the profile picture or any visual element — a visually "
            "framed clue misreads as a picture clue."
        )
    return VECTOR_GUIDANCE[facet]


def clue_plan(persona: "PersonaContext") -> list:
    """Deterministic ORDERED ramp (fallback when no per-hunt plan was
    shuffled). Same structure as ramp_plan, without the shuffling."""
    words = _name_facets(persona.display_name)
    head = [(f, RAMP_NAME_HARD) for f in words] + [("avatar", RAMP_AVATAR)]
    tail = [(f, RAMP_NAME_EASY) for f in words]
    return [*head, *tail]


def ramp_plan(display_name: str) -> list:
    """The per-hunt NAME+AVATAR ramp (Pedro, 2026-08-13), as (facet,
    obliqueness) pairs:

    - HEAD, shuffled once per hunt: each name word HARD + the avatar's single
      OBVIOUS clue (the photo matters least for search, so exactly one clue,
      and a plain one). For a 2-word name these are clues 1-3, in random order
      — EXCEPT clue 1 is never the avatar (Opus, Fase 4 review): the hunt
      always opens on a hard, oblique name clue; the photo varies between
      positions 2 and 3.
    - TAIL, fixed order: each name word again, EASY (clues 4-5).

    After the plan runs out, clue_slot_for takes over with the POST ladder
    (3 clues, 6-7-8) and then the HANDLE phase (9+)."""
    words = _name_facets(display_name)
    head = [(f, RAMP_NAME_HARD) for f in words] + [("avatar", RAMP_AVATAR)]
    random.shuffle(head)
    if head[0][0] == "avatar":
        # Swap the avatar with a random name position — never the opener.
        j = random.randrange(1, len(head))
        head[0], head[j] = head[j], head[0]
    tail = [(f, RAMP_NAME_EASY) for f in words]
    return [*head, *tail]


def clue_slot_for(clue_index: int, persona: "PersonaContext") -> tuple:
    """(facet, obliqueness) for this clue.

    Phases (2-word name): 1-3 = shuffled head (names hard + avatar obvious);
    4-5 = names easy; 6-7-8 = the POST ladder (locator first, then the
    persona's own anchor posts when they exist, each step clearer); 9+ = the
    HANDLE, near-explicit and getting clearer, forever (a hunt with no winner
    keeps escalating the handle until the timeout voids it)."""
    plan = persona.clue_facet_plan or clue_plan(persona)
    n = len(plan)
    if clue_index <= n:
        return tuple(plan[clue_index - 1])
    post_slot = clue_index - n  # 1-based inside the post ladder
    if post_slot <= len(RAMP_POSTS):
        obl = RAMP_POSTS[post_slot - 1]
        if post_slot == 1 or not persona.anchor_posts:
            return ("signature_post", obl)
        return ("anchor_post", obl)
    step = clue_index - n - len(RAMP_POSTS) - 1
    return ("handle", round(max(RAMP_HANDLE_FLOOR, RAMP_HANDLE_START - _RAMP_HANDLE_STEP * step), 3))


def clue_vector_for(clue_index: int, persona: "PersonaContext") -> str:
    """Facet this clue targets (see clue_slot_for for the full ramp)."""
    return clue_slot_for(clue_index, persona)[0]


def post_phase_start(persona: "PersonaContext") -> int:
    """First clue index of the POST ladder — anchors stay hidden from the
    prompt before this (need-to-know: earlier clues can't leak them)."""
    plan = persona.clue_facet_plan or clue_plan(persona)
    return len(plan) + 1


def next_clue_due(now: datetime | None = None) -> datetime:
    now = now or datetime.now(timezone.utc)
    return now + timedelta(seconds=random.randint(MIN_GAP_SECONDS, MAX_GAP_SECONDS))


# A hunt has no clue limit, so "how long can it run?" has no hard answer. This
# is the planning assumption: the ramp reaches the near-explicit handle phase
# at clue 9 (2-word name), so hunts realistically end at or before clue 10.
ASSUMED_MAX_CLUES = 10


def worst_case_hunt_hours(max_gap_s: int, assumed_clues: int = ASSUMED_MAX_CLUES) -> float:
    """Longest a hunt can plausibly run, in hours."""
    return assumed_clues * max_gap_s / 3600


def holding_window_covers_hunt(
    holding_hours: int, max_gap_s: int, assumed_clues: int = ASSUMED_MAX_CLUES
) -> bool:
    """THE invariant behind the public rule "hold before the first clue".

    The eligibility window looks BACK from the CLAIM, not from clue 1. So if a
    hunt can outlast holding_hours, someone can buy mid-hunt, wait out the
    window, claim — and win, contradicting the rule we announced. Since it's all
    on-chain, anyone can check and catch us. Guard it everywhere.
    """
    return holding_hours > worst_case_hunt_hours(max_gap_s, assumed_clues)


def next_clue_due_factory(min_gap_s: int, max_gap_s: int):
    """Build the clue-cadence function the Orchestrator calls between clues.

    THE single source of truth for cadence: production (main.py) and the
    pre-flight script both go through here, so what gets verified before a
    launch is literally what runs during the hunt — not a re-implementation.

    The gap is drawn fresh per clue: a fixed interval would let players set an
    alarm, which kills the surprise the game runs on.
    """
    if min_gap_s <= 0 or max_gap_s <= 0:
        raise ValueError(f"clue gaps must be positive (got {min_gap_s}, {max_gap_s})")
    if min_gap_s > max_gap_s:
        raise ValueError(
            f"CLUE_MIN_GAP_S ({min_gap_s}) > CLUE_MAX_GAP_S ({max_gap_s}) — "
            "random.randint would raise mid-hunt, after the treasure is already hidden"
        )

    def _due(now: datetime | None = None) -> datetime:
        now = now or datetime.now(timezone.utc)
        return now + timedelta(seconds=random.randint(min_gap_s, max_gap_s))

    return _due


SYSTEM_PROMPT = """You are the game master of "Finding Memeland", writing CLUES \
for the current treasure hunt, posted on the main @FindingMemeland account. There \
is a HIDDEN persona ACCOUNT on X. Players WIN by FINDING that account, reading the \
claim code in its bio, and posting that code as a REPLY to the Clue 1 post \
(claim-by-post). There are NO DMs in this game — never tell players to DM anyone \
or mention DMs at all.

Your clues must point at the persona's REAL, OBSERVABLE attributes — the words of \
its display name, its profile picture, its distinctive posts, and (as the very \
last resort) its @handle — so a player can LOCATE and RECOGNISE the exact \
account. A player must be able to ACT on each clue (search a name, recognise an \
image, search a phrase, type an @). Do NOT make them guess an abstract idea.

The persona is themed around a concept/figure (the 'theme' below) ONLY for \
coherence and flavour — never make players guess the theme; make them FIND the \
account by its real attributes.

Voice: playful, ironic, meme-native crypto Twitter. Community language, cheeky, \
lowercase is fine, the occasional emoji. NOT mystical or poetic. A smug oracle \
enjoying the struggle.

Hard rules for the clue text:
- One short post, max ~200 characters. Standalone clue text only.
- NEVER write verbatim: the display name or any of its words, the @handle, the \
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
easy revisit. The test is RECOGNITION vs INFERENCE: if the clue DESCRIBES the thing and \
the reader merely recognises it, that is a lookup — too easy, rewrite; a hard \
clue gives a lateral angle the reader has to JUMP to. Two valid flavours — vary \
between them: crypto-native lateral ("she'd have called every crash. nobody \
would've aped. name's been shorthand for being right and ignored ever since."; \
for a mood word: "the exact mood after warning everyone for the tenth time and \
watching them scroll past. one word.") or closed and dry ("she told them and they \
laughed."; "the original 'i told you so' — now just a girl's name."). Too easy: \
"she saw everything coming and got zero credit — a girl with a gift nobody \
wanted, cursed ever since" (three facts = a summary); "bone-weary, running on \
empty, done with this timeline" (a synonym list).
- Each clue must add a NEW angle, roughly 30% clearer than the previous one. Do \
not repeat earlier clues.
- NEVER build a clue on counting: do not state how many syllables, letters, \
characters, vowels or consonants anything has — you miscount them, and a wrong \
count makes players eliminate the RIGHT answer. The only number you may state \
about the name is its word count, and only if you are certain it is exact. \
Prefer qualitative hints (meaning, imagery, etymology, rhythm) over any counting.
- FACET TARGETING: each clue focuses on ONE real attribute (given below) and must \
CRYPTICALLY signal which one — so players know whether to look at the name, the \
profile picture, the banner, the bio, or the pinned post. Signal it indirectly \
(e.g. "a picture's worth a thousand...", "check what hangs above their head"), \
naming the facet outright only when obviousness is high (obliqueness 0.4 or lower).

For clue #1 only, set taunt to "". For clue #2 and later, also write a short, \
varying jeer that pokes fun at players for not solving it yet (e.g. "c'mon you \
lazy degens, money's on the line"). If the jeer mentions how many clues are out, \
the number is exactly {index} — this one included. Clue counts and jeering belong \
in the taunt ONLY: the clue text never opens with "N clues in…" or any jab — it \
is the puzzle and nothing else. Never call a clue "final" or "last": the ramp \
may continue past it.

Respond with ONLY a JSON object: {{"clue": "...", "taunt": "..."}}"""


def _is_second_visit(vector: str, clue_index: int, persona: PersonaContext) -> bool:
    """True when this facet already had a clue earlier in the ramp — the easy
    revisit (clues 4-5) must be clearer, not a rephrase. Post/handle phases:
    every clue after the phase's first is a revisit too."""
    plan = persona.clue_facet_plan or clue_plan(persona)
    earlier = [f for f, _ in plan[: min(clue_index - 1, len(plan))]]
    if vector in ("signature_post", "anchor_post", "handle"):
        first_post = post_phase_start(persona)
        if vector == "handle":
            return clue_index > first_post + len(RAMP_POSTS)
        return clue_index > first_post
    return vector in earlier


def _build_user_message(persona: PersonaContext, clue_index: int, prior_clues: list[str]) -> str:
    prior = "\n".join(f"- {c}" for c in prior_clues) if prior_clues else "(none — this is the first clue)"
    vector, obliqueness = clue_slot_for(clue_index, persona)
    return (
        "The account's REAL attributes (point clues AT these; never write them verbatim):\n"
        f"- display name: {persona.display_name}\n"
        f"- @handle (never write): {persona.handle}\n"
        f"- bio: {persona.bio}\n"
        f"- avatar (profile picture): {persona.avatar_description}\n"
        f"- banner (header image): {persona.banner_description}\n"
        f"- pinned locator post: {persona.findable_post}\n"
        + (
            # The anchor posts are lethal search vectors: the generator only
            # sees them once the post ladder begins, so earlier clues cannot
            # leak them even by accident (need-to-know).
            "- the account's own posts (anchors for post-phase clues):\n"
            + "".join(f"    * {p}\n" for p in persona.anchor_posts)
            if persona.anchor_posts and clue_index >= post_phase_start(persona) else ""
        )
        + (
            # Same need-to-know for the operator's handle decomposition: it
            # only enters the prompt in the handle phase itself.
            f"- handle hint (operator's decomposition of the @, INTERNAL): "
            + (persona.handle_hint or "(none — derive oblique hints from the handle itself)")
            + "\n"
            if vector == "handle" else ""
        )
        + "\n"
        f"Theme (FLAVOUR ONLY — do NOT make players guess this, do not write it): "
        f"{persona.backstory}\n"
        f"Terms to NEVER write: {persona.solution_terms}\n\n"
        f"This is clue #{clue_index}. Target obliqueness: {obliqueness}.\n"
        + (
            "HARD CLUE: one oblique angle only — no synonym lists, no story "
            "summary, no etymology/meaning, exactly ONE identifying fact. If an "
            "average reader gets it on first read, it is too easy.\n"
            if obliqueness >= HARD_CLUE_FLOOR else ""
        )
        + f"FACET for this clue: {vector} — {guidance_for(vector, persona)}\n"
        + (
            "NOTE: this facet was already hinted at in an earlier clue — give a "
            "COMPLETELY NEW, noticeably CLEARER angle on the same target (do not "
            "rephrase the earlier hint).\n"
            if _is_second_visit(vector, clue_index, persona) else ""
        )
        + f"Previous clues:\n{prior}\n\n"
        f"Write clue #{clue_index}."
    )


def hint_terms(handle_hint: str) -> list[str]:
    """The handle PARTS named in the operator's hint ('expresso = ...; tit =
    ...; go = ...' -> ['expresso', 'tit']) — radioactive words no clue may
    ever contain literally. Only the left-hand side of each '=' segment; parts
    under 3 chars are skipped (same policy as the guardrail tokens: banning
    2-letter English words like 'go' would strangle normal clue writing)."""
    terms: list[str] = []
    for seg in str(handle_hint or "").split(";"):
        part = seg.split("=", 1)[0].strip().lower()
        if len(part) >= 3 and re.fullmatch(r"[a-z0-9]+", part):
            terms.append(part)
    return terms


# Deterministic clean-up of two model habits the prompt only mostly fixes
# (Hunt 6 dry runs, 16/08): opening the CLUE text with a clue count / jab
# ("five clues deep and you still need help?") — that belongs in the taunt —
# and calling a clue "final"/"last" when the ramp may continue past it.
_NUM = r"(?:\d+|one|two|three|four|five|six|seven|eight|nine|ten|eleven|twelve)"
# Up to four short lead-in words ("ok", "still here after") may precede the
# count; the count must be PLURAL "clues" so "one clue away from glory" (a real
# clue opening) is never touched.
_LEADING_COUNT_RE = re.compile(
    rf"^\s*(?:[a-z']+[,.]?\s+){{0,4}}?{_NUM}\s+clues\b[^.?!\n]*[.?!]\s*",
    re.IGNORECASE,
)
_LEADING_FINAL_RE = re.compile(
    r"^\s*(?:ok(?:ay)?[,.]?\s*|fine[,.]?\s*)?"
    r"(?:the\s+)?(?:final|last)\s+(?:clue|one|resort|answer)(?:\s+time)?\s*[:.,!—-]+\s*",
    re.IGNORECASE,
)


def _strip_leading_meta(clue: str) -> str:
    """Drop a leading clue-count sentence and/or a leading 'final clue:' tag.
    Never empties the clue: if the whole text was the preamble, keep it as is."""
    out = clue
    for rx in (_LEADING_COUNT_RE, _LEADING_FINAL_RE):
        m = rx.match(out)
        if m and m.end() < len(out):
            out = out[m.end():]
    return out.strip() or clue


def _parse_clue(text: str) -> ClueDraft:
    start, end = text.find("{"), text.rfind("}")
    if start == -1 or end == -1 or end < start:
        raise ValueError(f"no JSON object in clue response: {text[:200]!r}")
    data = json.loads(text[start : end + 1])
    clue = _strip_leading_meta(str(data.get("clue", "")).strip())
    taunt = str(data.get("taunt", "")).strip()
    if not clue:
        raise ValueError("empty clue text")
    return ClueDraft(text=clue, taunt=taunt or None)


class ClueEngine:
    """Wraps the Anthropic SDK. Generates the next clue aware of prior clues,
    and validates it against the guardrails before returning."""

    def __init__(self, anthropic_client, model: str):
        self._client = anthropic_client
        self._model = model

    def generate(
        self,
        persona: PersonaContext,
        clue_index: int,
        prior_clues: list[str],
        *,
        feedback: str | None = None,
    ) -> ClueDraft:
        """One LLM call -> a clue (and a taunt for clues 2+). Not yet validated.
        `feedback` carries the previous attempt's guardrail rejection so the
        model knows exactly what to avoid on regeneration."""
        system = SYSTEM_PROMPT.format(
            index=clue_index, obliqueness=obliqueness_for(clue_index, persona),
            hard_floor=HARD_CLUE_FLOOR,
        )
        user = _build_user_message(persona, clue_index, prior_clues)
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

    def _guardrail_kwargs(self, persona: PersonaContext, clue_index: int) -> dict:
        """Extra `check_clue` switches for this hunt type. The persona engine has
        none; the relic engine turns on the puzzle-phase rules (no emoji, no
        rhyme) for clues 1..PUZZLE_CLUES."""
        return {}

    def _post_guardrail_reasons(
        self, draft: ClueDraft, persona: PersonaContext, clue_index: int,
        prior_clues: list[str],
    ) -> list[str]:
        """Rejection reasons that need more than a regex — run only after the
        text checks pass. Empty list = accepted. Overridden by the relic engine
        (blind solver, Hunt #7 post-mortem)."""
        return []

    def next_clue(
        self,
        persona: PersonaContext,
        clue_index: int,
        prior_clues: list[str],
        *,
        max_attempts: int = 4,
    ) -> ClueDraft:
        """Generate a guardrail-clean clue, regenerating on failure.

        Raises RuntimeError if no clean clue is produced within max_attempts —
        the orchestrator should pause and alert rather than post a bad clue.
        """
        last_reasons: list[str] = []
        feedback: str | None = None
        for _ in range(max_attempts):
            draft = self.generate(persona, clue_index, prior_clues, feedback=feedback)
            result = check_clue(
                draft.text,
                clue_index=clue_index,
                persona_display_name=persona.display_name,
                persona_handle=persona.handle,
                persona_bio=persona.bio,
                # Hint parts join the ban list for EVERY clue (not just the
                # handle phase): the parts the operator named are radioactive
                # anywhere ('tit' isn't visible in the camelCase split, so the
                # guardrail alone can't see it).
                solution_terms=[*persona.solution_terms, *hint_terms(persona.handle_hint)],
                **self._guardrail_kwargs(persona, clue_index),
            )
            reasons = list(result.reasons)
            if not reasons:
                # Text-level checks passed; a subclass may still reject on
                # what the clue DOES (the relic engine runs a blind solver).
                reasons = self._post_guardrail_reasons(
                    draft, persona, clue_index, prior_clues
                )
            if not reasons:
                return draft
            last_reasons = reasons
            feedback = (
                "Your previous attempt was REJECTED by the guardrails: "
                + "; ".join(reasons)
                + f". It was: {draft.text!r}. Write a COMPLETELY NEW clue that "
                "never contains the flagged word(s) themselves — point at them "
                "purely by inference (imagery, etymology, association, wordplay). "
                "The player must deduce the word; you must never write it."
            )
        # As razões CITAM as palavras sinalizadas — ou seja, a resposta da hunt.
        # Esta excepção sobe até ao Telegram do operador, portanto o texto fica
        # nos logs e a mensagem não o repete (auditoria v3, P0-C). Vale para
        # personas e para relics: em ambos os casos a palavra sinalizada é o que
        # o jogador tem de descobrir.
        import logging

        logging.getLogger(__name__).error(
            "clue #%s failed guardrails after %s attempts: %s",
            clue_index, max_attempts, last_reasons,
        )
        raise RuntimeError(
            f"clue #{clue_index} failed guardrails after {max_attempts} attempts "
            f"({len(last_reasons)} razões — omitidas aqui porque nomeiam a "
            "resposta; estão nos logs)"
        )

    def generate_taunt(self) -> str:
        """Standalone jeer (fallback / manual use). Normally the taunt comes back
        with the clue from generate(). Cheap curated pick, no LLM call."""
        return random.choice(_TAUNTS)


_TAUNTS = (
    "c'mon you lazy degens, money's on the line",
    "i thought you guys were supposed to be clever",
    "still nothing? embarrassing, frankly",
    "the prize is just sitting here. anyway",
    "tick tock. someone's about to beat you to it",
)
