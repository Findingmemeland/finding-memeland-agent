"""Target clue engine — Option A clues for an EXISTING third-party NFT.

EVOLUTION of the relic engine, not a rewrite: `TargetClueEngine` subclasses
`RelicClueEngine` and inherits the whole machinery that survived ten hunts —
the seeded puzzle ramp (7 hard pieces on name words + artwork, then the
reveal phase easing towards the name), the angle deck with its no-collision
rules, the guardrail loop with regeneration-on-feedback, the trail path and
the blind solver. What is target-specific lives here, and only this:

1. THE CONTEXT — built from a sealed Target: the BASE name (serial stripped;
   2+ words, not exactly 2), the artwork described by a vision model, and
   the on-chain description as flavour. The chain, contract and token id
   NEVER enter the context the writer sees: they are not attributes to hint
   at, they are the answer's address.

2. THE SYSTEM PROMPT — the target is somebody else's NFT, somewhere onchain.
   The old prompt said "a 1/1 minted on Base" with "a claim code in its
   description": both false now, and the first is a leak (decision: clues
   never state the chain — the chain is part of the answer).

3. FIVE GUARDS in `_post_guardrail_reasons`, before the solver — four
   mechanical, one LLM (the consistency judge: clue + answer, must be true;
   the blind solver's inverted twin, see TRUTH_JUDGE_SYSTEM):
   · NO LETTERS ON CLUES 1-3 (early_letter_errors): no declared claim and
     no talk about the letters, the spelling or a count of the name before
     clue 4 (Pedro, 16/10, after Hunt #22 opened on a letter fact).
   · WORD NUMBERING (word_position_errors): a word number the prose states
     must exist in the one reading, and a clue about word k calls it k
     (Pedro, 24/11, after Hunt #40 counted an "&" in one clue only).
     In the declaration check, two more from the same day (Hunt #41):
     every EXAMPLE word of a letter clue is declared and tested on its own
     spelling (example_errors), and every clue about a HYPHENATED word
     says so plainly and declares it (hyphen_errors).
   · STRUCTURAL CLAIMS (structural_claim_errors): anything the clue asserts
     about the name's letters is tested against the string; what cannot
     be tested (compound/fused/portmanteau/hidden word) is refused. Born
     from a false clue in Hunt #9 that repeated in the 09/09 live test.
   · NAME-OF-A-CHAIN-OR-PLATFORM: the clue must not contain ethereum/base/
     polygon/solana/…, nor foundation/superrare/opensea/…, as words. A
     platform name collapses the candidate set to one search box; a chain
     name is the address. Regex, word-boundary, case-insensitive — a prompt
     instruction is how emoji were "banned" and appeared anyway.
   · THE SEARCH GUARD (search_guard.ClueSearchGuard), puzzle phase only:
     the clue text is used as a marketplace query on the target's chain;
     if the target surfaces, the piece IS a search and is rejected. The
     guard runs its own canary first; an UNVERIFIABLE guard (blind canary,
     transport dead) raises immediately — fail-closed on every clue, not
     just clue 1: a leaked piece is forever, a delayed one is a non-event.

The ARTWORK description is produced once, at prepare time, by an injected
vision callable — and the image bytes are fetched through the same batched,
decoy-padded generic path as the live check (hunt.LiveCheck), never by a
lone request that tells a gateway which token we care about.
"""

from __future__ import annotations

import json
import logging
import random
import re
import time
from dataclasses import dataclass
from typing import Callable, Sequence

from ..content.clue_engine import HARD_CLUE_FLOOR, MalformedAnswer
from ..content.name_words import (
    FUNCTION_WORDS,
    answer_terms,
    name_skeleton,
    name_words,
    word_at,
)
from ..content.relic_clues import (
    PUZZLE_ANGLES,
    PUZZLE_PHASE_RULES,
    REVEAL_PHASE_RULES,
    STRUCTURE_EARLIEST,
    RelicClueContext,
    RelicClueEngine,
    ClueGuardUnavailable,
    angle_for_unverifiable,
    enumerable_words_in,
    puzzle_clues_of,
    relic_guidance_for,
    relic_slot_for,
    spent_angles,
    target_ramp_plan,
)
from .name_language import language_hint_errors, language_rule_for
from .search_guard import ClueSearchGuard
from .selector import ARTIST_KEYS, Target

# --------------------------------------------------------------------------- #
# Forbidden words — the address of the answer, never in a clue                  #
# --------------------------------------------------------------------------- #

CHAIN_WORDS = (
    "ethereum", "eth", "mainnet", "base", "polygon", "matic", "arbitrum",
    "optimism", "zora", "solana", "sol", "avalanche", "avax", "bnb", "bsc",
    "l1", "l2", "layer2", "layer-2", "rollup", "sidechain", "evm",
)
PLATFORM_WORDS = (
    "foundation", "superrare", "super rare", "makersplace", "makers place",
    "manifold", "opensea", "rarible", "knownorigin", "known origin",
    "async art", "asyncart", "blur", "looksrare", "looks rare", "magic eden",
    "objkt", "artblocks", "art blocks", "nifty gateway", "niftygateway",
)
_FORBIDDEN_RE = re.compile(
    r"\b(" + "|".join(re.escape(w) for w in CHAIN_WORDS + PLATFORM_WORDS) + r")\b",
    re.IGNORECASE,
)


def forbidden_address_words(text: str) -> list[str]:
    """Chain or platform names present in the clue text, as typed."""
    return sorted({m.group(1).lower() for m in _FORBIDDEN_RE.finditer(text or "")})


# --------------------------------------------------------------------------- #
# Structural-claim guard (Opus, live test 09/09 — the Hunt #9 finding repeated) #
# --------------------------------------------------------------------------- #
#
# The generator invents a structural property the word does not have
# ("word two of the name is a compound — two complete words fused"; the
# word was FUTURE). A player who obeys the clue walks away from the answer.
# Every claim a clue makes ABOUT THE STRING is tested against the string:
# letter counts, first/last letter, vowel/consonant, double letters, word
# count, palindrome, "contains the word X". Claims the guard CANNOT test —
# compound / fused / portmanteau / hidden word without naming it — are
# refused outright: an unverifiable structural claim was exactly the false
# one. Deterministic, like the emoji guard.

_NUM_WORDS = {"one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6,
              "seven": 7, "eight": 8, "nine": 9, "ten": 10, "eleven": 11,
              "twelve": 12, "a single": 1, "single": 1}
_ORD_WORDS = {"first": 1, "1st": 1, "second": 2, "2nd": 2, "third": 3, "3rd": 3,
              "fourth": 4, "4th": 4, "last": -1, "final": -1, "opening": 1,
              "closing": -1}
_NUM_RE = r"(?P<n>\d+|one|two|three|four|five|six|seven|eight|nine|ten|eleven|twelve)"
_WORD_REF_RE = re.compile(
    r"\b(?:word\s+(?P<wn>one|two|three|four|\d+)|"
    r"the\s+(?P<ord>first|second|third|fourth|last|final|opening|closing|1st|2nd|3rd|4th)"
    r"(?=\s+(?:word|half|part|name|one|has|is|starts|begins|ends|carries|contains|holds|"
    r"opens|closes|hides|runs)\b)|"
    r"(?P<whole>the\s+(?:whole\s+|full\s+)?name|both\s+words))\b",
    re.IGNORECASE)
_UNVERIFIABLE_RE = re.compile(
    r"\b((?<!hyphenated\s)compound|portmanteau|fused|welded|two\s+(?:complete\s+|whole\s+)?words\s+"
    r"(?:in\s+one|joined|stitched|glued|fused|merged)|hides?\s+(?:a|another)\s+word|"
    r"a\s+word\s+(?:hiding|hidden|inside)|find\s+(?:that|the)\s+seam|"
    r"(?:hidden|smaller|shorter|secret|buried|nested)\s+(?:\w+\s+){0,2}word\b|"
    r"\bword\s+(?:inside|within|nested|buried|tucked)\b|\b(?:prefix|suffix)\b|"
    r"anagram|rhymes?\s+with|(?:the\s+)?same\s+letters\s+as|acronym|initials?|"
    r"homophone|sounds\s+like|spelled\s+backwards?|reversed?\s+spells|"
    r"(?:the\s+)?only\s+(?:one|letter|vowel|consonant)\b(?:\s+(?:doing|that|of|in))?)\b",
    re.IGNORECASE)
_NO_VOWELS_RE = re.compile(r"\b(?:no|without|zero)\s+vowels?\b", re.IGNORECASE)
_LETTERS_RE = re.compile(_NUM_RE + r"[\s-]+letters?\b", re.IGNORECASE)
_WORDS_RE = re.compile(_NUM_RE + r"[\s-]+words?\b", re.IGNORECASE)
_LETTER_CLAIM = (        # "a vowel" | "a consonant" | "the letter F" | "'f'" | "F"
    r"(?:(?:an?\s+)?(?P<what>[Vv]owel|[Cc]onsonant)"
    r"|the\s+letter\s+['\u2018\u2019\"\u201c\u201d]?(?P<letter>[A-Za-z])['\u2018\u2019\"\u201c\u201d]?"
    r"|['\u2018\u2019\"\u201c\u201d](?P<qletter>[A-Za-z])['\u2018\u2019\"\u201c\u201d]"
    r"|(?P<uletter>[A-Z]))(?![A-Za-z])")
_STARTS_RE = re.compile(r"\b(?:[Ss]tarts|[Bb]egins|[Oo]pens)\s+with\s+" + _LETTER_CLAIM)
_ENDS_RE = re.compile(r"\b(?:[Ee]nds|[Cc]loses|[Ff]inishes)\s+(?:with|in|on)\s+" + _LETTER_CLAIM)
_FIRST_LETTER_RE = re.compile(
    r"\b(?P<pos>[Ff]irst|[Ll]ast)\s+letter\s+(?:is\s+|:\s*)?" + _LETTER_CLAIM)
_DOUBLE_RE = re.compile(r"\b(?:double|doubled|repeated|twin)\s+(?:letter|consonant|vowel)s?\b",
                        re.IGNORECASE)
_PALINDROME_RE = re.compile(r"\bpalindrom", re.IGNORECASE)
_CONTAINS_RE = re.compile(
    r"\b(?:contains|holds|hides|carries|conceals)\s+(?:the\s+word\s+)?"
    r"['\u2018\u2019\"\u201c\u201d](?P<w>[A-Za-z]{2,})['\u2018\u2019\"\u201c\u201d]",
    re.IGNORECASE)
# A letter compared with another word's (hunt #41): "the same last letter",
# "shares its first letter with", "ends like 'adventurer'". The last form
# needs the quote: "ends like a sigh" is about meaning, not letters.
_COMPARE_RE = re.compile(
    r"\bsame\s+(?:(?:first|last|opening|closing|final|starting|ending)\s+)?letter\b"
    r"|\bshares?\s+(?:its|a|the|their)\s+(?:first|last|opening|closing|final)\s+letter\b"
    r"|\b(?:starts?|begins?|opens?|ends?|closes?|finish(?:es)?)\s+(?:just\s+|exactly\s+)?"
    r"(?:like|as|the\s+(?:same\s+)?way\s+(?:as\s+)?)\s*['\u2018\u2019\"\u201c\u201d]",
    re.IGNORECASE)
# A word in quotes \u2014 an example, in a letter clue. Never an apostrophe inside
# a word ("don't", "players'"), never a lone letter ('r' is a letter claim).
_QUOTED_WORD_RE = re.compile(
    r"(?<![A-Za-z\u00c0-\u00ff])['\u2018\u2019\"\u201c\u201d]"
    r"(?P<w>[A-Za-z\u00c0-\u00ff]{2,}(?:[-'\u2019][A-Za-z\u00c0-\u00ff]+)*)"
    r"['\u2018\u2019\"\u201c\u201d](?![A-Za-z\u00c0-\u00ff])")
# "hyphenated", "a hyphen" — the one structural fact due on EVERY clue
# about a hyphenated word, the first three included (Pedro, 24/11, #41)
_HYPHEN_PROSE_RE = re.compile(r"\bhyphen(?:ated|s)?\b", re.IGNORECASE)
_LETTER_RUN = re.compile(r"[A-Za-z\u00c0-\u00ff]+")
_VOWELS = set("aeiou")


def _n(tok: str) -> int:
    tok = tok.lower()
    return int(tok) if tok.isdigit() else _NUM_WORDS.get(tok, 0)


def _scope_at(text: str, pos: int, words: list) -> tuple[list, bool]:
    """The name word(s) a claim at `pos` is about: the NEAREST preceding
    reference in the same sentence ("word two", "the first word"); none →
    every word plus the whole name (a claim then needs to hold for ANY).
    Returns (words to test, whether the whole name counts too)."""
    ref = None
    for m in _WORD_REF_RE.finditer(text):
        if m.start() > pos:
            break
        if re.search(r"[.!?\n]", text[m.end():pos]):    # a sentence boundary resets
            ref = None
            continue
        ref = m
    if ref is None or ref.group("whole"):
        return words, True
    idx = _n(ref.group("wn")) if ref.group("wn") else _ORD_WORDS.get(ref.group("ord").lower(), 0)
    if idx == -1:
        return [words[-1]], False
    if 1 <= idx <= len(words):
        return [words[idx - 1]], False
    return words, True


def _is(letter: str, m) -> bool:
    what = (m.group("what") or "").lower()
    if what == "vowel":
        return letter in _VOWELS
    if what == "consonant":
        return letter.isalpha() and letter not in _VOWELS
    target = m.group("letter") or m.group("qletter") or m.group("uletter") or ""
    return letter == target.lower()


@dataclass(frozen=True)
class _Spelling:
    """Letters to check a claim against when it is about the WHOLE name: the
    words' letters run together, as one run."""

    letters: str
    runs: tuple[str, ...]


def _whole_name(words) -> _Spelling:
    joined = "".join(w.letters for w in words)
    return _Spelling(letters=joined, runs=(joined,))


def _has_double(w) -> bool:
    """A doubled letter is two equal letters SIDE BY SIDE — inside one run,
    never across a hyphen or an apostrophe."""
    return any(a == b for run in w.runs for a, b in zip(run, run[1:]))


def _is_palindrome(w) -> bool:
    return len(w.letters) > 1 and w.letters == w.letters[::-1]


def _spells_inside(w, inner: str) -> bool:
    """A shorter real word spelt inside this one — "self" inside
    "Self-Portrait" counts; the word itself does not."""
    return any(inner in run for run in w.runs) and inner != w.letters


def structural_claim_errors(text: str, name: str) -> list[str]:
    """Claims the clue makes about the NAME'S STRING that are false or
    unverifiable, as feedback lines for the writer (each names the word:
    the writer already knows the name; these never reach the public).

    PERMISSIVE ON AMBIGUITY — the only guard in the package that breaks a
    tie in the TEXT'S favour (Opus, 09/09): when the clue does not say
    which word it means, a claim passes if it holds for ANY word or for
    the whole name ("six letters" passes for Ancient Future because
    FUTURE has six, even if the writer meant ANCIENT). Deliberate — it
    saves regenerations and a true-for-one claim does not mislead — but
    do not assume it fails closed like the others.

    KNOWN LIMIT — this closes the CASE, not the class: the unverifiable
    list (compound, portmanteau, anagram, rhymes with, initials, …) chases
    the model's imagination. The real fix, when there is room: invert the
    burden — the writer DECLARES its structural claims as data next to
    the clue ({"claims": [{"type": "letters", "word": 2, "n": 6}]}), the
    guard verifies those, and prose that makes a structural claim not on
    the declared list is refused. Same jump as emoji: from a banned word
    to a deterministic test."""
    words = list(name_words(name))            # ONE reading of the name (10/10)
    if not words:
        return []
    errs: list[str] = []
    whole = _whole_name(words)

    def holds(pos: int, pred) -> tuple[bool, list]:
        scope, any_ok = _scope_at(text, pos, words)
        ok = any(pred(w) for w in scope) or (any_ok and pred(whole))
        return ok, scope

    def false(m, pred, detail=None):
        ok, scope = holds(m.start(), pred)
        if not ok:
            errs.append(f"'{m.group(0)}' is false for "
                        + (detail(scope) if detail
                           else ", ".join(w.letters for w in scope)))

    m = _UNVERIFIABLE_RE.search(text)
    if m:
        errs.append(f"the clue asserts a hidden structure ('{m.group(0)}') that "
                    "cannot be checked against the string — the last such claim "
                    "was FALSE. Drop it; make only claims a reader can verify on "
                    "the letters (count, first/last letter, doubled letter) or none")
    for m in _LETTERS_RE.finditer(text):
        n = _n(m.group("n"))
        if n:
            false(m, lambda w: len(w.letters) == n,
                  lambda scope: ", ".join(f"{w.letters} ({len(w.letters)} letters)"
                                          for w in scope))
    for m in _WORDS_RE.finditer(text):
        n = _n(m.group("n"))
        if n and n != len(words) and "letter" not in text[m.end():m.end() + 12].lower():
            errs.append(f"'{m.group(0)}' is false: the name has {len(words)} words")
    for m in _STARTS_RE.finditer(text):
        false(m, lambda w: _is(w.letters[0], m))
    for m in _ENDS_RE.finditer(text):
        false(m, lambda w: _is(w.letters[-1], m))
    for m in _FIRST_LETTER_RE.finditer(text):
        pick = ((lambda w: w.letters[0]) if m.group("pos").lower() == "first"
                else (lambda w: w.letters[-1]))
        false(m, lambda w: _is(pick(w), m))
    for m in _DOUBLE_RE.finditer(text):
        false(m, _has_double)
    for m in _PALINDROME_RE.finditer(text):
        false(m, _is_palindrome)
    for m in _NO_VOWELS_RE.finditer(text):
        false(m, lambda w: not any(c in _VOWELS for c in w.letters))
    for m in _CONTAINS_RE.finditer(text):
        inner = m.group("w").lower()
        false(m, lambda w: _spells_inside(w, inner))
    return errs


# --------------------------------------------------------------------------- #
# WORD NUMBERING — the prose counts the one way too (Pedro, 24/11, hunt #40)  #
# --------------------------------------------------------------------------- #
#
# Hunt #40: clue 2 counted the "&" of the name as a word and the clues after
# it did not, so one word had two numbers in the same thread. The one reading
# (name_words, 10/10) never counted the "&". The WRITER did, by itself, and
# nothing read its number back: a position past the end of the name fell
# through to "any word" (`_scope_at`, permissive by design). Now every
# position the prose STATES is read off the text and checked against the
# one reading. The number must exist, and a clue about word k must call it
# word k. The writer is also handed the numbered words, not only the name.

_POS_ORD = {"first": 1, "1st": 1, "second": 2, "2nd": 2, "third": 3, "3rd": 3,
            "fourth": 4, "4th": 4, "fifth": 5, "5th": 5, "sixth": 6, "6th": 6,
            "seventh": 7, "7th": 7, "eighth": 8, "8th": 8, "opening": 1,
            "last": -1, "final": -1, "closing": -1}
# "the third word", "the name's 2nd word", "word two", "word 3", "the last
# word". A determiner is required before an ordinal, and "word" is singular:
# "famous last words" and "the first two words" state no position.
_POSITION_RE = re.compile(
    r"\b(?:the|its|their|this|that|your|name['’]s)\s+(?P<ord>"
    + "|".join(_POS_ORD) + r")[\s-]+word\b"
    r"|\bword\s+(?:no\.?\s*|number\s+|#\s*)?(?P<wn>[1-8]|one|two|three|four|five|six|"
    r"seven|eight)\b",
    re.IGNORECASE)


def stated_positions(text: str, n_words: int) -> list[tuple[str, int]]:
    """Every word position the clue's prose states, as (phrase, position).
    'the last word' is word `n_words`."""
    out: list[tuple[str, int]] = []
    for m in _POSITION_RE.finditer(text or ""):
        k = (_POS_ORD[m.group("ord").lower()] if m.group("ord")
             else _n(m.group("wn")))
        out.append((m.group(0), n_words if k == -1 else k))
    return out


def numbered_words(name: str) -> str:
    """The words of the name as every clue must number them: "1 'Salt',
    2 'Harbor'". The one reading (name_words): a symbol is not a word."""
    return ", ".join(f"{w.position} '{w.text}'"
                     + (" (ONE hyphenated word)" if w.hyphenated else "")
                     for w in name_words(name))


def word_position_errors(text: str, name: str, facet: str | None) -> list[str]:
    """Writer feedback when the clue numbers the words its own way: a
    position the name does not have ("the third word" of a two-word name,
    the "&" counted), or a clue about word k that calls it something else.
    Image pieces may mention a word, by its true number."""
    words = name_words(name)
    if not words:
        return []
    n = len(words)
    refs = stated_positions(text, n)
    if not refs:
        return []
    numbering = numbered_words(name)
    errs = [f"'{phrase}' is word {k}, but the name has {n} word"
            f"{'s' if n != 1 else ''}, numbered the one way every clue counts "
            f"them: {numbering}. A symbol such as '&' is not a word and takes "
            "no number"
            for phrase, k in refs if not 1 <= k <= n]
    if not errs and facet and facet.startswith("name_word_"):
        k = int(facet.rsplit("_", 1)[1])
        if all(pos != k for _phrase, pos in refs):
            said = ", ".join(f"'{phrase}'" for phrase, _pos in refs)
            mine = words[k - 1].text if 1 <= k <= n else "?"
            errs.append(f"this clue is about word {k} ('{mine}') but it says {said}. "
                        f"Call it word {k}, the number the FACET gives — the words "
                        f"are numbered {numbering}")
    return errs


# --------------------------------------------------------------------------- #
# DECLARE-AND-VERIFY (Opus, 09/09 — the class fix, not the case fix)          #
# --------------------------------------------------------------------------- #
#
# Three false clues from the same family (Hunt #9; both live tests of 09/09,
# the third one a commit AFTER the blacklist above) proved the point: a
# blacklist of phrasings chases the model's imagination. So the writer now
# returns what it DID as data next to the clue —
#     {"clue", "taunt", "angle": "<label>", "image_aspect": "<aspect>|null",
#      "claims": [{"type": "starts", "word": 2, "value": "vowel"}, …]}
# — and the guard verifies the DATA against the string and the assignment:
#   · every declared claim must be TRUE on the real spelling;
#   · prose that asserts something about the letters must be DECLARED
#     (undeclared → rejected), and what cannot be expressed as a claim type
#     (compound, hidden word, suffix, anagram, rhyme, initials) is refused;
#   · the declared angle must be the ASSIGNED one (name pieces), and the
#     declared image aspect the assigned one (art pieces) — two art pieces
#     get two different aspects by construction, so "phosphor, beam, tube"
#     cannot come twice;
#   · a STRUCTURE piece must carry at least one verified claim.
# Same jump as emoji: from "banned by prompt" to a deterministic test.

CLAIM_TYPES = ("starts", "ends", "contains", "double_letter", "palindrome",
               "no_vowels", "word_count", "hyphenated")

IMAGE_ASPECTS = {
    "subject": "WHAT is in the frame — one object, figure or element, obliquely",
    "composition": "HOW the frame is arranged — placement, scale, framing, empty space",
    "palette": "COLOUR and LIGHT — the palette, contrast, where the light comes from",
    "medium": "the MAKING — technique, process, material, the tool that made it",
    "mood": "the FEELING — atmosphere, era, energy, what it reads as",
    "text": "TEXT or SYMBOLS in the image, if any — their style, never their content",
    "motion": "TIME — stillness or motion, before/after, what is happening",
}
_ASPECT_ORDER = tuple(IMAGE_ASPECTS)


def angle_label(angle: str | None) -> str | None:
    return angle.split(":")[0].strip().upper() if angle else None


def image_aspect_for(clue_index: int, ctx) -> str | None:
    """The aspect assigned to THIS art piece: the k-th image piece of the
    plan takes the k-th aspect of a per-hunt permutation seeded by the name
    (stable across a crash-resume; distinct for every art piece by
    construction). None for name pieces and the reveal phase."""
    plan = ctx.clue_facet_plan or target_ramp_plan(ctx.display_name)
    if clue_index > len(plan) or plan[clue_index - 1][0] != "image":
        return None
    order = list(_ASPECT_ORDER)
    random.Random(f"{ctx.display_name}|aspects").shuffle(order)
    k = sum(1 for i in range(clue_index - 1) if plan[i][0] == "image")
    return order[k % len(order)]


def _claim_scope(words: list, word: int) -> tuple[list, bool]:
    if word == 0:
        return words, True
    if 1 <= word <= len(words):
        return [words[word - 1]], False
    return [], False


def verify_claims(claims, name: str) -> list[str]:
    """Every declared claim, checked against the real spelling. Errors name
    the claim and the word (writer-facing; never public).

    `word` is the word's TRUE position in the name (name_words, 10/10) — the
    same number the writer was given for the piece, function words and all.
    Until then this counted runs of letters while the plan counted spaces,
    and a true claim about the third word of "Self-Portrait at Dawn" was
    checked against "at"."""
    words = list(name_words(name))
    errs: list[str] = []
    if not isinstance(claims, list):
        return ["'claims' must be a list"]
    for c in claims:
        if not isinstance(c, dict) or c.get("type") not in CLAIM_TYPES:
            errs.append(f"claim {c!r}: unknown type — only {', '.join(CLAIM_TYPES)} "
                        "can be declared (and therefore asserted)")
            continue
        t = c["type"]
        try:
            w = int(c.get("word", 0))
        except (TypeError, ValueError):
            w = -1
        scope, whole_ok = _claim_scope(words, w)
        if not scope:
            errs.append(f"claim {t}: 'word' must be 1..{len(words)} or 0 for the whole name")
            continue
        v = str(c.get("value", "")).strip().lower()
        joined = "".join(x.letters for x in scope) if whole_ok else scope[0].letters
        spelt = ", ".join(x.letters for x in scope)
        if t == "word_count":
            ok = v.isdigit() and int(v) == len(words)
            why = f"the name has {len(words)} words"
        elif t in ("starts", "ends"):
            if v in ("vowel", "consonant"):
                def pred(ch):
                    return (ch in _VOWELS) if v == "vowel" else (ch.isalpha() and ch not in _VOWELS)
            elif len(v) == 1 and v.isalpha():
                def pred(ch):
                    return ch == v
            else:
                errs.append(f"claim {t}: value must be a single letter, 'vowel' or 'consonant'")
                continue
            ok = pred(joined[0] if t == "starts" else joined[-1])
            why = f"'{joined}' {t} with '{joined[0] if t == 'starts' else joined[-1]}'"
        elif t == "contains":
            if len(v) < 2 or not v.isalpha():
                errs.append("claim contains: value must be a real substring of 2+ letters")
                continue
            ok = any(_spells_inside(x, v) for x in scope) \
                or (whole_ok and v in joined and v != joined)
            why = f"'{v}' is not spelt inside " + spelt
        elif t == "double_letter":
            ok = any(_has_double(x) for x in scope)
            why = "no letter is doubled in " + spelt
        elif t == "palindrome":
            ok = any(_is_palindrome(x) for x in scope) or (whole_ok and joined == joined[::-1])
            why = "nothing here reads the same backwards"
        elif t == "hyphenated":
            ok = any(x.hyphenated for x in scope)
            why = "no hyphen joins two parts in " + ", ".join(f"'{x.text}'" for x in scope)
        else:  # no_vowels
            ok = any(not any(ch in _VOWELS for ch in x.letters) for x in scope)
            why = "every word here has a vowel"
        if not ok:
            errs.append(f"claim {t} (word {w}, value {v!r}) is FALSE: {why}")
        errs += example_errors(c)          # hunt #41: the words AROUND the fact
    return errs


# --------------------------------------------------------------------------- #
# EXAMPLES IN A LETTER CLUE — each one checked (Pedro, 24/11, hunt #41)       #
# --------------------------------------------------------------------------- #
#
# Hunt #41, clue 4: "detour", "corridor" and "contender" were given as words
# that do NOT end with the same letter as "adventurer" — all four end in R.
# The declaration covered the NAME only, and the judge reads a clue against
# the answer: the examples are not the answer, so nobody checked them. An
# example is data now, declared inside its claim as "like" (it shares the
# fact) or "unlike" (it does not), and tested on its own spelling with the
# same test as the name. In a letter clue, a quoted word that is not declared
# is refused, and so is a comparison ("the same letter as …", "ends like
# '…'") with no starts/ends claim behind it.

_LETTER_FACTS = ("starts", "ends", "contains", "double_letter", "palindrome", "no_vowels")


def _letter_test(t: str, v: str):
    """The test a declared letter fact applies to a spelling (lower-case
    letters) — the same one for the examples as for the name. None when
    the fact has no letter test (word_count) or its value is not valid."""
    if t in ("starts", "ends"):
        if v == "vowel":
            def is_(ch):
                return ch in _VOWELS
        elif v == "consonant":
            def is_(ch):
                return ch.isalpha() and ch not in _VOWELS
        elif len(v) == 1 and v.isalpha():
            def is_(ch):
                return ch == v
        else:
            return None
        at = 0 if t == "starts" else -1
        return lambda s: bool(s) and is_(s[at])
    if t == "contains":
        return (lambda s: v in s) if len(v) >= 2 and v.isalpha() else None
    if t == "double_letter":
        return lambda s: any(a == b for a, b in zip(s, s[1:], strict=False))
    if t == "palindrome":
        return lambda s: len(s) > 1 and s == s[::-1]
    if t == "no_vowels":
        return lambda s: bool(s) and not any(ch in _VOWELS for ch in s)
    return None


def _fact(t: str, v: str) -> str:
    return {"starts": f"starts with {v}", "ends": f"ends with {v}",
            "contains": f"contains '{v}'", "double_letter": "has a doubled letter",
            "palindrome": "reads the same backwards", "no_vowels": "has no vowel"}[t]


def _spelling_says(t: str, v: str, s: str, holds: bool) -> str:
    """What the example's own spelling shows, for the writer."""
    if t == "starts":
        return f"'{s}' starts with '{s[0]}'"
    if t == "ends":
        return f"'{s}' ends with '{s[-1]}'"
    if t == "contains":
        return f"'{v}' is {'' if holds else 'not '}spelt inside '{s}'"
    if t == "double_letter":
        return f"'{s}' has {'a' if holds else 'no'} doubled letter"
    if t == "palindrome":
        return f"'{s}' {'reads' if holds else 'does not read'} the same backwards"
    return f"'{s}' has {'no vowel' if holds else 'a vowel'}"


def _example_key(word) -> str:
    return "".join(_LETTER_RUN.findall(str(word))).lower()


def example_errors(claim) -> list[str]:
    """The examples a claim declares, each tested on its own spelling:
    every "like" word must share the fact, every "unlike" word must not."""
    like, unlike = claim.get("like") or [], claim.get("unlike") or []
    if not like and not unlike:
        return []
    t = claim.get("type")
    if not isinstance(like, list) or not isinstance(unlike, list):
        return [f"claim {t}: 'like' and 'unlike' must be lists of words"]
    v = str(claim.get("value", "")).strip().lower()
    test = _letter_test(t, v)
    if test is None:
        return [f"claim {t}: examples ('like'/'unlike') only go with a letter fact "
                f"({', '.join(_LETTER_FACTS)}) that has a valid value"]
    errs: list[str] = []
    for side, examples, want in (("like", like, True), ("unlike", unlike, False)):
        for raw in examples:
            s = _example_key(raw)
            if len(s) < 2:
                errs.append(f"claim {t}: example {raw!r} is not a word")
                continue
            holds = test(s)
            if holds != want:
                errs.append(
                    f"example '{raw}' is declared {side.upper()} the fact "
                    f"({_fact(t, v)}), but {_spelling_says(t, v, s, holds)} — a false "
                    "example sends players the wrong way: drop it, or pick a word "
                    f"that really {'shares the fact' if want else 'differs'}")
    return errs


def example_prose_errors(text: str, claims) -> list[str]:
    """Writer feedback when a LETTER clue — one that declares a letter fact,
    or whose prose talks about letters — quotes a word it did not declare
    as an example, or compares letters with no starts/ends claim behind
    the comparison. Quoted words in a clue about meaning are left alone."""
    good = [c for c in claims if isinstance(c, dict)] if isinstance(claims, list) else []
    facts = [c for c in good if c.get("type") in _LETTER_FACTS]
    errs: list[str] = []
    compare = _COMPARE_RE.search(text)
    if compare and not any(c.get("type") in ("starts", "ends") for c in good):
        errs.append(f"the prose compares letters ('{compare.group(0).strip()}') but no "
                    "'starts'/'ends' claim is declared — declare it, with the compared "
                    "words as its examples, or drop the comparison")
    talks_letters = compare or _FIRST_LETTER_RE.search(text) or any(
        rx.search(text) for rx, t in _PROSE_DETECTORS if t not in ("word_count", "hyphenated"))
    if not (facts or talks_letters):
        return errs
    declared = {_example_key(w) for c in facts
                for w in [*(c.get("like") or []), *(c.get("unlike") or [])]
                if isinstance(c.get("like") or [], list)
                and isinstance(c.get("unlike") or [], list)}
    values = ({str(c.get("value", "")).strip().lower() for c in facts
               if c.get("type") == "contains"}
              | {m.group("w").lower() for m in _CONTAINS_RE.finditer(text)})
    for m in _QUOTED_WORD_RE.finditer(text):
        key = _example_key(m.group("w"))
        if key in declared or key in values:
            continue
        errs.append(f"the clue quotes '{m.group('w')}' next to a letter fact without "
                    "declaring it — put it in that claim's \"like\" (it shares the "
                    "fact) or \"unlike\" (it does not), where it is checked on its "
                    "spelling, or drop it")
    return errs


_PROSE_DETECTORS = (                 # prose pattern → the claim type it must declare
    (_STARTS_RE, "starts"), (_ENDS_RE, "ends"), (_DOUBLE_RE, "double_letter"),
    (_PALINDROME_RE, "palindrome"), (_NO_VOWELS_RE, "no_vowels"),
    (_CONTAINS_RE, "contains"), (_WORDS_RE, "word_count"),
    (_HYPHEN_PROSE_RE, "hyphenated"),
)
_ENUM_LIST_RE = re.compile(r"\b[\w-]+,\s+[\w-]+,?\s+(?:and|or|nor)\s+[\w-]+\b", re.IGNORECASE)


def undeclared_prose_errors(text: str, claims) -> list[str]:
    """Prose that asserts something about the letters without a matching
    declared claim — the writer must put every structural statement in
    `claims`, where it is verified. The unverifiable family is refused
    outright, declared or not."""
    declared = {c.get("type") for c in claims if isinstance(c, dict)}
    errs: list[str] = []
    m = _UNVERIFIABLE_RE.search(text)
    if m:
        errs.append(f"the clue asserts '{m.group(0)}' — a structure the guard cannot "
                    "check and the writer cannot declare. Drop it; only a "
                    "declarable claim type may be asserted")
    for rx, t in _PROSE_DETECTORS:
        m = rx.search(text)
        if m and t not in declared:
            if t == "word_count" and "letter" in text[m.end():m.end() + 12].lower():
                continue
            if t == "first_letter":
                continue
            errs.append(f"the prose says '{m.group(0)}' but no '{t}' claim is declared — "
                        "declare it (it will be verified) or remove it")
    for m in _FIRST_LETTER_RE.finditer(text):
        t = "starts" if m.group("pos").lower() == "first" else "ends"
        if t not in declared:
            errs.append(f"the prose says '{m.group(0)}' but no '{t}' claim is declared")
    return errs


# Clues 1-3 say nothing about the letters (Pedro, 16/10, after Hunt #22). The
# deck no longer hands them STRUCTURE (relic_clues.STRUCTURE_EARLIEST), but
# any angle can still state a letter fact by declaring it, and a true one
# passes every check above. So on those clues ANY declared claim, and any
# prose these detectors read as talk about the letters, is refused. Counts of
# letters, syllables, vowels and consonants never get this far: the base text
# rules refuse them on every clue (guardrails, rule 1c).
_EARLY_LETTER_DETECTORS = (*(rx for rx, t in _PROSE_DETECTORS if t != "hyphenated"),
                           _FIRST_LETTER_RE, _UNVERIFIABLE_RE, _COMPARE_RE)


def early_letter_errors(draft, clue_index: int) -> list[str]:
    """Writer feedback when one of the first three clues talks about the
    letters, the spelling or a count of the name, the word count included
    (Pedro, 16/10). Empty from clue STRUCTURE_EARLIEST on."""
    if clue_index >= STRUCTURE_EARLIEST:
        return []
    letters = [c for c in draft.claims
               if not (isinstance(c, dict) and c.get("type") == "hyphenated")]
    if not letters and not any(rx.search(draft.text) for rx in _EARLY_LETTER_DETECTORS):
        return []
    return [f"clue #{clue_index} is one of the first three: it says NOTHING about the "
            "letters, the spelling or any count of the name (word count included) — "
            f"that starts at clue {STRUCTURE_EARLIEST}. Rewrite from the assigned angle "
            'with "claims": []']


def declaration_errors(draft, ctx, clue_index: int) -> list[str]:
    """The whole declare-and-verify check for one draft (writer feedback)."""
    errs: list[str] = []
    facet, _ = relic_slot_for(clue_index, ctx)
    puzzle = clue_index <= puzzle_clues_of(ctx)
    # angle / aspect: declared == assigned
    if puzzle and facet != "image":
        assigned = angle_label(angle_for_unverifiable(clue_index, ctx))
        got = (draft.angle or "").split(":")[0].strip().upper() or None
        if assigned and got != assigned:
            errs.append(f"'angle' must declare the ASSIGNED angle {assigned!r} "
                        f"(you declared {got!r}) — write from that angle and say so")
        if assigned == "STRUCTURE" and not draft.claims:
            errs.append("a STRUCTURE piece must DECLARE the fact it states as a claim "
                        "(it is verified on the spelling); no claim, no piece")
        if _ENUM_LIST_RE.search(draft.text):
            errs.append("the clue enumerates a list ('a, b, and c') — that reads as a "
                        "synonym list, solved by lookup in ten seconds. ONE oblique "
                        "constraint, no lists")
    if puzzle and facet == "image":
        assigned = image_aspect_for(clue_index, ctx)
        got = (draft.image_aspect or "").strip().lower() or None
        if assigned and got != assigned:
            errs.append(f"'image_aspect' must be the ASSIGNED aspect {assigned!r} "
                        f"(you declared {got!r}); the other art piece has another "
                        "aspect — two pieces on the same aspect is one piece")
    errs += verify_claims(draft.claims, ctx.display_name)
    errs += undeclared_prose_errors(draft.text, draft.claims)
    errs += example_prose_errors(draft.text, draft.claims)       # hunt #41
    errs += hyphen_errors(draft, ctx, clue_index)                 # hunt #41
    return errs


# --------------------------------------------------------------------------- #
# HYPHENATED WORDS — every clue says so (Pedro, 24/11, hunt #41)              #
# --------------------------------------------------------------------------- #
#
# Hunt #41: a word of the name was hyphenated ("X-Contour") and the clues
# described only the part after the hyphen. A player who decoded them had
# "contour" and searched the wrong thing. Pedro's two options: each part a
# word of its own, or a clue that says plainly the word is hyphenated. The
# second keeps the one reading (10/10: one word, one number), the plan and
# every preparation already sealed. Every clue about such a word, from
# clue 1, says "hyphenated" and declares it. The claim is verified like the
# others, and it is the one structural fact allowed before clue 4.


def _facet_word(ctx, clue_index: int):
    facet, _ = relic_slot_for(clue_index, ctx)
    if not facet.startswith("name_word_"):
        return None
    return word_at(ctx.display_name, int(facet.rsplit("_", 1)[1]))


def _declares_hyphen(claims, k: int) -> bool:
    for c in claims if isinstance(claims, list) else []:
        if isinstance(c, dict) and c.get("type") == "hyphenated":
            try:
                if int(c.get("word", -1)) == k:
                    return True
            except (TypeError, ValueError):
                continue
    return False


def hyphen_errors(draft, ctx, clue_index: int) -> list[str]:
    """Writer feedback when a clue about a HYPHENATED word does not say
    plainly that it is hyphenated, or does not declare it."""
    w = _facet_word(ctx, clue_index)
    if w is None or not w.hyphenated:
        return []
    if _HYPHEN_PROSE_RE.search(draft.text) and _declares_hyphen(draft.claims, w.position):
        return []
    parts = " + ".join(f"'{p}'" for p in w.parts)
    return [f"word {w.position} ('{w.text}') is ONE word made of parts joined by a "
            f"hyphen ({parts}). Every clue about it says so PLAINLY — use the word "
            "'hyphenated' — and declares {\"type\": \"hyphenated\", \"word\": "
            f"{w.position}}}. A clue that describes one part as if it were the "
            "whole word sends players to search the wrong thing; if you point at "
            "one part, say which (before or after the hyphen)"]


# What the writer is told after an answer that could not be read. ONE object:
# two of them is as unreadable as none (hunt #17).
ONE_OBJECT_REMINDER = (
    "Your previous answer could not be read. Check your claims SILENTLY and "
    "respond with exactly ONE JSON object — nothing before it and nothing "
    "after it.")


def _first_json_object(text: str) -> dict:
    """The FIRST complete JSON object in the writer's answer.

    Hunt #17, clue 4: the writer answered with TWO objects. Reading from the
    first "{" to the last "}" handed both to the parser as one ("Extra data:
    line 3 column 1") and the round died on an answer whose first object was
    whole. `raw_decode` stops where the first object ends; what follows is
    ignored. A "{" that opens nothing readable (prose, an object cut short)
    is skipped and the next one is tried."""
    decoder = json.JSONDecoder()
    start = text.find("{")
    while start != -1:
        try:
            obj, _ = decoder.raw_decode(text, start)
        except ValueError:
            obj = None
        if isinstance(obj, dict):
            return obj
        start = text.find("{", start + 1)
    # NEVER the text: the writer's reasoning names the answer, and this
    # message reaches the operator channel (audit 10/09: the 4th
    # real-clues run printed 'Looking at "Ancient"…' to the notifier)
    raise ValueError(f"no JSON object in clue response ({len(text)} chars)")


def parse_target_clue(text: str):
    """The target writer's JSON → ClueDraft with the declared data. Missing
    fields are None/[] (and then fail the declaration check with a reason)."""
    from ..content.clue_engine import ClueDraft, _json_text, _strip_leading_meta
    data = _first_json_object(text or "")
    # `null` is an empty field, never the word "None" (`_json_text`)
    clue = _strip_leading_meta(_json_text(data.get("clue")))
    if not clue:
        raise ValueError("empty clue text")
    claims = data.get("claims") or []
    return ClueDraft(text=clue, taunt=_json_text(data.get("taunt")) or None,
                     angle=(str(data["angle"]).strip() if data.get("angle") else None),
                     image_aspect=(str(data["image_aspect"]).strip().lower()
                                   if data.get("image_aspect") else None),
                     claims=claims if isinstance(claims, list) else [],
                     language_hint=_json_text(data.get("language_hint")) or None)


# --------------------------------------------------------------------------- #
# Context                                                                      #
# --------------------------------------------------------------------------- #

_ARTIST_KEYS = ARTIST_KEYS      # one reading (R9): banned here, credited at the reveal

# Function words are not the answer (07/10). The never-write list is matched
# as a SUBSTRING (guardrails 1b), so a function word in a name refused every
# clue that merely contained it inside another word — "there", "often",
# "into", "water" — and a clue that may not contain those can hardly be
# written. Exactly these eight leave the list. The matching itself (the
# substring, the roots) is untouched: a distinctive word is still refused as
# a word and as a root.
#
# 10/10: the list lives in content/name_words.py, with the ONE reading of a
# name's words — the same eight decide which words get puzzle pieces.
_FUNCTION_WORDS = FUNCTION_WORDS


@dataclass
class TargetClueContext(RelicClueContext):
    """RelicClueContext plus what the target engine needs and the writer must
    never see. `target_id` and `name_onchain` feed the SEARCH GUARD only —
    they are never formatted into a prompt."""

    target_id: str = ""
    name_onchain: str = ""
    # THE LANGUAGE OF THE NAME, measured at /prepare (06/11): a NameLanguage,
    # or None — not measured (a preparation from before, or the call failed)
    name_language: object = None

    @classmethod
    def from_target(cls, target: Target, *, image_description: str,
                    metadata: dict | None = None,
                    rare_last=None, name_language=None) -> "TargetClueContext":
        """`image_description` comes from the vision step (see
        describe_image_batched); `metadata` (the full token metadata, if the
        caller has it) contributes the artist/creator name to the never-write
        list — a creator name is a search box too."""
        # the name's own terms: runs of 2+ letters minus the function words
        # (and all of them, if nothing else is left) — name_words.answer_terms
        terms = answer_terms(target.name)
        for k in _ARTIST_KEYS:
            v = (metadata or {}).get(k)
            if isinstance(v, str) and v.strip() and not v.startswith("0x"):
                terms.extend(t.lower() for t in re.findall(r"[A-Za-zÀ-ÿ]{3,}", v)
                             if t.lower() not in _FUNCTION_WORDS)
        return cls(
            display_name=target.name,
            image_description=image_description,
            lore=(target.description or "")[:400],
            backstory=(target.description or "")[:400],
            solution_terms=sorted(set(terms)),
            enumerable_words=enumerable_words_in(target.name),
            # the puzzle of a TARGET hunt: two pieces a content word and two
            # for the artwork — 4, 6 or 8 clues, not a fixed seven (10/10);
            # in the rarity order measured at /prepare, when there is one
            # (28/10 — None: the order of before)
            clue_facet_plan=target_ramp_plan(target.name, rare_last=rare_last),
            angle_offset=sum(ord(c) for c in target.name) % len(PUZZLE_ANGLES),
            target_id=target.id(),
            name_onchain=target.name_onchain,
            name_language=name_language,
        )


# --------------------------------------------------------------------------- #
# Vision — batched image fetch, so the gateway never learns which one          #
# --------------------------------------------------------------------------- #


class ImageUnavailable(RuntimeError):
    """The artwork could not be fetched or described. The preparer treats
    this as launch-refusing (fail-closed): two puzzle pieces and the reveal
    description depend on it, and a hunt without them is a weaker hunt we
    never launch on hope."""


class ContentRefused(RuntimeError):
    """The image failed the content guard (NSFW, gore, hate symbols, stolen
    famous art, scam). Carries the target id so the caller EXCLUDES it and
    draws again — the message never carries a name."""

    def __init__(self, target_id: str, reason: str = ""):
        super().__init__("content guard refused the artwork")
        self.target_id = target_id
        self.reason = reason[:160]


def _fetch_cause(e: BaseException) -> str:
    """A label for a failed image read — HTTP code, 'timeout' or the
    exception type — read through the cause chain. Never a URL: the label
    lands in the operator's refusal message."""
    seen = 0
    cur: BaseException | None = e
    while cur is not None and seen < 6:
        code = getattr(cur, "code", None)
        if isinstance(code, int):
            return f"HTTP {code}"
        if isinstance(cur, TimeoutError) or "timed out" in str(cur).lower():
            return "timeout"
        reason = getattr(cur, "reason", None)     # urllib.error.URLError
        if isinstance(reason, BaseException):
            cur = reason
        else:
            cur = cur.__cause__ or cur.__context__
        seen += 1
    return type(e).__name__


def describe_image_batched(*, target_image_url: str,
                           decoy_image_urls: Sequence[str],
                           fetch_bytes_generic: Callable[[str], bytes | None],
                           describe: Callable[[bytes], str],
                           decoys: int = 7,
                           rng: random.Random | None = None,
                           content_ok: Callable[[bytes], "bool | None"] | None = None,
                           target_id: str = "",
                           rounds: int = 3, pause_s: float = 10.0,
                           sleep: Callable[[float], None] = time.sleep) -> str:
    """Fetch the target's image inside a shuffled batch of decoy images (from
    snapshot entries) through a generic public gateway, then describe ONLY
    the target's bytes with the vision callable (our own provider, our own
    key — that is fine; it is the marketplace/gateway that must not learn
    the target). Decoy fetch results are discarded. Raises ImageUnavailable
    when the target's bytes cannot be fetched or the description is empty.

    RETRY ROUNDS (Hunt #11, 16/09): the public gateway timed out on the
    artwork twice in a row (one read, 25 s, fail-closed). Reads that failed
    are retried for up to `rounds` passes with `pause_s` between passes —
    EVERY failed read, decoy or target alike, whether or not the target
    already came back: the gateway only ever sees "failed reads get
    retried", never which one we cared about. The refusal carries the
    MEASURED causes (HTTP code / timeout counts), never a URL.

    CONTENT GUARD (Opus, 06/09): text cannot see NSFW or stolen art, and
    this is the one place the target's IMAGE is already in hand, inside a
    batch that already exists — so `content_ok(bytes)` runs here, on the
    same bytes, zero extra reads. False → ContentRefused(target_id) (the
    caller excludes and redraws); None (vision unreachable) → fail-closed,
    ImageUnavailable. The batched text judge keeps writability."""
    rng = rng or random.SystemRandom()
    from .refresh import uri_is_content_addressed
    others = [u for u in decoy_image_urls
              if u and u != target_image_url and uri_is_content_addressed(u)]
    # P1-5: the batch is EXACTLY decoys + 1 through the batch's gateway, or
    # it is not a batch. A missing or https:// image silently shrank it.
    if len(others) < decoys or not uri_is_content_addressed(target_image_url):
        raise ImageUnavailable(
            f"image batch would have {len(others) + 1} gateway reads, not "
            f"{decoys + 1} — a decoy without a content-addressed image "
            "shrinks the anonymity set; refusing")
    batch = rng.sample(others, decoys) + [target_image_url]
    rng.shuffle(batch)
    got: dict[str, bytes] = {}
    causes: dict[str, int] = {}
    pending = list(batch)
    for r in range(max(1, rounds)):
        if r:
            sleep(pause_s)
        still: list[str] = []
        for url in pending:
            try:
                data = fetch_bytes_generic(url)
            except Exception as e:  # noqa: BLE001 — counted, retried next round
                causes[_fetch_cause(e)] = causes.get(_fetch_cause(e), 0) + 1
                data = None
            else:
                if not data:
                    causes["empty"] = causes.get("empty", 0) + 1
            if data:
                got[url] = data
            else:
                still.append(url)
        pending = still
        if not pending:
            break
    target_bytes = got.get(target_image_url)
    if not target_bytes:
        tally = ", ".join(f"{k}×{v}" for k, v in
                          sorted(causes.items(), key=lambda kv: -kv[1])) or "none"
        raise ImageUnavailable(
            "artwork bytes unavailable via the generic gateway after "
            f"{max(1, rounds)} rounds ({len(pending)} of {len(batch)} reads still "
            f"failing; causes: {tally}) — not launching without the art")
    if content_ok is not None:
        verdict = content_ok(target_bytes)
        if verdict is None:
            raise ImageUnavailable("content guard unreachable — not launching "
                                   "on an unchecked artwork (fail-closed)")
        if verdict is False:
            raise ContentRefused(target_id)
    text = (describe(target_bytes) or "").strip()
    if not text:
        raise ImageUnavailable("vision returned an empty description")
    return text


# --------------------------------------------------------------------------- #
# Prompt                                                                       #
# --------------------------------------------------------------------------- #

TARGET_SYSTEM_PROMPT = """You are the game master of "Finding Memeland", writing \
CLUES for the current treasure hunt, posted on the main @FindingMemeland account. \
The hidden target is an EXISTING NFT that belongs to somebody else, somewhere \
onchain. Players WIN by working out the NFT's NAME, finding that exact token on a \
marketplace, and posting its identity (chain:contract:tokenId, or the marketplace \
link) as a REPLY to the Clue 1 post. There are NO DMs in this game — never tell \
players to DM anyone or mention DMs at all.

Your clues point at exactly TWO things: the words of the NFT's NAME and its \
ARTWORK. Nothing else. The NAME is the only searchable thing, so the name carries \
the hunt.

ABSOLUTE SECRECY OF THE ADDRESS: never say or hint WHICH blockchain the NFT lives \
on, WHICH marketplace or platform it was minted or listed on, WHO made it, or \
WHEN. Not as a word, not as a nickname, not as an allusion ("the chain everyone \
moved to", "the blue marketplace"). The chain and the platform are part of the \
ANSWER — a player must work them out by finding the token, and a clue that names \
them collapses the game into one search box. A mechanical guard rejects any clue \
containing a chain or platform name.

THE PUZZLE DOCTRINE (the heart of the game). Early clues are PIECES of a puzzle, \
not steps of a staircase. Players solve these with AI help, cross-referencing \
every clue against candidate answers — so a piece is NOT meant to be solvable on \
its own. Each piece is a CONSTRAINT that narrows the space of possible words (a \
semantic field, a cultural use, how the word is built, a relation between the \
words, a detail of the art), and the pieces INTERSECT on exactly one answer. \
Write each clue so that: (a) alone it is genuinely hard and could fit several \
words; (b) combined with the earlier clues it eliminates almost everything else; \
(c) it is CHECKABLE — a solver who guesses the right word can confirm the clue \
fits, and one who guesses wrong can rule it out. Never repeat an angle you have \
already used: each clue must add NEW information, or the intersection never \
tightens.

A SEARCH GUARD also reads every puzzle-phase clue: it types your clue into a \
marketplace search box, and if the target comes up, the clue is rejected. A \
puzzle piece must never work as a search query — no literal description of the \
picture, no phrase that appears in the title.

The on-chain description (the 'lore' below) is FLAVOUR only — never make players \
guess it, and never quote it: a quoted phrase is a search query.

Voice: playful, ironic, meme-native crypto Twitter. Community language, cheeky, \
lowercase is fine. NOT mystical or poetic. A smug oracle enjoying the struggle. \
Emoji only where the phase rules below allow them. The hidden NFT is "the \
treasure" (or "the piece", "the name") — NEVER "the relic": there are no relics \
in this game and the word sends players to the wrong collection.

Hard rules for the clue text:
- One short post, max ~200 characters. Standalone clue text only.
- NEVER write verbatim: the NFT's name or any of its words, the creator's name, \
any URL, or hashtags. You HINT at them; you never spell them out — that is the \
puzzle.
{phase_rules}
- Obliqueness. You are writing clue #{index}; target obliqueness {obliqueness} \
(1.0 = maximally subtle; lower = clearer). The difficulty of EACH clue is set by \
the game's ramp — obey the number, not the clue's position. Never just write the \
name.
- HARD clues (obliqueness {hard_floor} or higher) are ONE oblique angle: a single \
sideways reference that rewards knowledge or lateral thinking. NEVER a list of \
synonyms, NEVER more than one identifying fact (if your clue splits into two \
facts, delete one), and NEVER an etymology, dictionary meaning or "the name \
means…" — a meaning is a lookup, save it for the easy revisit. The test is \
RECOGNITION vs INFERENCE: if the clue DESCRIBES the thing and the reader merely \
recognises it, that is a lookup — too easy, rewrite.
- THE ONE-MINUTE TEST for every hard clue: would a sharp player WITH AN AI, \
holding ONLY this clue, land on the word in under a minute? If yes, rewrite it. \
The SIDEWAYS DEFINITION is the most common failure: rephrasing what a word MEANS \
is still a definition. A hard clue attacks from a direction that is NOT the \
word's meaning: how it is built, where culture puts it to work, which world it \
lives in, how it leans on the other words.
- Each clue must add a NEW angle, roughly 30% clearer than the previous one.
- NEVER build a clue on counting: do not state how many syllables, letters, \
characters, vowels or consonants anything has. The only number you may state \
about the name is its word count, and only if you are certain it is exact.
- FACET TARGETING: each clue focuses on ONE real attribute (given below) and must \
CRYPTICALLY signal which one — name or artwork — naming the facet outright only \
when obviousness is high (obliqueness 0.4 or lower).
- WHERE to search is NOT your job: the pinned rules tell players it is an NFT \
and how to claim. Never spend a clue on that.
- SPELLING WARNING: marketplace search is UNFORGIVING. If the name is spelt in a \
non-standard way, you MUST say so PLAINLY in the reveal phase ("it's spelt wrong \
on purpose", "drop a letter from what you'd expect"). A sly hint is NOT enough.

For clue #1 only, set taunt to "". For clue #2 and later, also write a short, \
varying jeer that pokes fun at players for not solving it yet. If the jeer \
mentions how many clues are out, the number is exactly {index} — this one \
included. Clue counts and jeering belong in the taunt ONLY: the clue text never \
opens with "N clues in…" or any jab — it is the puzzle and nothing else. The \
taunt is a sentence of its own: it never repeats the clue, never starts it, and \
never ends hanging on a dash. Never call a clue "final" or "last": the ramp may \
continue.

DECLARE WHAT YOU DID (it is verified mechanically; a mismatch rejects the clue):
- "angle": for a NAME piece, the label of the angle you were assigned (e.g. \
"SEMANTIC FIELD") — you must write from that angle and say so; null for art \
pieces and reveal clues.
- "image_aspect": for an ART piece, the aspect you were assigned (e.g. \
"palette"); null otherwise.
- "claims": EVERY statement your clue makes about the LETTERS or SPELLING of the \
name, as data. Allowed types: "starts" / "ends" (value: a single letter, \
"vowel" or "consonant"), "contains" (value: a shorter real word spelt inside), \
"double_letter", "palindrome", "no_vowels", "word_count" (value: the number), \
"hyphenated" (no value: the word is ONE word made of parts joined by a hyphen). \
"word" is 1 for the first word, 2 for the second, 0 for the whole name. Each \
claim is CHECKED against the real spelling: a false one rejects the clue. If \
your prose asserts something about the letters that is not declared, the clue \
is rejected. If a structural idea cannot be expressed as one of these types \
(compound, portmanteau, hidden word, prefix, suffix, anagram, rhyme, initials), \
you may NOT assert it — do not guess how a word is built. An empty list means \
your clue says nothing about the letters, which is the normal case.
- EXAMPLES in a letter clue: if you give a word that shares the letter fact \
("the same last letter as 'anchor'") or one that does not ("unlike 'cloud'"), \
put it in quotes and declare it INSIDE that claim — "like": [the words that \
share the fact], "unlike": [the words that do not]. Every example is checked on \
its own spelling exactly like the name: one false example rejects the clue, and \
so does a quoted word in a letter clue that you did not declare.
- "language_hint": ONLY when the message asks for a hint about the LANGUAGE of \
the name — the exact phrase of your clue that carries it. null otherwise.

Respond with ONLY a JSON object — no reasoning before it (verify your claims \
silently; text outside the JSON is discarded and wastes your budget): \
{{"clue": "...", "taunt": "...", "angle": "<label or null>", "image_aspect": \
"<aspect or null>", "claims": [...], "language_hint": "<phrase or null>"}}"""


_RELIC_WORD_RE = re.compile(r"\b(the\s+)?relic('s)?\b", re.IGNORECASE)


def _treasure_wording(guidance: str) -> str:
    """The facet guidance is inherited from the relic engine and says "the
    relic's NAME" / "the RELIC'S ARTWORK". In this game the public word is
    THE TREASURE (Clue 1, reveal). Local to the target engine; the relic
    engine's own text is untouched."""
    def sub(m):
        art = m.group(1) or ""
        upper = m.group(0)[len(art):][:5].isupper()
        poss = ("'S" if upper else "'s") if m.group(2) else ""
        word = "TREASURE" if upper else "treasure"
        return f"{art}{word}{poss}"
    return _RELIC_WORD_RE.sub(sub, guidance)


def _small_words_note(words) -> str:
    """What the writer is told about the words of the name that are NOT the
    puzzle (10/10): which they are, that no clue is about them, and that
    word numbers are the TRUE ones — counted over every word. Empty when
    every word is a content word."""
    small = [w for w in words if not w.content]
    if not small:
        return ""
    listed = ", ".join(f"word {w.position} ('{w.text}')" for w in small)
    return (f"SMALL WORDS: {listed} — part of the name, NOT part of the puzzle. "
            "No clue is ever about them; you may write them freely. Word numbers "
            "count EVERY word of the name, these included: when you say which "
            "word a clue is about, and in \"claims\", use the number the FACET "
            "below gives.\n")


def small_words_for(ctx, clue_index: int) -> str | None:
    """The name's skeleton — "___ of a ___" — for the FIRST clue of the
    reveal phase and for no other; None when the name has nothing to show.
    The puzzle is over at that point, and what the skeleton shows was never
    the puzzle (name_words.name_skeleton)."""
    if clue_index != puzzle_clues_of(ctx) + 1:
        return None
    return name_skeleton(ctx.display_name)


# What the writer reads on clues 1-3 (Pedro, 16/10, after Hunt #22); the guard
# that holds it is early_letter_errors.
FIRST_THREE_CLUES_RULE = (
    "FIRST THREE CLUES: say NOTHING about the letters, the spelling or the length "
    "of the name or its words — no first or last letter, no vowels, no doubled "
    "letter, no word spelt inside, no count of any kind (word count included). "
    f"\"claims\" must be []. Letters may come from clue {STRUCTURE_EARLIEST} on.\n"
)


def build_target_user_message(ctx: TargetClueContext, clue_index: int,
                              prior_clues: list[str]) -> str:
    """Mirrors build_relic_user_message on the DIRECT path (no anchor angle —
    an unverified artefact is the one clue worse than a hard one). The
    target's address never appears here."""
    prior = ("\n".join(f"- {c}" for c in prior_clues) if prior_clues
             else "(none — this is the first clue)")
    vector, obliqueness = relic_slot_for(clue_index, ctx)
    angle = angle_for_unverifiable(clue_index, ctx)
    aspect = image_aspect_for(clue_index, ctx)
    spent = spent_angles(clue_index, ctx, allow_anchor=False)
    words = name_words(ctx.display_name)
    n_words = len(words)
    return (
        "The NFT's REAL attributes (point clues AT these; never write them verbatim):\n"
        f"- name ({n_words} words): {ctx.display_name}\n"
        # hunt #40: the writer counted the "&" by itself (Pedro, 24/11)
        f"- its words, numbered the ONE way every clue counts them: "
        f"{numbered_words(ctx.display_name)} (a symbol such as '&' is not a word "
        "and takes no number; when a clue says which word, it uses THIS number)\n"
        f"- artwork: {ctx.image_description}\n"
        f"- lore (on-chain description, FLAVOUR ONLY, never quote it): {ctx.lore or '(none)'}\n"
        "\n"
        f"Terms to NEVER write: {ctx.solution_terms}\n"
        + _small_words_note(words) + "\n"
        f"This is clue #{clue_index}. Target obliqueness: {obliqueness}.\n"
        + (
            f"PUZZLE PIECE {clue_index} of {puzzle_clues_of(ctx)}: this clue is one piece "
            "of the puzzle, not the answer. Give ONE new constraint on the target "
            "— a single angle nobody could turn into the word by itself, but which "
            "a solver can CHECK against a candidate. No synonym lists, no "
            "explanations, no 'the word means…'.\n"
            if clue_index <= puzzle_clues_of(ctx) else ""
        )
        + (f"ANGLE FOR THIS PIECE (use THIS one, not another, and declare its label "
           f"as \"angle\"): {angle}\n" if angle else "")
        + (f"ASPECT FOR THIS ART PIECE (declare it as \"image_aspect\"): {aspect} — "
           f"{IMAGE_ASPECTS[aspect]}. The other art piece of this hunt takes a "
           "DIFFERENT aspect; stay inside yours.\n" if aspect else "")
        + ("ALREADY SPENT on this word — do NOT repeat these angles: "
           + ", ".join(spent) + "\n" if spent else "")
        + (FIRST_THREE_CLUES_RULE if clue_index < STRUCTURE_EARLIEST else "")
        + hyphen_note_for(ctx, clue_index)
        + (
            "ENUMERABLE WORD(S) IN THIS NAME: " + ", ".join(ctx.enumerable_words)
            + ". These belong to a set a player can list out loud. NEVER let a "
            "clue gesture at the CATEGORY — attack these words ONLY by cultural "
            + ("use or their relation to the other words.\n"
               if clue_index < STRUCTURE_EARLIEST else
               "use, structure, or their relation to the other words.\n")
            if clue_index <= puzzle_clues_of(ctx) and ctx.enumerable_words else ""
        )
        + f"FACET for this clue: {vector} — {_treasure_wording(relic_guidance_for(vector, ctx, clue_index))}\n"
        # the first clue of the reveal points at a name not in English (06/11)
        + language_rule_for(ctx, clue_index)
        + f"Previous clues:\n{prior}\n\n"
        f"Write clue #{clue_index}."
    )


def hyphen_note_for(ctx, clue_index: int) -> str:
    """What the writer is told when THIS clue is about a hyphenated word
    (Pedro, 24/11, hunt #41). Empty otherwise."""
    w = _facet_word(ctx, clue_index)
    if w is None or not w.hyphenated:
        return ""
    parts = " + ".join(f"'{p}'" for p in w.parts)
    return (f"HYPHENATED WORD: word {w.position} ('{w.text}') is ONE word made of "
            f"parts joined by a hyphen ({parts}). This clue — like every clue about "
            "it, from clue 1 on, the first-three rule notwithstanding — says PLAINLY "
            "that the word is hyphenated (use the word \"hyphenated\"; never "
            "\"compound\" alone, never \"two words joined\": the guard refuses those) "
            f"and declares {{\"type\": \"hyphenated\", \"word\": {w.position}}}. "
            "Never describe one part as if it were the whole word: if you point at "
            "one part, say which (before or after the hyphen), and across this "
            "word's clues point at BOTH parts.\n")


# --------------------------------------------------------------------------- #
# Engine                                                                       #
# --------------------------------------------------------------------------- #


# ClueGuardUnavailable lives in content.relic_clues (shared base: the solver
# outage at clue 1 raises it too — Opus P1-4). Raised, not returned as
# feedback: fail-closed on EVERY clue — a leaked or false piece is forever, a
# delayed one is a non-event. integration.clue_failed turns it into the hold
# (deadline frozen, ramp stopped). Never carries the clue.


class SearchGuardUnavailable(ClueGuardUnavailable):
    """The search guard could not verify a puzzle-phase clue (blind canary or
    transport)."""


class SearchIndexBlind(SearchGuardUnavailable):
    """The canary failed: the marketplace index does not surface THIS piece
    even searched by its own on-chain name.

    A subclass on purpose. Mid-hunt the ramp must treat it like any other
    guard outage — hold, freeze the deadline, call the operator — because a
    live hunt is never ended by a guard. At /prepare, where nothing is at
    stake yet, it means something sharper: this target can never be cleared
    by this guard, so it is dropped and the next candidate gets its turn.
    Keeping it would mean re-testing the same blind piece for ever."""


class TruthJudgeUnavailable(ClueGuardUnavailable):
    """The consistency judge could not answer (API down / malformed)."""


# --------------------------------------------------------------------------- #
# The CONSISTENCY JUDGE — the blind solver's inverted twin (Opus, 09/09)      #
# --------------------------------------------------------------------------- #
#
# Live test 09/09, clue 1 for "Ancient Future": "sits between what was and
# what cannot yet be measured — neither the record nor the unknown, but the
# seam between them". That is the PRESENT. The target is FUTURE — the clue
# put the answer on the side it excludes, and every guard was green: the
# blind solver is a DIFFICULTY gate (publish if it fails to guess), and a
# clue that points the wrong way makes it fail faster. Misleading and hard
# are indistinguishable from outside. So:
#   · blind solver (exists): clue, no answer — must FAIL.
#   · consistency judge (new): clue AND answer — "is this true of it?" —
#     must PASS.
# One measures difficulty, the other truth; neither can replace the other.
# It also closes the other half of declare-and-verify: prose that adds a
# structural embellishment the declaration did not carry ("the only one
# doing that job" over ANCIENT's three vowels) is judged against the word.

TRUTH_JUDGE_SYSTEM = (
    "You are the fact-checker of a word-puzzle treasure hunt. You are given the "
    "ANSWER — the name of an NFT (its words) and a description of its artwork — "
    "and ONE clue written about it. The clue is meant to be hard, oblique and "
    "sideways; difficulty is NOT your concern. Your only question: taken as a "
    "statement about the answer, is the clue TRUE? A clue is consistent when the "
    "answer satisfies it under a fair reading; it is INCONSISTENT when it describes "
    "something the answer is not, asserts a property (of the word, its letters, "
    "its meaning, its cultural use, or the artwork) that the answer does not have, "
    "or points a reasoning player AWAY from the answer — e.g. a clue that defines "
    "'present' for the word 'future', or says a letter is the only vowel when the "
    "word has three. Read the clue literally first, then charitably; if the "
    "literal reading is false and a player obeying it would discard the right "
    "answer, it is inconsistent. The same holds for any EXAMPLE words the clue "
    "gives: a word offered as sharing a property with the answer must really "
    "share it, and one offered as NOT sharing it must really lack it — 'ends "
    "like X, unlike Y' is inconsistent when Y ends the same way. Never reward "
    "cleverness, never punish "
    "obscurity. You may reason BRIEFLY first (a few lines at most — checking "
    "letters, weighing a reading), then END with the JSON object on its own "
    'line: {"consistent": true|false, "reason": "<one sentence, may name the '
    'answer>"}. The JSON must be the last thing you write.'
)


@dataclass(frozen=True)
class TruthVerdict:
    consistent: bool | None        # None = the judge could not answer
    reason: str = ""


class AnthropicTruthJudge:
    """TruthJudge: check(clue, name=, word=, artwork=) -> TruthVerdict. One
    call per draft; any transport/parse trouble → consistent=None (the
    engine raises TruthJudgeUnavailable — fail-closed, like the search
    guard). The reason may name the answer: it goes to the WRITER (who
    knows it) and to our logs, never to a post."""

    name = "anthropic-truth-judge"

    def __init__(self, client, model: str, *, max_tokens: int = 900,
                 tries: int = 3, sleep=None):
        self._client = client
        self._model = model
        self._max_tokens = max_tokens
        self._tries = tries
        self._sleep = sleep or (lambda s: time.sleep(s))

    def check(self, clue: str, *, name: str, word: str | None,
              artwork: str) -> TruthVerdict:
        """Three tries over transient trouble (a 529, a truncated answer)
        before the engine fails closed — one blip must not freeze a hunt.
        The MEASURED cause travels in `reason` (R8: 'unavailable' alone told
        the operator nothing on 09/09)."""
        about = (f"the word '{word}' of the name" if word else "the ARTWORK")
        user = (f"ANSWER — name: {name}\nartwork: {artwork or '(no description)'}\n"
                f"This clue is about {about}.\n\nCLUE: {clue}\n\n"
                "Is the clue TRUE of the answer? Reason briefly if you need to, "
                "then end with the JSON object.")
        last = "no attempt"
        for attempt in range(self._tries):
            try:
                resp = self._client.messages.create(
                    model=self._model, max_tokens=self._max_tokens,
                    system=TRUTH_JUDGE_SYSTEM,
                    messages=[{"role": "user", "content": user}])
                text = "".join(getattr(b, "text", "") for b in resp.content)
                end = text.rfind("}")
                start = text.rfind("{", 0, end) if end != -1 else -1
                if start == -1:
                    raise ValueError(f"no JSON in judge answer ({len(text)} chars)")   # never the text
                doc = json.loads(text[start:end + 1])
                return TruthVerdict(bool(doc["consistent"]), str(doc.get("reason", ""))[:300])
            except Exception as e:  # noqa: BLE001 — retried, then fail closed
                # Opus P1-1 (10/09): never str(e) — an SDK error can echo the
                # request body, which starts with "ANSWER — name: <target>".
                # Type + status code (when the SDK gives one), nothing else.
                code = getattr(e, "status_code", None)
                last = type(e).__name__ + (f" (HTTP {code})" if code else "")
                if attempt < self._tries - 1:
                    self._sleep(2.0 * (attempt + 1))
        return TruthVerdict(None, last)


class TargetClueEngine(RelicClueEngine):
    """RelicClueEngine with the target prompt/context and two extra guards.

    `search_guard`: a ClueSearchGuard (required — the puzzle phase does not
    run without it; pass `search_guard=False` ONLY in offline simulations,
    never in production wiring)."""

    def __init__(self, anthropic_client, model: str, *, search_guard,
                 truth_judge, trail_verifier=None, trail_policy=None, solver=None):
        super().__init__(anthropic_client, model, trail_verifier=trail_verifier,
                         trail_policy=trail_policy, solver=solver)
        if search_guard is None:
            raise ValueError("TargetClueEngine needs a ClueSearchGuard "
                             "(search_guard=False only for offline simulation)")
        if truth_judge is None:
            raise ValueError("TargetClueEngine needs a truth judge "
                             "(truth_judge=False only for offline simulation)")
        self._search_guard: ClueSearchGuard | None = (
            None if search_guard is False else search_guard)
        self._truth_judge = None if truth_judge is False else truth_judge
        self._forbidden_hits: dict[str, int] = {}
        self._attempts = 0
        self._rejections: dict[str, int] = {}

    CLUE_ONE_ATTEMPTS = 10     # a refusal at clue 1 costs a draw; try harder there

    # the relic engine logs the blind solver's guesses and, on exhaustion,
    # the rejection reasons (they name the answer; "fica nos logs" was
    # acceptable for a relic). A target's name never reaches a log mid-hunt
    # (audit 10/09; Opus P1-3): the target engine logs the tally only.
    LOG_SOLVER_GUESSES = False
    LOG_REJECTION_REASONS = False

    def _judge_canary(self, persona) -> None:
        """R2 applied to the judge (Opus, 10/09): zero rejections in three
        runs does not tell "good judge" from "judge that always says yes".
        Before clue 1, the judge is shown two clues that are FALSE about
        the real name by construction — one structural, one semantic — and
        must reject both. If it approves either, it cannot see, and the
        launch is refused (TruthJudgeUnavailable → 'stays prepared'). Two
        calls per hunt. The canary texts never leave this process."""
        if self._truth_judge is None:
            return
        # the LAST word of the name, at its true position (name_words, 10/10:
        # by spaces, "Salt & Harbor" made Harbor "the third word")
        words = name_words(persona.display_name)
        if not words:
            return
        n, w = words[-1].position, words[-1].text
        ordinal = {1: "first", 2: "second", 3: "third"}.get(n, f"{n}th")
        canaries = []
        if len(w) >= 2:
            canaries.append(f"the {ordinal} word of the name is spelled with a single letter")
        if w.lower() not in ("zero", "nothing", "none"):
            canaries.append(f"the {ordinal} word of the name is the number zero, written out")
        for text in canaries:
            v = self._truth_judge.check(text, name=persona.display_name, word=w,
                                        artwork=persona.image_description)
            if v.consistent is None:
                raise TruthJudgeUnavailable(
                    f"consistency judge unavailable at its canary: {v.reason}")
            if v.consistent:
                raise TruthJudgeUnavailable(
                    "consistency judge FAILED its canary (approved a clue that is "
                    "false by construction) — it cannot see; not launching on it")
        logging.getLogger(__name__).info("consistency judge passed its canary (%d/%d)",
                                         len(canaries), len(canaries))

    def next_clue(self, persona, clue_index, prior_clues, *, max_attempts: int = 6):
        """GUARD PRESSURE IS MEASURED, NOT GUESSED (Pedro, 09/09: "a hunt
        must run, it cannot block"). Every published clue logs how many
        attempts it took and which guard refused what; a clue that fails
        every attempt logs the same tally. That tally — not opinion — is
        what decides whether a guard is too tight."""
        self._forbidden_hits = {}
        self._attempts = 0
        self._rejections: dict[str, int] = {}
        if clue_index == 1:
            max_attempts = max(max_attempts, self.CLUE_ONE_ATTEMPTS)
            self._judge_canary(persona)
        log = logging.getLogger(__name__)
        try:
            draft = super().next_clue(persona, clue_index, prior_clues,
                                      max_attempts=max_attempts)
        except Exception as e:
            # Every REFUSED attempt is in the tally. One that a guard of ours
            # could not judge (or the API dropped) was not refused: it is
            # named apart, by its type, so the numbers always add up.
            cut = ("" if sum(self._rejections.values()) >= self._attempts
                   else f"; attempt {self._attempts} cut short ({type(e).__name__})")
            log.warning("clue #%s: NOT produced after %s attempts — %s%s",
                        clue_index, self._attempts, self._tally(), cut)
            raise
        if self._attempts > 1:
            log.warning("clue #%s: published after %s attempts — %s",
                        clue_index, self._attempts, self._tally())
        self.last_attempt_report = (self._attempts, dict(self._rejections))
        return draft

    # Tally labels for what is counted outside `_post_guardrail_reasons`.
    TALLY_ANSWER_TERM = "answer term (1b)"
    TALLY_TEXT_RULES = "text rules"
    TALLY_MALFORMED = "malformed answer"

    def _tally(self) -> str:
        """Every refused attempt, by the guard that refused it — COUNTED where
        it happens. Until 09/10 the text rules were worked out by subtraction
        ("attempts − 1 − the rest"), which is right when the last attempt was
        published and one short when the whole round failed: hunt #17 logged
        six attempts whose counts added up to five."""
        return (", ".join(f"{k} ×{v}" for k, v in self._rejections.items())
                or "no rejections")

    def _count(self, guard: str) -> None:
        self._rejections[guard] = self._rejections.get(guard, 0) + 1

    def _reject(self, guard: str, reasons: list[str]) -> list[str]:
        self._count(guard)
        return reasons

    def _note_text_rejection(self, result) -> None:
        """A draft the text rules refused. Rule 1b — a term of the ANSWER
        written in the clue — gets its own counter (hunt #17: five rounds
        failed under "text rules" and the log could not say whether it was
        this rule). The number only; the term names the answer. One refused
        attempt, one count: a draft that broke 1b and something else is
        counted as 1b."""
        self._count(self.TALLY_ANSWER_TERM if getattr(result, "answer_terms", 0)
                    else self.TALLY_TEXT_RULES)

    def _guardrail_kwargs(self, persona, clue_index):
        # Rule 1b for SHORT answer terms (09/10): refused as a word, not as a
        # substring (guardrails.SHORT_TERM_LEN). Target hunts only — a
        # target's name is whatever its artist called it, short words and all.
        return {**super()._guardrail_kwargs(persona, clue_index),
                "short_terms_whole_word": True}

    def _post_guardrail_reasons(self, draft, persona, clue_index, prior_clues):
        # 1. the address of the answer, mechanically, every phase
        words = forbidden_address_words(draft.text)
        if words:
            # Opus (06/09, Q3): a ramp fighting the same term attempt after
            # attempt ("the base of the…") must be visible in the log, not
            # discovered by accident when the generator exhausts its tries.
            # THE WORD ITSELF NEVER (09/10): the chain and the platform are
            # part of the answer, and this line used to print it. The count
            # says the same thing.
            for w in words:
                self._forbidden_hits[w] = self._forbidden_hits.get(w, 0) + 1
            repeated = max(self._forbidden_hits[w] for w in words)
            if repeated >= 2:
                logging.getLogger(__name__).warning(
                    "clue #%s: generator hit the same forbidden address word "
                    "%s times in this clue's attempts", clue_index, repeated)
            return self._reject("address words", ["the clue names a blockchain or a platform (" +
                    ", ".join(words) + ") — the chain and the platform are part "
                    "of the ANSWER; remove every such word and any allusion to "
                    "them"])
        # 2. nothing about the letters on clues 1-3 (Pedro, 16/10, Hunt #22) —
        #    before the structural check, so the writer hears "no letters"
        #    and not "make the letter true"
        early = early_letter_errors(draft, clue_index)
        if early:
            logging.getLogger(__name__).warning(
                "clue #%s: letter talk before clue %s rejected", clue_index, STRUCTURE_EARLIEST)
            return self._reject("early letters", early)
        # 2b. the word numbers the prose states — the one reading (Pedro,
        #     24/11, hunt #40: the "&" was word 2 in one clue and nothing after)
        facet, _ = relic_slot_for(clue_index, persona)
        numbering = word_position_errors(draft.text, persona.display_name, facet)
        if numbering:
            logging.getLogger(__name__).warning(
                "clue #%s: a word number off the one reading rejected", clue_index)
            return self._reject("word numbering", [
                "the clue numbers the words of the name its own way — "
                + " | ".join(numbering)])
        # 3. structural claims about the name's string — true, or gone
        #    (Opus, live test 09/09: "word two is a compound" — it was FUTURE)
        structural = structural_claim_errors(draft.text, persona.display_name)
        if structural:
            logging.getLogger(__name__).warning(
                "clue #%s: false/unverifiable structural claim rejected (%d)",
                clue_index, len(structural))
            return self._reject("structural", ["the clue makes a claim about the NAME'S LETTERS that is false "
                    "or unverifiable — a player who obeys it walks AWAY from the "
                    "answer. " + " | ".join(structural) + ". Rewrite without any "
                    "false structural claim; if you cannot verify a claim on the "
                    "actual letters, do not make it"])
        # 4. declare-and-verify: angle/aspect as assigned, every claim true,
        #    every structural statement declared, no synonym lists
        decl = declaration_errors(draft, persona, clue_index)
        if decl:
            logging.getLogger(__name__).warning(
                "clue #%s: declaration check rejected (%d)", clue_index, len(decl))
            return self._reject("declaration", ["DECLARE-AND-VERIFY failed: " + " | ".join(decl)])
        # 4b. the language of the name (Pedro, 06/11): on the first clue of
        #     the reveal, the hint is in the clue and names nothing too plainly
        language = language_hint_errors(draft, persona, clue_index)
        if language:
            return self._reject("language", language)
        # 5. the consistency judge — clue + answer, must be TRUE (every phase)
        if self._truth_judge is not None:
            facet, _ = relic_slot_for(clue_index, persona)
            word = None
            if facet.startswith("name_word_"):
                # the facet's number is the word's TRUE position (10/10)
                hit = word_at(persona.display_name, int(facet.rsplit("_", 1)[1]))
                word = hit.text if hit else None
            v = self._truth_judge.check(draft.text, name=persona.display_name,
                                        word=word, artwork=persona.image_description)
            if v.consistent is None:
                raise TruthJudgeUnavailable(
                    f"consistency judge unavailable for clue #{clue_index}: "
                    f"{v.reason} — not publishing (fail-closed)")
            if not v.consistent:
                logging.getLogger(__name__).warning(
                    "clue #%s: consistency judge rejected the draft", clue_index)
                return self._reject("judge", ["the CONSISTENCY JUDGE read this clue next to the answer and "
                        f"found it FALSE or pointing away from it: {v.reason}. A "
                        "player who reasons correctly must arrive at the answer, not "
                        "be pushed off it. Rewrite so the clue is TRUE of the word "
                        "under a literal reading — hard is fine, wrong is not"])
        # 6. the search guard, puzzle phase only
        if self._search_guard is not None and clue_index <= puzzle_clues_of(persona):
            v = self._search_guard.check(
                draft.text, target_item_id=persona.target_id,
                target_name_onchain=persona.name_onchain)
            if not v.ok and v.found is None:
                # `blind` = the canary failed (this piece is invisible to
                # the index); anything else = the marketplace did not answer
                err = (SearchIndexBlind if getattr(v, "blind", False)
                       else SearchGuardUnavailable)
                raise err(f"search guard unverifiable for clue #{clue_index}: "
                          f"{v.detail} — not publishing (fail-closed)")
            if not v.ok:
                return self._reject("search guard", ["a SEARCH GUARD typed this clue into a marketplace search "
                        "and the target came up — the piece IS a search. Remove "
                        "any literal description of the picture or phrase that "
                        "could appear in a title; attack from the assigned angle "
                        "only"])
        # 7. the blind solver (inherited)
        solver = super()._post_guardrail_reasons(draft, persona, clue_index, prior_clues)
        return self._reject("solver", solver) if solver else solver

    def generate(self, persona, clue_index, prior_clues, *, feedback=None):
        obliqueness = relic_slot_for(clue_index, persona)[1]
        system = TARGET_SYSTEM_PROMPT.format(
            index=clue_index, obliqueness=obliqueness, hard_floor=HARD_CLUE_FLOOR,
            phase_rules=(PUZZLE_PHASE_RULES if clue_index <= puzzle_clues_of(persona)
                         else REVEAL_PHASE_RULES),
        )
        user = build_target_user_message(persona, clue_index, prior_clues)
        if feedback:
            user += "\n\n" + feedback
        # 4th --real-clues (09/09): asked for a STRUCTURE claim, the model
        # reasoned out loud ("Let me verify facts about…"), spent the 512
        # tokens and never reached the JSON — a ValueError escaped the
        # regeneration loop and the ROUND was skipped. Budget doubled, and a
        # malformed answer got ONE pointed retry in here.
        #
        # Hunt #17, clue 4: the retry's answer could not be read either
        # ("Extra data": two objects) and the ValueError still killed the
        # round, with attempts left. An answer that cannot be read is now ONE
        # ATTEMPT of the round like any other refusal: it is counted, the
        # next attempt carries the reminder, and only a round with no attempt
        # left ends.
        self._attempts += 1
        resp = self._client.messages.create(
            model=self._model, max_tokens=1024, system=system,
            messages=[{"role": "user", "content": user}],
        )
        text = "".join(b.text for b in resp.content if getattr(b, "type", "") == "text")
        try:
            return parse_target_clue(text)
        except ValueError as e:
            logging.getLogger(__name__).warning(
                "clue #%s: the writer's answer could not be read (%s) — one "
                "attempt spent", clue_index, type(e).__name__)
            self._count(self.TALLY_MALFORMED)
            # `from None`: a JSON error carries the whole answer in `.doc`
            raise MalformedAnswer(f"unreadable answer ({len(text)} chars)",
                                  reminder=ONE_OBJECT_REMINDER) from None
