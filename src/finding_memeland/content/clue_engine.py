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

    @classmethod
    def from_generated(cls, generated, handle: str) -> "PersonaContext":
        """Build from a GeneratedPersona plus the account's actual @handle."""
        return cls(
            display_name=generated.display_name,
            handle=handle,
            bio=generated.bio,
            avatar_description=generated.avatar_prompt,
            voice=generated.voice,
            backstory=generated.backstory,
            solution_terms=list(generated.solution_terms),
        )


@dataclass
class ClueDraft:
    text: str
    taunt: str | None = None    # None for clue 1; a jeer for clues 2+


def obliqueness_for(clue_index: int) -> float:
    """1.0 (max oblique) easing down. clue_index is 1-based."""
    return round(EASING_FACTOR ** (clue_index - 1), 3)


def next_clue_due(now: datetime | None = None) -> datetime:
    now = now or datetime.now(timezone.utc)
    return now + timedelta(seconds=random.randint(MIN_GAP_SECONDS, MAX_GAP_SECONDS))


SYSTEM_PROMPT = """You are the game master of "Finding Memeland", writing CLUES \
for the current treasure hunt. The clues are posted on the main @FindingMemeland \
account and point players toward a HIDDEN persona account whose true identity is \
given to you. Players must identify it by INFERENCE — combining at least two \
vectors (a paradox, a structural quirk, name + avatar, etc.) — never by a direct \
name lookup.

Voice: playful, ironic, meme-native crypto Twitter. Community language, cheeky, \
lowercase is fine, the occasional emoji. NOT mystical or poetic. Think a smug \
oracle who is enjoying watching people struggle.

Hard rules for the clue text:
- One short post, max ~200 characters. Standalone puzzle text only.
- NEVER include: the solution terms (the literal answer), the persona's display \
name, its @handle, any URL, or hashtags.
- Obliqueness by progression. You are writing clue #{index}; target obliqueness \
{obliqueness} (1.0 = maximally oblique; lower = more obvious). Clues 1-3 must stay \
oblique — no name, nationality, or biographical fact that solves it by direct \
lookup. From clue 4 onward you may be structurally direct, but STILL never write \
the literal answer.
- Each clue must add a NEW angle, roughly 30% more obvious than the previous one. \
Do not repeat earlier clues.

For clue #1 only, set taunt to "". For clue #2 and later, also write a short, \
varying jeer that pokes fun at players for not solving it yet (e.g. "c'mon you \
lazy degens, money's on the line").

Respond with ONLY a JSON object: {{"clue": "...", "taunt": "..."}}"""


def _build_user_message(persona: PersonaContext, clue_index: int, prior_clues: list[str]) -> str:
    prior = "\n".join(f"- {c}" for c in prior_clues) if prior_clues else "(none — this is the first clue)"
    return (
        "SECRET — do NOT reveal any of this literally:\n"
        f"- true identity / backstory: {persona.backstory}\n"
        f"- solution terms to NEVER write: {persona.solution_terms}\n"
        f"- persona display name: {persona.display_name}\n"
        f"- persona @handle: {persona.handle}\n"
        f"- persona bio: {persona.bio}\n"
        f"- avatar: {persona.avatar_description}\n\n"
        f"This is clue #{clue_index}. Target obliqueness: {obliqueness_for(clue_index)}.\n"
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
        self, persona: PersonaContext, clue_index: int, prior_clues: list[str]
    ) -> ClueDraft:
        """One LLM call -> a clue (and a taunt for clues 2+). Not yet validated."""
        system = SYSTEM_PROMPT.format(
            index=clue_index, obliqueness=obliqueness_for(clue_index)
        )
        resp = self._client.messages.create(
            model=self._model,
            max_tokens=512,
            system=system,
            messages=[{"role": "user", "content": _build_user_message(persona, clue_index, prior_clues)}],
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
        for _ in range(max_attempts):
            draft = self.generate(persona, clue_index, prior_clues)
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
