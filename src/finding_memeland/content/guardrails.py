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


# Counting claims (post-mortem P2a: clue 2 said "five syllables" for Penelope,
# which has four — anyone counting eliminated the RIGHT answer). LLMs miscount
# sub-word units systematically (tokenization), so:
#   - syllables/letters/characters/vowels/consonants: BANNED outright — we
#     cannot verify syllables programmatically either (that's exactly why the
#     model gets them wrong).
#   - words: VERIFIED — a word count is checkable against the display name, so
#     it is allowed only when it is exactly right.
_NUM_WORDS = {
    "one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6,
    "seven": 7, "eight": 8, "nine": 9, "ten": 10, "eleven": 11, "twelve": 12,
}
_UNVERIFIABLE_COUNT = re.compile(
    rf"\b(\d+|{'|'.join(_NUM_WORDS)})[\s-]*(syllable|letter|character|vowel|consonant)s?\b",
    re.IGNORECASE,
)
_WORD_COUNT_CLAIM = re.compile(
    rf"\b(\d+|{'|'.join(_NUM_WORDS)})[\s-]*words?\b", re.IGNORECASE
)


def _claimed_number(token: str) -> int:
    t = token.lower()
    return int(t) if t.isdigit() else _NUM_WORDS[t]


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

    # 1c. Counting claims. Unverifiable units are banned; word counts must be
    # exactly right (checked against the display name).
    m = _UNVERIFIABLE_COUNT.search(clue_text)
    if m:
        reasons.append(
            f"clue asserts a count of {m.group(2)}s ('{m.group(0)}') — counting "
            "claims are banned (models miscount; a wrong count eliminates the "
            "right answer). Hint qualitatively instead."
        )
    for m in _WORD_COUNT_CLAIM.finditer(clue_text):
        actual = len((persona_display_name or "").split())
        if _claimed_number(m.group(1)) != actual:
            reasons.append(
                f"clue claims '{m.group(0)}' but the display name has "
                f"{actual} word(s) — wrong counts poison the puzzle."
            )

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
