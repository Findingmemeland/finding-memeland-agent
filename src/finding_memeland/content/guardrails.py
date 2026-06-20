"""Pre-publication guardrails (checklist step 24).

Because game posts publish with NO human approval, these automated checks are
the safety net before any clue goes out. They protect the integrity narrative:
a clue must never literally leak the persona's identity, and obliqueness rules
apply to early clues (feedback: clues 1-3 never reveal name/nationality/bio).
"""

from __future__ import annotations

import re
from dataclasses import dataclass


@dataclass(frozen=True)
class GuardrailResult:
    ok: bool
    reasons: list[str]


# Tokens that must never appear literally in any clue.
def _forbidden_tokens(display_name: str, handle: str, bio: str) -> set[str]:
    tokens: set[str] = set()
    for source in (display_name, handle, bio):
        for word in re.findall(r"[A-Za-z]{3,}", source or ""):
            tokens.add(word.lower())
    tokens.add(handle.lstrip("@").lower())
    return tokens


def check_clue(
    clue_text: str,
    *,
    clue_index: int,
    persona_display_name: str,
    persona_handle: str,
    persona_bio: str,
    max_len: int = 280,
    is_long_post: bool = False,
) -> GuardrailResult:
    reasons: list[str] = []
    text_lower = clue_text.lower()

    # 1. Never leak identity literally (all clues).
    leaked = sorted(
        tok
        for tok in _forbidden_tokens(persona_display_name, persona_handle, persona_bio)
        if re.search(rf"\b{re.escape(tok)}\b", text_lower)
    )
    if leaked:
        reasons.append(f"clue leaks persona identity tokens: {leaked}")

    # 2. No URLs in clues — $0.20 each on the X API, and a tell.
    if re.search(r"https?://|\bwww\.", text_lower):
        reasons.append("clue contains a URL (cost + leakage risk)")

    # 3. Length.
    limit = 25000 if is_long_post else max_len
    if len(clue_text) > limit:
        reasons.append(f"clue exceeds {limit} chars ({len(clue_text)})")

    # 4. Obliqueness for early clues (1-3): no @handle vector, no bare handle.
    if clue_index <= 3 and re.search(r"@\w+", clue_text):
        reasons.append("clues 1-3 must not reference an @handle (obliqueness rule)")

    return GuardrailResult(ok=not reasons, reasons=reasons)
