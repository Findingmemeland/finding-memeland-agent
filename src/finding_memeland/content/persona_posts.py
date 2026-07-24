"""Prep-window persona posts (P2 findability architecture).

During the T-24h window the dressed persona publishes 2-4 posts of its own.
Two jobs: (a) give X time and signals to INDEX the profile (the Hunt #2 lesson:
a fresh name is suppressed in search; a distinctive POST phrase is findable),
(b) be the ANCHOR posts the clues point players at.

Rules mirror findable_post: in-character, distinctive/searchable phrasing,
X-safe, and they must NEVER contain a solution term — they locate the account,
they don't solve the puzzle. Generated 100% by the agent (operator blindness:
game content is never approved by a human).
"""

from __future__ import annotations

import json

from ..social.x_text import sanitize_x_text

_MAX_ATTEMPTS = 3

SYSTEM_PROMPT = """You write X (Twitter) posts for a fictional persona in \
"Finding Memeland", an AI-run treasure hunt. The persona is a HIDDEN account \
players must identify by inference. These posts are published during a quiet \
warm-up window BEFORE the hunt starts.

Each post must:
- be fully in-character (voice and theme below);
- contain at least one DISTINCTIVE, easily-searchable phrase — unusual word \
combinations a later clue can point players to (search -> lands on this account);
- read like a normal niche account posting, NOT like a puzzle or announcement;
- NEVER contain the solution terms or state who the persona "really" is;
- no URLs, no hashtags, no @mentions, max ~240 characters each.

Respond with ONLY a JSON array of {n} strings, no prose."""


def _extract_posts(text: str, n: int) -> list[str]:
    start, end = text.find("["), text.rfind("]")
    if start == -1 or end == -1 or end < start:
        raise ValueError(f"no JSON array in response: {text[:200]!r}")
    items = json.loads(text[start : end + 1])
    if not isinstance(items, list):
        raise ValueError("response is not a list")
    return [str(x) for x in items][:n]


class PersonaPostEngine:
    """LLM-backed generator for the persona's prep posts."""

    def __init__(self, anthropic_client, model: str):
        self._client = anthropic_client
        self._model = model

    def generate(self, identity, n: int = 3) -> list[str]:
        """n in-character anchor posts, validated (X-safe, no solution terms,
        non-empty). Regenerates on validation failure, like the other engines."""
        user = (
            f"Persona voice: {identity.voice}\n"
            f"Persona bio: {identity.bio}\n"
            f"Theme (INTERNAL, never write it): {identity.backstory}\n"
            f"Terms to NEVER write: {identity.solution_terms}\n"
            f"The pinned post (do not repeat its phrasing): {identity.findable_post}\n\n"
            f"Write {n} posts."
        )
        last: Exception | None = None
        msg = user
        for _ in range(_MAX_ATTEMPTS):
            resp = self._client.messages.create(
                model=self._model,
                max_tokens=800,
                system=SYSTEM_PROMPT.format(n=n),
                messages=[{"role": "user", "content": msg}],
            )
            text = "".join(
                b.text for b in resp.content if getattr(b, "type", "") == "text"
            )
            try:
                return self._validate(_extract_posts(text, n), identity, n)
            except ValueError as e:
                last = e
                msg = user + f"\n\nYour previous attempt was REJECTED: {e}. Fix it."
        raise ValueError(f"prep posts failed after {_MAX_ATTEMPTS} attempts: {last}")

    @staticmethod
    def _validate(posts: list[str], identity, n: int) -> list[str]:
        clean: list[str] = []
        for p in posts:
            p = sanitize_x_text(p)[:240].strip()
            if not p:
                raise ValueError("empty post after sanitization")
            low = p.lower()
            leaked = [t for t in identity.solution_terms if t.lower() in low]
            if leaked:
                raise ValueError(f"post contains solution term(s) {leaked}")
            clean.append(p)
        if len(clean) < max(1, n - 1):  # tolerate the model returning n-1
            raise ValueError(f"expected ~{n} posts, got {len(clean)}")
        return clean
