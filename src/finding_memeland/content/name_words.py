"""THE WORDS OF A NAME — one reading, for the plan, the clues and the guard.

10/10. Until now the words of a name were counted in six places, six ways:

  · the puzzle plan            by spaces                ("&" and "7" were words)
  · the facet guidance         by spaces
  · the writer's word count    by spaces
  · the blind solver           runs of ASCII letters
  · the structural guard       runs of letters and apostrophes (a hyphen split)
  · the never-write terms      runs of 2+ letters, minus the function words

and they disagreed. In "Self-Portrait at Dawn" the plan's third word was
"Dawn" and the guard's was "at": a TRUE claim about "Dawn", declared as word
3 exactly as the writer was told to, was refused as false. In "Salt & Harbor"
the "&" was given two of the seven puzzle pieces. And a function word was a
plan word like any other: in Hunt #17 ("Chasing the Doge") "the" took two
pieces and the artwork lost one of its two.

ONE READING NOW (Pedro, 10/10):

  WORD      a space-separated token with at least one letter. "&", "7" and
            "—" are not words; "Self-Portrait" is one. POSITIONS are counted
            over the words, the way a reader counts them on the piece's own
            page — never renumbered: in "Portrait of a Lady", "Lady" is the
            4th word, and a clue that says so is true.

  CONTENT   a word that carries the answer — it gives at least one term to
            the never-write list: a run of 2+ letters that is not a function
            word. Only content words get puzzle pieces and reveal clues.
            "of" is not content (a function word); "a" is not (one letter,
            which was never a term); "for" IS — the function words are
            exactly the eight below and no others, because "for", "with",
            "by", "from" can carry the meaning of a name.

  The word COUNT a clue may state is the count of words, content or not.

What is not content is not the puzzle: the writer may write it, and the
first clue of the reveal phase shows it to everyone (`name_skeleton`).
"""

from __future__ import annotations

import re
from dataclasses import dataclass

# Function words are not the answer (07/10). The never-write list is matched
# as a SUBSTRING (guardrails 1b), so a function word in a name refused every
# clue that merely contained it inside another word — "there", "often",
# "into", "water". Exactly these eight (Pedro, 07/10 and again 10/10).
FUNCTION_WORDS = frozenset({"the", "an", "of", "and", "in", "on", "to", "at"})

_RUN = re.compile(r"[A-Za-zÀ-ÿ]+")
_TERM_MIN = 2                       # a term has 2+ letters (selector._WORD)

# What stands for a content word in the public skeleton. A fixed width: the
# length of a word is a count, and counts are not given away.
BLANK = "___"


@dataclass(frozen=True)
class NameWord:
    position: int               # 1-based, over the WORDS of the name
    text: str                   # the token as the name writes it
    runs: tuple[str, ...]       # its runs of letters, lower-cased
    content: bool               # it carries the answer

    @property
    def letters(self) -> str:
        """Its letters and nothing else — what a claim about the spelling
        (first letter, last letter, palindrome) is checked against."""
        return "".join(self.runs)


def _runs(token: str) -> tuple[str, ...]:
    return tuple(r.lower() for r in _RUN.findall(token))


def answer_terms(name: str) -> list[str]:
    """The terms of the NAME a clue may never write, in order of appearance:
    every run of 2+ letters, minus the function words. A name made ONLY of
    function words keeps them all — an empty list would let a clue write the
    whole answer."""
    named = [r for token in str(name or "").split() for r in _runs(token)
             if len(r) >= _TERM_MIN]
    return [t for t in named if t not in FUNCTION_WORDS] or named


def name_words(name: str) -> tuple[NameWord, ...]:
    """Every word of the name, with its true position and whether it is
    content. Tokens without a letter are not words and take no position."""
    terms = set(answer_terms(name))
    out: list[NameWord] = []
    for token in str(name or "").split():
        runs = _runs(token)
        if not runs:
            continue
        out.append(NameWord(position=len(out) + 1, text=token, runs=runs,
                            content=any(r in terms for r in runs)))
    return tuple(out)


def content_words(name: str) -> tuple[NameWord, ...]:
    return tuple(w for w in name_words(name) if w.content)


def word_at(name: str, position: int) -> NameWord | None:
    """The word at a TRUE position (1-based), or None."""
    words = name_words(name)
    return words[position - 1] if 0 < position <= len(words) else None


def name_skeleton(name: str) -> str | None:
    """The name with every content word blanked and everything else as it is
    written — "___ of a ___". None when there is nothing to show (every
    token is a content word).

    Public from the first clue of the reveal phase: what it shows was never
    the puzzle. It is not on the never-write list, so a clue could always
    have written it; and without it a player who has worked out both content
    words still has to guess what sits between them."""
    terms = set(answer_terms(name))
    shown = False
    parts: list[str] = []
    for token in str(name or "").split():
        if any(r in terms for r in _runs(token)):
            parts.append(BLANK)
        else:
            parts.append(token)
            shown = True
    return " ".join(parts) if shown and BLANK in parts else None
