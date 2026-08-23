"""Trail clues — the multi-angle clue type (Pedro's design, 22/08).

THE IDEA: a name clue doesn't have to describe the word. It can send players on a
TRAIL through the real world — a findable X post, a news article, an archive —
from which the name is INFERRED. Pedro's examples: a post where someone named a
project sharing the persona's first name; "in March 1999 a climber summited
Everest, a newspaper covered it… but the name isn't the climber's, it's the one
who stayed home waiting". Every hunt can use a different research surface, so the
game never feels the same twice — and the clues become shareable content.

THE DANGER (Fable's caveat, and it is the real one): if the model INVENTS the
post or the article, the trail leads nowhere and the hunt becomes UNSOLVABLE —
the worst possible outcome. So this module is built around three hard safeguards:

1. VERIFY BEFORE PUBLISH — the model must declare the real artifact it points to
   plus the search terms that reach it; a verifier checks the artifact EXISTS.
   Unverified never publishes.
2. FALLBACK TO DIRECT — if verification keeps failing, the clue silently becomes
   an ordinary direct clue about the same target. A hunt never stalls on this.
3. EARLY ONLY — trails may occupy the opening (hard) clues; from `max_clue_index`
   onward every clue is direct on the name/image, so the ramp always converges on
   something searchable regardless of what the trails did.

Verification defaults to DENY: any error, any ambiguity, any missing field = not
verified. A boring direct clue is always better than a broken hunt.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Protocol, runtime_checkable


@dataclass(frozen=True)
class TrailPolicy:
    """When trails are allowed. Defaults are deliberately conservative."""

    enabled: bool = True
    max_clue_index: int = 3      # trails only in the opening clues; 4+ always direct
    max_attempts: int = 2        # regeneration tries before falling back to direct

    def allows(self, clue_index: int) -> bool:
        return self.enabled and clue_index <= self.max_clue_index


@dataclass
class TrailDraft:
    """A trail clue plus the machine-checkable claims that justify it."""

    text: str
    taunt: str | None
    artifact: str            # the REAL thing it points at (post/article/record)
    search_terms: list[str] = field(default_factory=list)  # what a player would search
    verified: bool = False


@runtime_checkable
class TrailVerifier(Protocol):
    """Does `artifact` actually exist and is it reachable by `search_terms`?

    Implementations MUST default to False on any error — an unverifiable trail is
    treated exactly like a false one."""

    def verify(self, artifact: str, search_terms: list[str]) -> bool: ...


class AlwaysDenyVerifier:
    """The safe default when no verifier is wired: every trail falls back to a
    direct clue. Used so a misconfiguration degrades to 'boring but working'."""

    def verify(self, artifact: str, search_terms: list[str]) -> bool:
        return False


TRAIL_INSTRUCTIONS = """
TRAIL MODE for this clue. Instead of describing the target word directly, send \
players on a RESEARCH TRAIL through something REAL and FINDABLE — a public post, \
a news article, a record, an archive page — from which the target can be inferred.

Rules that make a trail fair:
- The artifact must REALLY EXIST and be findable by an ordinary web/X search. If \
you are not CERTAIN it exists, do not use it — write a normal clue instead.
- The trail must END at the target word by inference (the target may be one step \
sideways from the obvious answer — e.g. not the person in the story but someone \
connected to them). Say enough that a determined player can get there.
- NEVER name the target, and never write the search terms as an instruction list \
— the clue is still a clue, not a recipe.
- Public figures and public posts/articles only. No private individuals.

Return these EXTRA fields alongside clue and taunt:
- "artifact": one plain sentence naming the real thing you point at (e.g. "a 2019 \
tweet by @X about Y", "a Público article from March 1999 about the Everest \
ascent"). Be specific enough to be checked.
- "search_terms": a JSON array of 2-4 search strings a player would realistically \
type to reach that artifact.
"""


def _parse_trail(text: str) -> TrailDraft:
    """Parse the model's JSON. Missing/blank artifact or search terms = invalid,
    which the caller treats as 'not verified' (fallback)."""
    start, end = text.find("{"), text.rfind("}")
    if start == -1 or end == -1 or end < start:
        raise ValueError(f"no JSON object in trail response: {text[:200]!r}")
    data = json.loads(text[start : end + 1])
    clue = str(data.get("clue") or "").strip()
    if not clue:
        raise ValueError("trail response has no clue text")
    terms = data.get("search_terms") or []
    if not isinstance(terms, list):
        terms = []
    taunt = data.get("taunt")
    return TrailDraft(
        text=clue,
        taunt=(str(taunt).strip() if taunt else None),
        artifact=str(data.get("artifact") or "").strip(),
        search_terms=[str(t).strip() for t in terms if str(t).strip()],
    )


class WebSearchTrailVerifier:
    """Verifies an artifact with an LLM that HAS web search.

    The model is asked a closed question and must answer with a single token. We
    accept ONLY an explicit VERIFIED; anything else (UNVERIFIED, prose, an
    exception, an empty response) is False. `search_fn` is injected so tests run
    offline and so the caller decides which client/tooling does the searching."""

    def __init__(self, search_fn):
        self._search = search_fn  # callable(prompt: str) -> str

    def verify(self, artifact: str, search_terms: list[str]) -> bool:
        if not artifact or not search_terms:
            return False
        prompt = (
            "You are fact-checking a puzzle clue. Using web search, decide whether "
            "this artifact REALLY EXISTS and is findable:\n\n"
            f"ARTIFACT: {artifact}\n"
            f"SEARCH TERMS A PLAYER WOULD USE: {search_terms}\n\n"
            "Answer with exactly one word: VERIFIED if you found concrete evidence "
            "it exists and is reachable by those searches, or UNVERIFIED if you did "
            "not, or if you are unsure. Do not explain."
        )
        try:
            answer = (self._search(prompt) or "").strip().upper()
        except Exception:  # noqa: BLE001 — unverifiable == not verified
            return False
        return answer.startswith("VERIFIED")


def generate_trail_clue(
    engine,
    ctx,
    clue_index: int,
    prior_clues: list[str],
    *,
    verifier: TrailVerifier,
    policy: TrailPolicy = TrailPolicy(),
) -> TrailDraft | None:
    """Try to produce a VERIFIED trail clue for this slot.

    Returns the draft only when the artifact was verified; returns None when
    trails aren't allowed here, when generation/parsing fails, or when
    verification fails within the attempt budget — the caller then falls back to
    an ordinary direct clue."""
    if not policy.allows(clue_index):
        return None

    from .relic_clues import RELIC_SYSTEM_PROMPT, build_relic_user_message, relic_slot_for
    from .clue_engine import HARD_CLUE_FLOOR

    obliqueness = relic_slot_for(clue_index, ctx)[1]
    system = RELIC_SYSTEM_PROMPT.format(
        index=clue_index, obliqueness=obliqueness, hard_floor=HARD_CLUE_FLOOR
    ) + "\n" + TRAIL_INSTRUCTIONS

    feedback = ""
    for _ in range(max(1, policy.max_attempts)):
        user = build_relic_user_message(ctx, clue_index, prior_clues) + feedback
        try:
            resp = engine._client.messages.create(
                model=engine._model, max_tokens=700, system=system,
                messages=[{"role": "user", "content": user}],
            )
            text = "".join(b.text for b in resp.content if getattr(b, "type", "") == "text")
            draft = _parse_trail(text)
        except Exception:  # noqa: BLE001 — bad JSON/API hiccup: try again, then fall back
            feedback = "\n\nYour previous response was unusable. Return valid JSON."
            continue

        if verifier.verify(draft.artifact, draft.search_terms):
            return TrailDraft(
                text=draft.text, taunt=draft.taunt, artifact=draft.artifact,
                search_terms=draft.search_terms, verified=True,
            )
        feedback = (
            "\n\nThe artifact you pointed at could NOT be verified as real. Pick a "
            "DIFFERENT artifact you are certain exists (a well-known public post, "
            "a documented event, a published article) — or the clue will be "
            "discarded."
        )
    return None
