"""Pre-publication guardrails (checklist step 24).

Because game posts publish with NO human approval, these automated checks are
the safety net before any clue goes out. They protect the integrity narrative:
a clue must never literally leak the persona's identity, and obliqueness rules
apply to early clues (feedback: clues 1-3 never reveal name/nationality/bio).
"""

from __future__ import annotations

import re
import unicodedata
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
# Sound hints (Hunt #7: "rhymes with 'singing'", "rhymes with 'blimp'"). A rhyme
# fixes the word's ending; with any second fact the word falls. Puzzle phase only.
_RHYME_HINT = re.compile(
    r"\b(rhym\w*|sounds?\s+like|sound\s+alone|homophone\w*|pronounc\w*|"
    r"say\s+it\s+(out\s+)?loud|out\s+loud|syllable\w*)\b",
    re.IGNORECASE,
)
_WORD_COUNT_CLAIM = re.compile(
    rf"\b(\d+|{'|'.join(_NUM_WORDS)})[\s-]*words?\b", re.IGNORECASE
)


def _claimed_number(token: str) -> int:
    t = token.lower()
    return int(t) if t.isdigit() else _NUM_WORDS[t]


# Emoji (Hunt #7 post-mortem, 27/08). A pista 2 dizia "rhymes with 'blimp'… 🦐"
# e passou: o guardrail compara TEXTO, e o emoji é a resposta em pictograma. O
# Unicode dá-nos o nome de cada símbolo de graça — 🦐 chama-se "SHRIMP" — por
# isso expandimos cada emoji para o seu nome e verificamos esse nome como se
# fosse texto. Não é perfeito (🎒 chama-se "SCHOOL SATCHEL", não "bags"), e é
# por isso que na fase de puzzle os emojis são PROIBIDOS de todo (`puzzle_phase`);
# a leitura fica como rede para a fase de revelação.
_EMOJI_JOINERS = {"‍", "︎", "️"}     # ZWJ + variation selectors


def _is_emoji(ch: str) -> bool:
    if ch in _EMOJI_JOINERS or ch.isascii():
        return False
    if ord(ch) >= 0x1F000:                     # emoji blocks proper
        return True
    return unicodedata.category(ch) in {"So", "Sk"}   # ☀ ♥ ⚡ and friends


def emoji_names(text: str) -> list[tuple[str, str]]:
    """[(emoji, unicode name lower-cased)] for every emoji in `text`.
    Skin-tone and gender modifiers are dropped; unknown symbols get ''."""
    out = []
    for ch in text:
        if not _is_emoji(ch):
            continue
        name = unicodedata.name(ch, "").lower()
        if name.startswith("emoji modifier"):
            continue
        out.append((ch, name))
    return out


# Distinctive tokens that must never appear literally in any clue.
# NOTE: we deliberately do NOT block generic bio words — the bio is public and
# oblique by design, and blocking common words ("never", "first") strangles clue
# writing. The real leak risks are the persona's name/handle and the answer
# (solution_terms, handled separately).
def _forbidden_tokens(display_name: str, handle: str) -> set[str]:
    tokens: set[str] = set()
    for source in (display_name, handle):
        # Split camelCase/digit boundaries FIRST (Fase 4 pre-deploy fix): a
        # handle like @ExpressoTitgo must ban 'expresso' and 'titgo' as words,
        # not just the whole 'expressotitgo' — sub-tokens are exactly what a
        # handle clue could leak.
        spaced = re.sub(r"(?<=[a-z0-9])(?=[A-Z])|(?<=[A-Za-z])(?=\d)|(?<=\d)(?=[A-Za-z])", " ", source or "")
        for word in re.findall(r"[A-Za-z]{3,}", spaced):
            w = word.lower()
            if w not in STOPWORDS:
                tokens.add(w)
    h = (handle or "").lstrip("@").lower()
    if h:
        tokens.add(h)
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
    puzzle_phase: bool = False,
) -> GuardrailResult:
    """`puzzle_phase=True` (relic hunts, clues 1..PUZZLE_CLUES) switches on the
    hard-clue rules: no emoji at all, no rhyme / sound-alike hints. Both were
    measured on Hunt #7 (27/08): the emoji drew the answer and the rhyme turned
    the riddle into a crossword lookup."""
    reasons: list[str] = []
    text_lower = clue_text.lower()
    found_emoji = emoji_names(clue_text)
    # The emoji names are scanned like text for EVERY leak check below: an
    # emoji that depicts the answer is the answer.
    emoji_text = " ".join(name for _, name in found_emoji)
    scan_lower = f"{text_lower} {emoji_text}" if emoji_text else text_lower

    # 1. Never leak identity literally (all clues): persona name/handle tokens.
    leaked = sorted(
        tok
        for tok in _forbidden_tokens(persona_display_name, persona_handle)
        if re.search(rf"\b{re.escape(tok)}\b", scan_lower)
    )
    if leaked:
        reasons.append(f"clue leaks persona identity tokens: {leaked}")

    # 1b. Never write the literal answer (solution terms) — in ANY clue.
    answer_leaks = sorted(
        term
        for term in solution_terms
        if term.strip() and term.strip().lower() in scan_lower
    )
    if answer_leaks:
        reasons.append(f"clue contains solution term(s): {answer_leaks}")

    # 1c. Root variants: a term's stem is as good as the term itself to a
    # player ("severe" gives away "Severus"). Only long, distinctive terms are
    # checked, and only on a 5+ char shared prefix, so ordinary words don't trip.
    root_leaks = sorted(
        f"{term}~{word}"
        for term in solution_terms
        if term.strip() and len(term.strip()) >= 6
        for word in set(re.findall(r"[a-z]{5,}", scan_lower))
        if word != term.strip().lower()
        and word[:5] == term.strip().lower()[:5]
    )
    if root_leaks:
        reasons.append(
            f"clue leaks a ROOT VARIANT of a solution term: {root_leaks} — the "
            "stem gives the answer away; point at it by inference instead"
        )

    # 1d. An emoji that DEPICTS the answer — named explicitly so the feedback
    # to the model says "the emoji", not just "the word" (it wrote no word).
    watch = {t.strip().lower() for t in solution_terms if t.strip()}
    watch |= _forbidden_tokens(persona_display_name, persona_handle)
    emoji_leaks = sorted(
        f"{ch} ({name})" for ch, name in found_emoji
        if any(w and (w in name.split() or (len(w) >= 5 and w[:5] in name)) for w in watch)
    )
    if emoji_leaks:
        reasons.append(
            f"an emoji draws the answer: {emoji_leaks} — the guardrail reads "
            "emoji by their Unicode names; do not depict the word either"
        )

    # 1e. Puzzle-phase rules (relic hunts, clues 1..7 — Hunt #7 post-mortem).
    if puzzle_phase:
        if found_emoji:
            reasons.append(
                "puzzle-phase clue contains emoji "
                f"{[ch for ch, _ in found_emoji]} — NO emoji at all in the "
                "puzzle phase (an emoji is a picture of the answer)"
            )
        m = _RHYME_HINT.search(clue_text)
        if m:
            reasons.append(
                f"puzzle-phase clue hints at the SOUND of the word ('{m.group(0)}') "
                "— rhymes and sound-alikes are a crossword lookup; they belong to "
                "the reveal phase only"
            )

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
