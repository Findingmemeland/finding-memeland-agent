"""Clue Engine — generates one clue at a time with an easing curve.

Design (memory: design decisions, 2026-05-23):
- Number of clues is NOT fixed. Drop progressively more obvious clues until won.
- Cadence between clues: random 1h-3h.
- Aggressive easing: each clue ~30% more obvious than the last.
- Clues 1-3 stay oblique (identify by inference, never direct lookup).
  Clues 4+ may become structurally direct, but never name the answer.
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
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

from .guardrails import check_clue

# Easing: obliqueness starts at 1.0 and multiplies by ~0.7 per clue (~30% easier).
EASING_FACTOR = 0.70
MIN_GAP_SECONDS = 60 * 60        # 1h
MAX_GAP_SECONDS = 3 * 60 * 60    # 3h


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
    clue_facet_plan: list[str] = field(default_factory=list)  # shuffled once per hunt

    @classmethod
    def from_generated(cls, generated, handle: str) -> "PersonaContext":
        """Build from a GeneratedPersona plus the account's actual @handle.
        The facet plan is shuffled per hunt (variety), with signature_post forced
        last (the locator anchor must be the final escalation)."""
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
            clue_facet_plan=shuffled_facet_plan(generated.display_name),
        )


@dataclass
class ClueDraft:
    text: str
    taunt: str | None = None    # None for clue 1; a jeer for clues 2+


def obliqueness_for(clue_index: int) -> float:
    """1.0 (max oblique) easing down. clue_index is 1-based."""
    return round(EASING_FACTOR ** (clue_index - 1), 3)


# Each clue targets one facet of the persona, cryptically signalled so players
# know whether to look at the name, the picture, the banner, or a pinned post.
# Progression: concept first → visual disambiguators → name → the searchable
# pinned post as the last-resort LOCATOR if the hunt drags on.
# Static facet guidance. Name words use dynamic per-word facets ("name_word_N")
# resolved by guidance_for(), so a name of ANY length gets a clue for EVERY word.
VECTOR_GUIDANCE = {
    "avatar": "the PROFILE PICTURE — describe a distinctive visual element of the "
    "avatar so players recognise the exact account among look-alikes.",
    "banner": "the HEADER BANNER image — describe a distinctive visual element of "
    "the banner.",
    "bio": "the BIO — hint at the wording or attitude of the account's bio so "
    "players recognise it.",
    "signature_post": "the pinned LOCATOR POST — point players (cryptically, more "
    "directly as clues ease) toward the distinctive phrase in the pinned post, so a "
    "search lands them on the exact account.",
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
            "substitute a theme-related word. Never write the word itself."
        )
    return VECTOR_GUIDANCE[facet]


def clue_plan(persona: "PersonaContext") -> list[str]:
    """Deterministic ORDERED facet template (fallback when no per-hunt plan was
    shuffled). Ends on the locator post."""
    return [*_name_facets(persona.display_name), "avatar", "banner", "bio", "signature_post"]


def shuffled_facet_plan(display_name: str) -> list[str]:
    """Per-hunt plan: a facet per name word + avatar/banner/bio in RANDOM order,
    with signature_post forced last (the searchable locator is always last)."""
    facets = [*_name_facets(display_name), "avatar", "banner", "bio"]
    random.shuffle(facets)
    return [*facets, "signature_post"]


def clue_vector_for(clue_index: int, persona: "PersonaContext") -> str:
    """Facet this clue targets. Uses the persona's per-hunt shuffled plan if set,
    else the ordered template. Beyond the plan it stays on 'signature_post' — the
    longer a hunt runs, the more the clues point at the searchable post."""
    plan = persona.clue_facet_plan or clue_plan(persona)
    return plan[min(clue_index - 1, len(plan) - 1)]


def next_clue_due(now: datetime | None = None) -> datetime:
    now = now or datetime.now(timezone.utc)
    return now + timedelta(seconds=random.randint(MIN_GAP_SECONDS, MAX_GAP_SECONDS))


SYSTEM_PROMPT = """You are the game master of "Finding Memeland", writing CLUES \
for the current treasure hunt, posted on the main @FindingMemeland account. There \
is a HIDDEN persona ACCOUNT on X. Players WIN by FINDING that account, reading the \
claim code in its bio, and DMing it.

Your clues must point at the persona's REAL, OBSERVABLE attributes — the words of \
its display name, its profile picture, its banner image, its bio, and its \
distinctive pinned post — so a player can LOCATE and RECOGNISE the exact account. \
A player must be able to ACT on each clue (search a name, recognise an image, \
search a phrase). Do NOT make them guess an abstract idea.

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
- Obliqueness by progression. You are writing clue #{index}; target obliqueness \
{obliqueness} (1.0 = maximally subtle; lower = clearer). Early clues are subtle, \
later clues clearer — but never just write the name.
- Each clue must add a NEW angle, roughly 30% clearer than the previous one. Do \
not repeat earlier clues.
- FACET TARGETING: each clue focuses on ONE real attribute (given below) and must \
CRYPTICALLY signal which one — so players know whether to look at the name, the \
profile picture, the banner, the bio, or the pinned post. Signal it indirectly \
(e.g. "a picture's worth a thousand...", "check what hangs above their head"), \
naming the facet outright only when obviousness is high (clue 5+).

For clue #1 only, set taunt to "". For clue #2 and later, also write a short, \
varying jeer that pokes fun at players for not solving it yet (e.g. "c'mon you \
lazy degens, money's on the line").

Respond with ONLY a JSON object: {{"clue": "...", "taunt": "..."}}"""


def _build_user_message(persona: PersonaContext, clue_index: int, prior_clues: list[str]) -> str:
    prior = "\n".join(f"- {c}" for c in prior_clues) if prior_clues else "(none — this is the first clue)"
    vector = clue_vector_for(clue_index, persona)
    return (
        "The account's REAL attributes (point clues AT these; never write them verbatim):\n"
        f"- display name: {persona.display_name}\n"
        f"- @handle (never write): {persona.handle}\n"
        f"- bio: {persona.bio}\n"
        f"- avatar (profile picture): {persona.avatar_description}\n"
        f"- banner (header image): {persona.banner_description}\n"
        f"- pinned locator post: {persona.findable_post}\n\n"
        f"Theme (FLAVOUR ONLY — do NOT make players guess this, do not write it): "
        f"{persona.backstory}\n"
        f"Terms to NEVER write: {persona.solution_terms}\n\n"
        f"This is clue #{clue_index}. Target obliqueness: {obliqueness_for(clue_index)}.\n"
        f"FACET for this clue: {vector} — {guidance_for(vector, persona)}\n"
        f"Previous clues:\n{prior}\n\n"
        f"Write clue #{clue_index}."
    )


def _parse_clue(text: str) -> ClueDraft:
    start, end = text.find("{"), text.rfind("}")
    if start == -1 or end == -1 or end < start:
        raise ValueError(f"no JSON object in clue response: {text[:200]!r}")
    data = json.loads(text[start : end + 1])
    clue = str(data.get("clue", "")).strip()
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
            index=clue_index, obliqueness=obliqueness_for(clue_index)
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
                solution_terms=persona.solution_terms,
            )
            if result.ok:
                return draft
            last_reasons = result.reasons
            feedback = (
                "Your previous attempt was REJECTED by the guardrails: "
                + "; ".join(result.reasons)
                + f". It was: {draft.text!r}. Write a COMPLETELY NEW clue that "
                "never contains the flagged word(s) themselves — point at them "
                "purely by inference (imagery, etymology, association, wordplay). "
                "The player must deduce the word; you must never write it."
            )
        raise RuntimeError(
            f"clue #{clue_index} failed guardrails after {max_attempts} attempts: {last_reasons}"
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
