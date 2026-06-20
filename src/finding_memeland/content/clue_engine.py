"""Clue Engine — generates one clue at a time with an easing curve.

Design (memory: design decisions, 2026-05-23):
- Number of clues is NOT fixed. Drop progressively more obvious clues until won.
- Cadence between clues: random 1h-3h.
- Aggressive easing: each clue ~30% more obvious than the last.
- Clues 1-3 stay oblique (identify by inference, never direct lookup).
  Clues 4+ may become structurally direct, but never name the answer.
- Clue 1 is special: announcement + reshare gate + integrity hash.

Each generated clue is checked by guardrails before publishing.
"""

from __future__ import annotations

import random
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

# Easing: obliqueness starts at 1.0 and multiplies by ~0.7 per clue (~30% easier).
EASING_FACTOR = 0.70
MIN_GAP_SECONDS = 60 * 60        # 1h
MAX_GAP_SECONDS = 3 * 60 * 60    # 3h


@dataclass
class PersonaContext:
    """What the LLM may reason over to build clues — full identity."""
    display_name: str
    handle: str
    bio: str
    avatar_description: str
    voice: str
    backstory: str


def obliqueness_for(clue_index: int) -> float:
    """1.0 (max oblique) easing down. clue_index is 1-based."""
    return round(EASING_FACTOR ** (clue_index - 1), 3)


def next_clue_due(now: datetime | None = None) -> datetime:
    now = now or datetime.now(timezone.utc)
    return now + timedelta(seconds=random.randint(MIN_GAP_SECONDS, MAX_GAP_SECONDS))


class ClueEngine:
    """Wraps the Anthropic SDK. Generates the next clue aware of prior clues."""

    def __init__(self, anthropic_client, model: str):
        self._client = anthropic_client
        self._model = model

    def generate_clue(
        self,
        persona: PersonaContext,
        clue_index: int,
        prior_clues: list[str],
    ) -> str:
        """Return clue text only (templating happens in templates.py).

        TODO(step 24): build the prompt from VOICE_SPEC + obliqueness target +
        prior clues, call self._client.messages.create(...), return text.
        Must respect: clues 1-3 oblique (no name/nationality/bio lookup),
        clues 4+ may be structural, never literal answer. Generate, then the
        orchestrator runs guardrails.check_clue() and regenerates on failure.
        """
        raise NotImplementedError("clue generation — implemented in step 24")

    def generate_taunt(self) -> str:
        """A short varying jeer for clues 2+ (e.g. 'c'mon you lazy bastards').

        TODO(step 24): single cheap LLM call or sampled from a seed list.
        """
        raise NotImplementedError("taunt generation — implemented in step 24")
