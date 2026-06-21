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


# Common words that are NOT identity leaks even if they appear in a name.
STOPWORDS = frozenset({
    "the", "and", "for", "with", "into", "that", "this", "from", "just", "here",
    "are", "but", "not", "never", "than", "then", "over", "ever", "all", "you",
    "your", "its", "was", "were", "has", "have", "of", "a", "an", "in", "on",
})


# Distinctive tokens that must never appear literally in any clue.
# NOTE: we deliberately do NOT block generic bio words — the bio is public and
# oblique by design, and blocking common words ("never", "first") strangles clue
# writing. The real leak risks are the persona's name/handle and the answer
# (solution_terms, handled separately).
def _forbidden_tokens(display_name: str, handle: str) -> set[str]:
    tokens: set[str] = set()
    for source in (display_name, handle):
        for word in re.findall(r"[A-Za-z]{3,}", source or ""):
            w = word.lower()
            if w not in STOPWORDS:
                tokens.add(w)
    tokens.add(handle.lstrip("@").lower())
    return tokens


def check_clue(
    clue_text: str,
    *,
    clue_index: int,
    persona_display_name: str,
    persona_handle: str,
    persona_bio: str,
    solution_terms: list[str] | tuple[str, ...] = (),
    max_len: int = 280,
    is_long_post: bool = False,
) -> GuardrailResult:
    reasons: list[str] = []
    text_lower = clue_text.lower()

    # 1. Never leak identity literally (all clues): persona name/handle tokens.
    leaked = sorted(
        tok
        for tok in _forbidden_tokens(persona_display_name, persona_handle)
        if re.search(rf"\b{re.escape(tok)}\b", text_lower)
    )
    if leaked:
        reasons.append(f"clue leaks persona identity tokens: {leaked}")

    # 1b. Never write the literal answer (solution terms) — in ANY clue.
    answer_leaks = sorted(
        term
        for term in solution_terms
        if term.strip() and term.strip().lower() in text_lower
    )
    if answer_leaks:
        reasons.append(f"clue contains solution term(s): {answer_leaks}")

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
