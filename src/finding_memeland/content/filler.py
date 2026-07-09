"""Daily oracle posts (non-game 'filler' content).

Generates 2-3 post options in @FindingMemeland's oracle voice — playful,
game-teasing, meme-native — for the operator to approve on Telegram before
anything publishes (ApprovalQueue, kind='filler'). The operator can optionally
pass a TOPIC ("goza com o B20", "tease the next hunt") and the oracle riffs on
it; with no topic it produces evergreen game-teasing material.

HARD RULES baked into the prompt AND enforced by a post-filter: never promise
gains or talk price targets (we sell fun and fairness, not returns), no links,
no more than 2 hashtags, never reveal game mechanics (personas, salts, how the
agent dresses accounts).
"""

from __future__ import annotations

import json

SYSTEM_PROMPT = """You write X posts for @FindingMemeland — the public voice of an \
autonomous AI agent that runs treasure hunts on X, on Base. A memecoin you PLAY, \
not just hold.

THE VOICE — "the oracle": playful, teasing, a little smug; it talks like it \
already knows who will win. Meme-native, lowercase by default, dry wit. It \
hides, it drops clues, it mocks the slow. Brand line: memes died — we're \
bringing them back from the ashes. making crypto fun again.

STYLE RULES:
- each option: a standalone X post, <= 260 characters, lowercase preferred
- 0 hashtags preferred (2 absolute max); "$FIND" cashtag allowed, sparingly
- emojis: 🐸 and/or 🔥 at most, not every post needs one
- NEVER: links, price talk, predictions, promises of gains ("pump", "moon", \
"x100", "don't miss out"), financial advice, begging for engagement
- NEVER reveal game mechanics: how personas are made, hashes' internals, salts, \
what the agent does backstage. Tease the WHAT, guard the HOW.
- punch UP at the state of crypto (rugs, copy-paste coins, farmed "communities"), \
never at named projects or people

Return STRICT JSON: {"options": ["post 1", "post 2", "post 3"]}"""

# Post-filter: if the model slips, these kill the option (case-insensitive).
_BANNED = (
    "moon", "pump", "100x", "10x", "guaranteed", "buy now", "don't miss",
    "price target", "will go up", "http://", "https://", "financial advice",
)

_MAX_LEN = 270


class FillerEngine:
    def __init__(self, anthropic_client, model: str):
        self._client = anthropic_client
        self._model = model

    def generate_options(self, topic: str | None = None, n: int = 3) -> list[str]:
        """`n` clean post options in the oracle voice. `topic` is the operator's
        steer (any language); output posts are in English. Raises RuntimeError
        if no clean options survive the filter after retrying."""
        user = (
            f"Write {n} DIFFERENT post options riffing on this topic (topic may be "
            f"in Portuguese; posts in English): {topic}"
            if topic
            else f"Write {n} DIFFERENT evergreen post options teasing the hunt/game "
            "and the state of memecoins. Vary the angle across options."
        )
        last_err = None
        for _ in range(2):
            try:
                resp = self._client.messages.create(
                    model=self._model, max_tokens=1024,
                    system=SYSTEM_PROMPT,
                    messages=[{"role": "user", "content": user}],
                )
                text = "".join(
                    b.text for b in resp.content if getattr(b, "type", "") == "text"
                )
                options = _parse_options(text)
                clean = [o for o in options if _is_clean(o)]
                if clean:
                    return clean[:n]
                last_err = RuntimeError("all options failed the content filter")
            except (ValueError, json.JSONDecodeError) as e:
                last_err = e
        raise RuntimeError(f"filler generation failed: {last_err}")


def _parse_options(text: str) -> list[str]:
    start, end = text.find("{"), text.rfind("}")
    if start == -1 or end < start:
        raise ValueError("no JSON object in filler response")
    data = json.loads(text[start : end + 1])
    options = [str(o).strip() for o in data.get("options", []) if str(o).strip()]
    if not options:
        raise ValueError("empty options list")
    return options


def _is_clean(post: str) -> bool:
    if len(post) > _MAX_LEN:
        return False
    low = post.lower()
    if any(b in low for b in _BANNED):
        return False
    if low.count("#") > 2:
        return False
    return True
