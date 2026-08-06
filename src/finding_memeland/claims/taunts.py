"""Oracle taunts — public replies to wrong guesses and game-funny chatter.

ARCHITECTURE RULE (Pedro, 2026-07-25): the reply engine can NEVER leak a clue,
by construction, not by promise — it receives NO clue/solution/persona content.
Its only inputs are the player's own post text and a BANNED-TERMS list used
exclusively for hard output validation (solution terms, persona name tokens,
the claim code). "Give me a hint" gets a deflecting jeer, never information —
the engine literally has nothing to give. New clues only ever go out as public
clue posts, for everyone, on the normal cadence.

Costs: replies are $0.015 each and NEVER carry URLs ($0.20 each). The
orchestrator enforces the rate caps (1 taunt per profile, guess cap per
account); this module makes each individual reply safe and varied.
"""

from __future__ import annotations

import re

from ..social.x_text import sanitize_x_text

_MAX_LEN = 240

# Static pool — the guaranteed-safe fallback (and the whole engine when no LLM
# is wired). Voice: playful crypto-Twitter oracle; jeering, never informative.
TAUNT_POOL: tuple[str, ...] = (
    "not even close. the pond is deep and you're splashing at the edge. 🐸",
    "wrong code. the persona remains unbothered.",
    "that's a code alright. just not mine.",
    "swing and a miss. the clues are right there.",
    "nope. somewhere on X, someone who doesn't exist is laughing at you.",
    "cold. colder than the bottom of the pond.",
    "the oracle has reviewed your submission: no.",
    "confident. wrong, but confident. i respect it.",
    "that code opens nothing. keep digging.",
    "close only counts in horseshoes, not treasure hunts.",
    "denied. the frog abides. 🐸",
    "you typed that with such hope. anyway — no.",
)

_URL_RE = re.compile(r"https?://|\bwww\.", re.IGNORECASE)
_MENTION_RE = re.compile(r"@\w+")

_VARIATION_SYSTEM = """You are the voice of "Finding Memeland", an AI oracle \
running a treasure hunt on X. A player just replied with a WRONG guess. Write \
ONE short public jeer (max 200 chars): playful, meme-native crypto Twitter, \
ironic, never mean-spirited, never informative. You know NOTHING about the \
hunt's answer — never pretend to hint, never say what the answer is like, \
never confirm or deny how close they are thematically. No URLs, no hashtags, \
no @mentions, no emojis except 🐸 (optional). Reply with ONLY the jeer text."""

_FUNNY_SYSTEM = """You judge replies in an X treasure-hunt thread. Given a \
player's reply (NOT a code guess), answer YES if the game's oracle should \
reply with a playful jeer. YES for anything ABOUT the game: complaints that \
it's hard or impossible, begging for hints or mercy, taunting or challenging \
the oracle, jokes about the hunt, declaring defeat, wrong-theory banter. NO \
for: greetings, generic spam, shilling or links, scam offers, serious \
questions that need a real answer, anything unrelated to the game, or \
genuine (non-playful) hostility. When unsure whether it is about the game, \
say NO. Reply with ONLY YES or NO."""


class TauntEngine:
    """LLM-varied taunts with a hard-validated static fallback."""

    def __init__(self, anthropic_client=None, model: str = ""):
        self._client = anthropic_client
        self._model = model
        self._n = 0  # rotation counter for the static pool

    # -- public API ------------------------------------------------------
    def taunt(self, player_text: str, banned_terms: tuple[str, ...]) -> str:
        """A safe public jeer for a wrong guess. Always returns something:
        LLM variation when available and valid, static pool otherwise."""
        if self._client is not None:
            try:
                raw = self._complete(_VARIATION_SYSTEM, player_text[:400])
                cand = self._validate(raw, banned_terms)
                if cand:
                    return cand
            except Exception:  # noqa: BLE001 — variation is a nicety, never a blocker
                pass
        return self._from_pool(banned_terms)

    def should_taunt_chatter(self, player_text: str) -> bool:
        """Is this non-code reply funny enough (game-wise) to deserve a jeer?
        LLM-judged, strict, and fail-CLOSED: any error or no LLM => False —
        'Good morning' must never get a reply."""
        if self._client is None:
            return False
        try:
            raw = self._complete(_FUNNY_SYSTEM, player_text[:400])
            return raw.strip().upper().startswith("YES")
        except Exception:  # noqa: BLE001
            return False

    # -- internals -------------------------------------------------------
    def _complete(self, system: str, user: str) -> str:
        resp = self._client.messages.create(
            model=self._model, max_tokens=120, system=system,
            messages=[{"role": "user", "content": user}],
        )
        return "".join(
            b.text for b in resp.content if getattr(b, "type", "") == "text"
        )

    def _from_pool(self, banned_terms: tuple[str, ...]) -> str:
        """Deterministic rotation over the pool, skipping any entry that a
        (paranoid) banned-terms hit invalidates."""
        for i in range(len(TAUNT_POOL)):
            cand = TAUNT_POOL[(self._n + i) % len(TAUNT_POOL)]
            if self._validate(cand, banned_terms):
                self._n += i + 1
                return cand
        self._n += 1
        return TAUNT_POOL[0]  # pool is static and clue-free by construction

    @staticmethod
    def _validate(text: str, banned_terms: tuple[str, ...]) -> str | None:
        """Hard gate for ANY outgoing taunt: X-safe, short, no URLs (cost), no
        @mentions, and none of the banned terms (solution terms, persona name
        tokens, claim code). Returns the cleaned text, or None if unsafe."""
        cand = sanitize_x_text(text or "").strip()[:_MAX_LEN]
        if not cand:
            return None
        if _URL_RE.search(cand) or _MENTION_RE.search(cand):
            return None
        low = cand.lower()
        for term in banned_terms:
            t = str(term).strip().lower()
            if t and t in low:
                return None
        return cand
