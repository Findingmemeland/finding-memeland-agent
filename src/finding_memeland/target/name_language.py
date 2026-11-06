"""THE LANGUAGE OF THE NAME (Pedro, 05/11 — decided 06/11, after hunt #31).

Hunt #31's name was German and no clue said so: the five that went out gave
its meanings as English concepts (questions / and / answers) — fair
complaints. Measured over the 21 closed target hunts (#11–#31): only #16
(Japanese) pointed at the language, on the writer's own initiative; #21
(French) was won at clue 2, before any clue could. There was no rule.

THE RULE (D1 (a)): when the name is not in English, the FIRST CLUE OF THE
REVEAL PHASE points at its language — sideways, never by the language's
name, its own name for itself or its country. That clue has no blind solver
and no search guard: it cannot be refused for being easy.

MEASURED AT /prepare: one call, with the name and nothing else, sealed with
the preparation and then with the hunt (a restart keeps it). The report says
"sim / não / não medida (causa)" — never the language. Without a
measurement (a preparation from before, or the call failed) the same clue
gets the rule as a condition: "if the name is not in English…".

The writer declares the phrase that carries the hint ("language_hint"); a
mechanical guard checks the phrase is in the clue and that no too-obvious
word is.
"""
from __future__ import annotations

import json
import re
import time
import unicodedata
from dataclasses import dataclass

from ..content.relic_clues import puzzle_clues_of

MAX_OBVIOUS = 12          # too-obvious words kept from the model's answer


def language_clue_of(ctx) -> int:
    """The clue that carries the hint: the first of the reveal phase (D1 (a))."""
    return puzzle_clues_of(ctx) + 1


def _norm(text: str) -> str:
    """Lowercase, accents off, every run of non-letters one space: the one
    reading of a clue, a declared phrase and a banned word."""
    s = unicodedata.normalize("NFKD", str(text or ""))
    s = "".join(ch for ch in s if not unicodedata.combining(ch)).lower()
    return re.sub(r"[\W_]+", " ", s).strip()


def _names(clue: str, word: str) -> bool:
    """Does the (normalised) clue write this (normalised) word — as a word,
    a plural or a possessive? A word in another script: anywhere."""
    if not word:
        return False
    if not word.isascii():
        return word in clue
    pat = (r"(?<![a-z0-9])" + r"\s+".join(map(re.escape, word.split()))
           + r"(?:s|es)?(?:\s+s)?(?![a-z0-9])")
    return re.search(pat, clue) is not None


@dataclass(frozen=True)
class NameLanguage:
    """What /prepare measured. `language` is in English ("German"), empty
    when the name is English; `obvious` = the words that would name it too
    plainly. SECRET like the rest of the preparation: about the answer."""

    english: bool
    language: str = ""
    obvious: tuple[str, ...] = ()

    def banned(self) -> tuple[str, ...]:
        """What the hint may not write: the language and the obvious words,
        normalised, without repeats."""
        out: list[str] = []
        for w in (self.language, *self.obvious):
            n = _norm(w)
            if n and n not in out:
                out.append(n)
        return tuple(out)

    def to_doc(self) -> dict:
        return {"english": self.english, "language": self.language,
                "obvious": list(self.obvious)}

    @classmethod
    def from_doc(cls, doc) -> NameLanguage | None:
        """A sealed measurement back — None for anything else (a preparation
        or a hunt sealed before 06/11 has none)."""
        try:
            return parse_language(doc)
        except ValueError:
            return None


def parse_language(doc) -> NameLanguage:
    """The model's answer (or a sealed one) → NameLanguage. ValueError on any
    other shape — never the text in the message: it is about the name."""
    if not isinstance(doc, dict) or not isinstance(doc.get("english"), bool):
        raise ValueError("no boolean 'english'")
    if doc["english"]:
        return NameLanguage(english=True)
    language = doc.get("language")
    if not isinstance(language, str) or not language.strip():
        raise ValueError("not English, and no language named")
    obvious = tuple(w.strip()[:40] for w in (doc.get("obvious") or [])
                    if isinstance(w, str) and w.strip())
    return NameLanguage(english=False, language=language.strip()[:40],
                        obvious=obvious[:MAX_OBVIOUS])


def language_note(lang: NameLanguage | None, cause: str = "") -> str:
    """The /prepare report's words — never the language."""
    if lang is None:
        return f"não medida ({cause or 'sem resposta'})"
    return "não" if lang.english else "sim"


# --------------------------------------------------------------------------- #
# The measurement — one call, the name only                                    #
# --------------------------------------------------------------------------- #

LANGUAGE_SYSTEM = (
    "You tell which language an NFT's title is written in. Answer with ONE JSON "
    'object and nothing else: {"english": true or false, "language": "<the '
    'language, named in English>", "obvious": ["..."]}. "english" is true when '
    "an English reader would read the title as English — the everyday English "
    "words, names and borrowed words English uses included. When it is false, "
    '"language" is the title\'s language and "obvious" lists, in lowercase, the '
    "words that would name that language too plainly in a hint: the language's "
    "name in English and in the language itself, the adjective and the word for "
    "its people, and the main country where it is spoken, in English and in the "
    'language — with their common forms (at most 12). When "english" is true, '
    '"language" is "" and "obvious" is [].'
)


class NameLanguageUnmeasured(RuntimeError):
    """The measurement failed on our side. `kind` = the type (and HTTP
    status), never the message: an SDK error can echo the request — the
    name."""

    def __init__(self, kind: str):
        super().__init__(kind)
        self.kind = kind


def _json_object(text: str) -> dict:
    end = text.rfind("}")
    start = text.rfind("{", 0, end) if end != -1 else -1
    if start == -1:
        raise ValueError(f"no JSON in the answer ({len(text)} chars)")
    return json.loads(text[start:end + 1])


class AnthropicNameLanguage:
    """name -> NameLanguage, for /prepare. The name and nothing else goes in.
    Two tries over a blip; then NameLanguageUnmeasured."""

    def __init__(self, client, model: str, *, max_tokens: int = 300,
                 tries: int = 2, sleep=None):
        self._client = client
        self._model = model
        self._max_tokens = max_tokens
        self._tries = max(1, int(tries))
        self._sleep = sleep or (lambda s: time.sleep(s))

    def __call__(self, name: str) -> NameLanguage:
        last = "no attempt"
        for attempt in range(self._tries):
            try:
                resp = self._client.messages.create(
                    model=self._model, max_tokens=self._max_tokens,
                    system=LANGUAGE_SYSTEM,
                    messages=[{"role": "user", "content": f"TITLE: {name}"}])
                text = "".join(getattr(b, "text", "") for b in resp.content)
                return parse_language(_json_object(text))
            except Exception as e:  # noqa: BLE001 — retried, then unmeasured
                code = getattr(e, "status_code", None)
                last = type(e).__name__ + (f" HTTP {code}" if code else "")
                if attempt < self._tries - 1:
                    self._sleep(2.0)
        raise NameLanguageUnmeasured(last)


# --------------------------------------------------------------------------- #
# The clue — what the writer reads, and the guard                              #
# --------------------------------------------------------------------------- #

_SIDEWAYS = ("a place, a people, a writer, a custom, how the language is "
             "written — something a player can recognise, then check against "
             "the name")


def language_rule_for(ctx, clue_index: int) -> str:
    """The writer's instruction on the designated clue; "" on every other
    clue and for a name in English."""
    if clue_index != language_clue_of(ctx):
        return ""
    lang = getattr(ctx, "name_language", None)
    if lang is None:
        return ("LANGUAGE OF THE NAME: if the name is NOT in English, THIS clue "
                f"must point at its language, sideways — {_SIDEWAYS} — never "
                "naming the language or its country; declare, as "
                "\"language_hint\", the exact phrase of your clue that carries "
                "it. If the name is in English, \"language_hint\" is null.\n")
    if lang.english:
        return ""
    return (f"LANGUAGE OF THE NAME: the name is in {lang.language}, not English, "
            "and no clue has said so. THIS clue must point at the language, "
            f"sideways — {_SIDEWAYS}. Never write any of these words: "
            f"{', '.join(lang.banned())}. Declare, as \"language_hint\", the "
            "exact phrase of your clue that carries the hint.\n")


def language_hint_errors(draft, ctx, clue_index: int) -> list[str]:
    """The mechanical check of the designated clue: the declared phrase is
    in the clue, and no too-obvious word is. Measured not English: the
    phrase is required. Unmeasured: only what was declared is checked."""
    if clue_index != language_clue_of(ctx):
        return []
    lang = getattr(ctx, "name_language", None)
    if lang is not None and lang.english:
        return []
    hint = _norm(getattr(draft, "language_hint", None) or "")
    clue = _norm(draft.text)
    errors = []
    if lang is not None and not hint:
        errors.append("this clue MUST point at the LANGUAGE of the name and "
                      "declare the phrase that does it as \"language_hint\" — "
                      "none was declared")
    if hint and hint not in clue:
        errors.append("the declared \"language_hint\" is not in the clue — "
                      "write that phrase in the clue, word for word")
    if lang is not None:
        named = [w for w in lang.banned() if _names(clue, w)]
        if named:
            errors.append("the clue names the language too plainly ("
                          + ", ".join(named) + ") — point at it sideways: "
                          + _SIDEWAYS)
    return errors
