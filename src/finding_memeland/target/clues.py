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

3. FOUR GUARDS in `_post_guardrail_reasons`, before the solver — three
   mechanical, one LLM (the consistency judge: clue + answer, must be true;
   the blind solver's inverted twin, see TRUTH_JUDGE_SYSTEM):
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
from dataclasses import dataclass
from typing import Callable, Sequence

from ..content.clue_engine import HARD_CLUE_FLOOR
from ..content.relic_clues import (
    PUZZLE_ANGLES,
    PUZZLE_CLUES,
    PUZZLE_PHASE_RULES,
    REVEAL_PHASE_RULES,
    RelicClueContext,
    RelicClueEngine,
    angle_for_unverifiable,
    enumerable_words_in,
    relic_guidance_for,
    relic_ramp_plan,
    relic_slot_for,
    spent_angles,
)
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
    r"\b(compound|portmanteau|fused|welded|two\s+(?:complete\s+|whole\s+)?words\s+"
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
_VOWELS = set("aeiou")


def _n(tok: str) -> int:
    tok = tok.lower()
    return int(tok) if tok.isdigit() else _NUM_WORDS.get(tok, 0)


def _scope_at(text: str, pos: int, words: list[str]) -> tuple[list[str], bool]:
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
    words = [w for w in re.findall(r"[A-Za-zÀ-ÿ']+", (name or "").lower())]
    if not words:
        return []
    errs: list[str] = []
    whole = "".join(words)

    def holds(pos: int, pred) -> tuple[bool, list[str]]:
        scope, any_ok = _scope_at(text, pos, words)
        ok = any(pred(w) for w in scope) or (any_ok and pred(whole))
        return ok, scope

    def false(m, pred, detail=None):
        ok, scope = holds(m.start(), pred)
        if not ok:
            errs.append(f"'{m.group(0)}' is false for "
                        + (detail(scope) if detail else ", ".join(scope)))

    m = _UNVERIFIABLE_RE.search(text)
    if m:
        errs.append(f"the clue asserts a hidden structure ('{m.group(0)}') that "
                    "cannot be checked against the string — the last such claim "
                    "was FALSE. Drop it; make only claims a reader can verify on "
                    "the letters (count, first/last letter, doubled letter) or none")
    for m in _LETTERS_RE.finditer(text):
        n = _n(m.group("n"))
        if n:
            false(m, lambda w: len(w) == n,
                  lambda scope: ", ".join(f"{w} ({len(w)} letters)" for w in scope))
    for m in _WORDS_RE.finditer(text):
        n = _n(m.group("n"))
        if n and n != len(words) and "letter" not in text[m.end():m.end() + 12].lower():
            errs.append(f"'{m.group(0)}' is false: the name has {len(words)} words")
    for m in _STARTS_RE.finditer(text):
        false(m, lambda w: _is(w[0], m))
    for m in _ENDS_RE.finditer(text):
        false(m, lambda w: _is(w[-1], m))
    for m in _FIRST_LETTER_RE.finditer(text):
        pick = (lambda w: w[0]) if m.group("pos").lower() == "first" else (lambda w: w[-1])
        false(m, lambda w: _is(pick(w), m))
    for m in _DOUBLE_RE.finditer(text):
        false(m, lambda w: any(a == b for a, b in zip(w, w[1:])))
    for m in _PALINDROME_RE.finditer(text):
        false(m, lambda w: len(w) > 1 and w == w[::-1])
    for m in _NO_VOWELS_RE.finditer(text):
        false(m, lambda w: not any(c in _VOWELS for c in w))
    for m in _CONTAINS_RE.finditer(text):
        inner = m.group("w").lower()
        false(m, lambda w: inner in w and inner != w)
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
               "no_vowels", "word_count")

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
    plan = ctx.clue_facet_plan or relic_ramp_plan(ctx.display_name)
    if clue_index > len(plan) or plan[clue_index - 1][0] != "image":
        return None
    order = list(_ASPECT_ORDER)
    random.Random(f"{ctx.display_name}|aspects").shuffle(order)
    k = sum(1 for i in range(clue_index - 1) if plan[i][0] == "image")
    return order[k % len(order)]


def _claim_scope(words: list[str], word: int) -> tuple[list[str], bool]:
    if word == 0:
        return words, True
    if 1 <= word <= len(words):
        return [words[word - 1]], False
    return [], False


def verify_claims(claims, name: str) -> list[str]:
    """Every declared claim, checked against the real spelling. Errors name
    the claim and the word (writer-facing; never public)."""
    words = re.findall(r"[A-Za-zÀ-ÿ']+", (name or "").lower())
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
        joined = "".join(scope) if whole_ok else scope[0]
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
            ok = any(v in x and v != x for x in scope) or (whole_ok and v in joined and v != joined)
            why = f"'{v}' is not spelt inside " + ", ".join(scope)
        elif t == "double_letter":
            ok = any(a == b for x in scope for a, b in zip(x, x[1:]))
            why = "no letter is doubled in " + ", ".join(scope)
        elif t == "palindrome":
            ok = any(len(x) > 1 and x == x[::-1] for x in scope) or (whole_ok and joined == joined[::-1])
            why = "nothing here reads the same backwards"
        else:  # no_vowels
            ok = any(not any(ch in _VOWELS for ch in x) for x in scope)
            why = "every word here has a vowel"
        if not ok:
            errs.append(f"claim {t} (word {w}, value {v!r}) is FALSE: {why}")
    return errs


_PROSE_DETECTORS = (                 # prose pattern → the claim type it must declare
    (_STARTS_RE, "starts"), (_ENDS_RE, "ends"), (_DOUBLE_RE, "double_letter"),
    (_PALINDROME_RE, "palindrome"), (_NO_VOWELS_RE, "no_vowels"),
    (_CONTAINS_RE, "contains"), (_WORDS_RE, "word_count"),
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


def declaration_errors(draft, ctx, clue_index: int) -> list[str]:
    """The whole declare-and-verify check for one draft (writer feedback)."""
    errs: list[str] = []
    facet, _ = relic_slot_for(clue_index, ctx)
    puzzle = clue_index <= PUZZLE_CLUES
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
    return errs


def parse_target_clue(text: str):
    """The target writer's JSON → ClueDraft with the declared data. Missing
    fields are None/[] (and then fail the declaration check with a reason)."""
    from ..content.clue_engine import ClueDraft, _strip_leading_meta
    start, end = text.find("{"), text.rfind("}")
    if start == -1 or end == -1 or end < start:
        raise ValueError(f"no JSON object in clue response: {text[:200]!r}")
    data = json.loads(text[start:end + 1])
    clue = _strip_leading_meta(str(data.get("clue", "")).strip())
    if not clue:
        raise ValueError("empty clue text")
    claims = data.get("claims") or []
    return ClueDraft(text=clue, taunt=str(data.get("taunt", "")).strip() or None,
                     angle=(str(data["angle"]).strip() if data.get("angle") else None),
                     image_aspect=(str(data["image_aspect"]).strip().lower()
                                   if data.get("image_aspect") else None),
                     claims=claims if isinstance(claims, list) else [])


# --------------------------------------------------------------------------- #
# Context                                                                      #
# --------------------------------------------------------------------------- #

_ARTIST_KEYS = ARTIST_KEYS      # one reading (R9): banned here, credited at the reveal


@dataclass
class TargetClueContext(RelicClueContext):
    """RelicClueContext plus what the target engine needs and the writer must
    never see. `target_id` and `name_onchain` feed the SEARCH GUARD only —
    they are never formatted into a prompt."""

    target_id: str = ""
    name_onchain: str = ""

    @classmethod
    def from_target(cls, target: Target, *, image_description: str,
                    metadata: dict | None = None) -> "TargetClueContext":
        """`image_description` comes from the vision step (see
        describe_image_batched); `metadata` (the full token metadata, if the
        caller has it) contributes the artist/creator name to the never-write
        list — a creator name is a search box too."""
        words = [w for w in re.findall(r"[A-Za-zÀ-ÿ]{2,}", target.name)]
        terms = [w.lower() for w in words]
        for k in _ARTIST_KEYS:
            v = (metadata or {}).get(k)
            if isinstance(v, str) and v.strip() and not v.startswith("0x"):
                terms.extend(t.lower() for t in re.findall(r"[A-Za-zÀ-ÿ]{3,}", v))
        return cls(
            display_name=target.name,
            image_description=image_description,
            lore=(target.description or "")[:400],
            backstory=(target.description or "")[:400],
            solution_terms=sorted(set(terms)),
            enumerable_words=enumerable_words_in(target.name),
            clue_facet_plan=relic_ramp_plan(target.name),
            angle_offset=sum(ord(c) for c in target.name) % len(PUZZLE_ANGLES),
            target_id=target.id(),
            name_onchain=target.name_onchain,
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


def describe_image_batched(*, target_image_url: str,
                           decoy_image_urls: Sequence[str],
                           fetch_bytes_generic: Callable[[str], bytes | None],
                           describe: Callable[[bytes], str],
                           decoys: int = 7,
                           rng: random.Random | None = None,
                           content_ok: Callable[[bytes], "bool | None"] | None = None,
                           target_id: str = "") -> str:
    """Fetch the target's image inside a shuffled batch of decoy images (from
    snapshot entries) through a generic public gateway, then describe ONLY
    the target's bytes with the vision callable (our own provider, our own
    key — that is fine; it is the marketplace/gateway that must not learn
    the target). Decoy fetch results are discarded; a decoy failure is
    noise. Raises ImageUnavailable when the target's bytes cannot be
    fetched or the description is empty.

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
    target_bytes: bytes | None = None
    for url in batch:
        try:
            data = fetch_bytes_generic(url)
        except Exception:  # noqa: BLE001 — a decoy's failure is noise
            data = None
        if url == target_image_url:
            target_bytes = data
    if not target_bytes:
        raise ImageUnavailable("artwork bytes unavailable via the generic "
                               "gateway — not launching without the art")
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
Emoji only where the phase rules below allow them.

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
included. Never call a clue "final" or "last": the ramp may continue.

DECLARE WHAT YOU DID (it is verified mechanically; a mismatch rejects the clue):
- "angle": for a NAME piece, the label of the angle you were assigned (e.g. \
"SEMANTIC FIELD") — you must write from that angle and say so; null for art \
pieces and reveal clues.
- "image_aspect": for an ART piece, the aspect you were assigned (e.g. \
"palette"); null otherwise.
- "claims": EVERY statement your clue makes about the LETTERS or SPELLING of the \
name, as data. Allowed types: "starts" / "ends" (value: a single letter, \
"vowel" or "consonant"), "contains" (value: a shorter real word spelt inside), \
"double_letter", "palindrome", "no_vowels", "word_count" (value: the number). \
"word" is 1 for the first word, 2 for the second, 0 for the whole name. Each \
claim is CHECKED against the real spelling: a false one rejects the clue. If \
your prose asserts something about the letters that is not declared, the clue \
is rejected. If a structural idea cannot be expressed as one of these types \
(compound, portmanteau, hidden word, prefix, suffix, anagram, rhyme, initials), \
you may NOT assert it — do not guess how a word is built. An empty list means \
your clue says nothing about the letters, which is the normal case.

Respond with ONLY a JSON object: {{"clue": "...", "taunt": "...", "angle": \
"<label or null>", "image_aspect": "<aspect or null>", "claims": [...]}}"""


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
    n_words = len(ctx.display_name.split())
    return (
        "The NFT's REAL attributes (point clues AT these; never write them verbatim):\n"
        f"- name ({n_words} words): {ctx.display_name}\n"
        f"- artwork: {ctx.image_description}\n"
        f"- lore (on-chain description, FLAVOUR ONLY, never quote it): {ctx.lore or '(none)'}\n"
        "\n"
        f"Terms to NEVER write: {ctx.solution_terms}\n\n"
        f"This is clue #{clue_index}. Target obliqueness: {obliqueness}.\n"
        + (
            f"PUZZLE PIECE {clue_index} of {PUZZLE_CLUES}: this clue is one piece "
            "of the puzzle, not the answer. Give ONE new constraint on the target "
            "— a single angle nobody could turn into the word by itself, but which "
            "a solver can CHECK against a candidate. No synonym lists, no "
            "explanations, no 'the word means…'.\n"
            if clue_index <= PUZZLE_CLUES else ""
        )
        + (f"ANGLE FOR THIS PIECE (use THIS one, not another, and declare its label "
           f"as \"angle\"): {angle}\n" if angle else "")
        + (f"ASPECT FOR THIS ART PIECE (declare it as \"image_aspect\"): {aspect} — "
           f"{IMAGE_ASPECTS[aspect]}. The other art piece of this hunt takes a "
           "DIFFERENT aspect; stay inside yours.\n" if aspect else "")
        + ("ALREADY SPENT on this word — do NOT repeat these angles: "
           + ", ".join(spent) + "\n" if spent else "")
        + (
            "ENUMERABLE WORD(S) IN THIS NAME: " + ", ".join(ctx.enumerable_words)
            + ". These belong to a set a player can list out loud. NEVER let a "
            "clue gesture at the CATEGORY — attack these words ONLY by cultural "
            "use, structure, or their relation to the other words.\n"
            if clue_index <= PUZZLE_CLUES and ctx.enumerable_words else ""
        )
        + f"FACET for this clue: {vector} — {relic_guidance_for(vector, ctx, clue_index)}\n"
        + f"Previous clues:\n{prior}\n\n"
        f"Write clue #{clue_index}."
    )


# --------------------------------------------------------------------------- #
# Engine                                                                       #
# --------------------------------------------------------------------------- #


class ClueGuardUnavailable(RuntimeError):
    """A guard of OURS could not verify a clue. Raised, not returned as
    feedback: fail-closed on EVERY clue — a leaked or false piece is
    forever, a delayed one is a non-event. integration.clue_failed turns it
    into the hold (deadline frozen, ramp stopped). Never carries the clue."""


class SearchGuardUnavailable(ClueGuardUnavailable):
    """The search guard could not verify a puzzle-phase clue (blind canary or
    transport)."""


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
    "answer, it is inconsistent. Never reward cleverness, never punish "
    "obscurity. Answer ONLY a JSON object: "
    '{"consistent": true|false, "reason": "<one sentence, may name the answer>"}'
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

    def __init__(self, client, model: str, *, max_tokens: int = 200):
        self._client = client
        self._model = model
        self._max_tokens = max_tokens

    def check(self, clue: str, *, name: str, word: str | None,
              artwork: str) -> TruthVerdict:
        about = (f"the word '{word}' of the name" if word else "the ARTWORK")
        user = (f"ANSWER — name: {name}\nartwork: {artwork or '(no description)'}\n"
                f"This clue is about {about}.\n\nCLUE: {clue}\n\n"
                "Is the clue TRUE of the answer?")
        try:
            resp = self._client.messages.create(
                model=self._model, max_tokens=self._max_tokens,
                system=TRUTH_JUDGE_SYSTEM,
                messages=[{"role": "user", "content": user}])
            text = "".join(getattr(b, "text", "") for b in resp.content)
            start, end = text.find("{"), text.rfind("}")
            doc = json.loads(text[start:end + 1])
            return TruthVerdict(bool(doc["consistent"]), str(doc.get("reason", ""))[:300])
        except Exception:  # noqa: BLE001 — the engine fails closed on None
            return TruthVerdict(None, "judge unavailable")


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

    def next_clue(self, persona, clue_index, prior_clues, *, max_attempts: int = 6):
        self._forbidden_hits = {}
        return super().next_clue(persona, clue_index, prior_clues,
                                 max_attempts=max_attempts)

    def _post_guardrail_reasons(self, draft, persona, clue_index, prior_clues):
        # 1. the address of the answer, mechanically, every phase
        words = forbidden_address_words(draft.text)
        if words:
            # Opus (06/09, Q3): a ramp fighting the same term attempt after
            # attempt ("the base of the…") must be visible in the log, not
            # discovered by accident when the generator exhausts its tries.
            for w in words:
                self._forbidden_hits[w] = self._forbidden_hits.get(w, 0) + 1
                if self._forbidden_hits[w] >= 2:
                    logging.getLogger(__name__).warning(
                        "clue #%s: generator hit forbidden address word %r "
                        "%s times in this clue's attempts", clue_index, w,
                        self._forbidden_hits[w])
            return ["the clue names a blockchain or a platform (" +
                    ", ".join(words) + ") — the chain and the platform are part "
                    "of the ANSWER; remove every such word and any allusion to "
                    "them"]
        # 2. structural claims about the name's string — true, or gone
        #    (Opus, live test 09/09: "word two is a compound" — it was FUTURE)
        structural = structural_claim_errors(draft.text, persona.display_name)
        if structural:
            logging.getLogger(__name__).warning(
                "clue #%s: false/unverifiable structural claim rejected (%d)",
                clue_index, len(structural))
            return ["the clue makes a claim about the NAME'S LETTERS that is false "
                    "or unverifiable — a player who obeys it walks AWAY from the "
                    "answer. " + " | ".join(structural) + ". Rewrite without any "
                    "false structural claim; if you cannot verify a claim on the "
                    "actual letters, do not make it"]
        # 3. declare-and-verify: angle/aspect as assigned, every claim true,
        #    every structural statement declared, no synonym lists
        decl = declaration_errors(draft, persona, clue_index)
        if decl:
            logging.getLogger(__name__).warning(
                "clue #%s: declaration check rejected (%d)", clue_index, len(decl))
            return ["DECLARE-AND-VERIFY failed: " + " | ".join(decl)]
        # 4. the consistency judge — clue + answer, must be TRUE (every phase)
        if self._truth_judge is not None:
            facet, _ = relic_slot_for(clue_index, persona)
            word = None
            if facet.startswith("name_word_"):
                n = int(facet.rsplit("_", 1)[1])
                ws = persona.display_name.split()
                word = ws[n - 1] if 0 < n <= len(ws) else None
            v = self._truth_judge.check(draft.text, name=persona.display_name,
                                        word=word, artwork=persona.image_description)
            if v.consistent is None:
                raise TruthJudgeUnavailable(
                    f"consistency judge unavailable for clue #{clue_index}: "
                    f"{v.reason} — not publishing (fail-closed)")
            if not v.consistent:
                logging.getLogger(__name__).warning(
                    "clue #%s: consistency judge rejected the draft", clue_index)
                return ["the CONSISTENCY JUDGE read this clue next to the answer and "
                        f"found it FALSE or pointing away from it: {v.reason}. A "
                        "player who reasons correctly must arrive at the answer, not "
                        "be pushed off it. Rewrite so the clue is TRUE of the word "
                        "under a literal reading — hard is fine, wrong is not"]
        # 5. the search guard, puzzle phase only
        if self._search_guard is not None and clue_index <= PUZZLE_CLUES:
            v = self._search_guard.check(
                draft.text, target_item_id=persona.target_id,
                target_name_onchain=persona.name_onchain)
            if not v.ok and v.found is None:
                raise SearchGuardUnavailable(
                    f"search guard unverifiable for clue #{clue_index}: "
                    f"{v.detail} — not publishing (fail-closed)")
            if not v.ok:
                return ["a SEARCH GUARD typed this clue into a marketplace search "
                        "and the target came up — the piece IS a search. Remove "
                        "any literal description of the picture or phrase that "
                        "could appear in a title; attack from the assigned angle "
                        "only"]
        # 6. the blind solver (inherited)
        return super()._post_guardrail_reasons(draft, persona, clue_index, prior_clues)

    def generate(self, persona, clue_index, prior_clues, *, feedback=None):
        obliqueness = relic_slot_for(clue_index, persona)[1]
        system = TARGET_SYSTEM_PROMPT.format(
            index=clue_index, obliqueness=obliqueness, hard_floor=HARD_CLUE_FLOOR,
            phase_rules=(PUZZLE_PHASE_RULES if clue_index <= PUZZLE_CLUES
                         else REVEAL_PHASE_RULES),
        )
        user = build_target_user_message(persona, clue_index, prior_clues)
        if feedback:
            user += "\n\n" + feedback
        resp = self._client.messages.create(
            model=self._model, max_tokens=512, system=system,
            messages=[{"role": "user", "content": user}],
        )
        text = "".join(b.text for b in resp.content if getattr(b, "type", "") == "text")
        return parse_target_clue(text)
