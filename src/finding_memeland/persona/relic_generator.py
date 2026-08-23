"""Relic generator — LLM-driven identity creation for the relic pool.

Mirrors persona/generator.py (same anthropic client, same JSON-out + validate +
regenerate pattern, same 2-word hard rule enforced in code, not just prompted),
adapted to the relic model:

- The identity is a MEME character we invent — never an established/owned meme
  (same safety line as the persona generator: original subjects only, no Pepe /
  Wojak / Doge trade dress, no living people, no trademarks).
- display name: EXACTLY TWO WORDS, distinctive, and NON-GOOGLABLE — checked live
  by an injected NameAvailability port so the name is a clean search test (a name
  that already resolves to a real thing would make the hunt trivial or ambiguous).
- Visual style is deliberately VARIED per relic (decision 2026-08-22 "camouflage
  before, brand after"): a common house style pre-reveal would let an indexer
  filter the whole pool by look and compare clues against ~100 candidates instead
  of searching the world. So each relic draws a different style directive; the
  collection identity is born only at remint (package 4).

OFFLINE + testable: the anthropic client and the NameAvailability check are
injected, so tests run against fakes with zero network.
"""

from __future__ import annotations

import json
import re
import secrets
from dataclasses import dataclass
from typing import Protocol, runtime_checkable

from .relic import RelicIdentity, new_identity


@dataclass
class GeneratedRelic:
    """Raw, validated identity fields from the model (code + salt are added by
    new_identity, never by the LLM)."""

    name: str                  # exactly two words, non-googlable
    description: str           # meme lore for the NFT description
    image_prompt: str          # includes the chosen style directive
    image_style: str           # the style bucket used (for pool-diversity audits)
    solution_terms: list[str]  # literal answer words clues must never contain

    def to_identity(self) -> RelicIdentity:
        return new_identity(
            name=self.name,
            description=self.description,
            image_prompt=self.image_prompt,
            solution_terms=self.solution_terms,
        )


@runtime_checkable
class NameAvailability(Protocol):
    """Is a candidate name a CLEAN search test — i.e. non-googlable to a real
    entity AND not already used by a relic in our pool? The real adapter (search
    + pool lookup) lands in package 2; package 1 tests inject a fake."""

    def is_available(self, name: str) -> bool: ...


# Deliberately heterogeneous visual styles so the pool has no common signature
# an indexer could filter on. One is drawn per relic (crypto-secure choice so the
# sequence isn't predictable).
STYLE_DIRECTIVES = (
    "flat vector sticker art, bold outlines, limited palette",
    "grainy 1990s photocopy zine aesthetic, high contrast black and white",
    "soft 3D clay render, pastel lighting, rounded forms",
    "pixel art, 32x32 feel, dithered shading",
    "hand-inked cartoon, loose linework, watercolor washes",
    "retro CRT / vaporwave gradients, scanlines, neon",
    "oil-painting pastiche, visible brushstrokes, museum framing",
    "childlike crayon doodle on paper, imperfect and warm",
    "brutalist collage, torn paper and photocopied textures",
    "glossy corporate mascot render, clean studio background",
)

# Original meme-flavored archetypes (OURS — never an established/owned meme).
RELIC_ARCHETYPES = (
    "an invented crypto-native creature (anon degen animal, rugged dev gremlin, "
    "diamond-hands elder, perma-bull oracle beast)",
    "an original absurd object-with-a-face given a voice",
    "a fabricated folklore creature that never existed",
    "an invented mascot for a fictional ritual or place",
    "an original anthropomorphic concept (a mood, a market condition, a bug)",
)

REGISTERS = {
    "accessible": "ACCESSIBLE: a broad crypto-Twitter audience should crack the "
    "name within a few clues. Still inferential, never a bare name drop.",
    "medium": "MEDIUM: solvable by an attentive player connecting 2-3 vectors.",
    "cerebral": "CEREBRAL: a hard puzzle for the hardcore — several combined "
    "inferences to reach the name.",
}

# Words from SMALL CLOSED SETS. A prompt rule alone is not enough here: one of
# these slipping through costs a whole hunt (mini hunt #1, 2026-08-23 — "Uncle
# Pump" fell on clue 3 in 12 minutes because any gesture at kinship collapses
# "uncle" to a handful of candidates). Enforced in code so it cannot drift.
CLOSED_CATEGORY_WORDS = frozenset(
    # kinship
    """uncle aunt auntie aunty cousin nephew niece granny grandma grandpa granddad
    grandad mother father mom mum dad daddy mommy mummy brother sister sibling son
    daughter nana papa stepdad stepmom godfather godmother"""
    # colours
    """ red orange yellow green blue indigo violet purple pink brown black white
    grey gray beige teal cyan magenta maroon"""
    # numbers
    """ zero one two three four five six seven eight nine ten eleven twelve
    thirteen twenty thirty forty fifty hundred thousand million billion first
    second third fourth fifth"""
    # days / months / seasons
    """ monday tuesday wednesday thursday friday saturday sunday january february
    march april may june july august september october november december spring
    summer autumn fall winter"""
    # directions / planets
    """ north south east west northern southern eastern western mercury venus
    earth mars jupiter saturn uranus neptune pluto""".split()
)


SYSTEM_PROMPT = """You invent original MEME characters for "Finding Memeland", an \
AI-run onchain treasure hunt. Each character becomes a 1/1 NFT (a "relic") that \
players must identify by INFERENCE from oblique clues, then find by NAME on NFT \
marketplaces/explorers. So the identity needs texture: a distinctive, searchable \
two-word name and lore a clue can hint at without stating.

Hard rules:
- ORIGINAL creations only. FORBIDDEN: established/owned memes and their trade \
dress (Pepe / "feels good man", Wojak, the Doge photo), real living people, \
trademarks, modern IP-held characters. Generic ORIGINAL subjects are fine, \
including original frogs in your own distinct style — just never a clone of a \
known character.
- name: EXACTLY TWO WORDS (hard rule — marketplace name search behaves best on \
short distinctive names). Max 50 chars, plain characters. DISTINCTIVE and \
NON-GOOGLABLE: an invented pairing that does NOT already resolve to a real \
person, brand, place, or thing. Vary the style (adjective+noun, noun+noun, \
lowercase pairs); never default to "The ___".
- PUZZLE DEPTH (critical — this is what makes the hunt last): EACH of the two \
words must support at least THREE independent clue angles: how it SOUNDS, where it \
COMES FROM, the SEMANTIC FIELD it sits in, where a person MEETS it (a saying, a \
job, an object, a scene), how it is BUILT, and how it plays against the OTHER word. \
The test is RICHNESS OF ASSOCIATION, not fancy vocabulary. A plain, funny, everyday \
word is often the RICHEST: "rat" gives you sinking ships, the rat race, a snitch, \
sewers, the plague, lab rats — six angles from three letters. What FAILS is a word \
with only ONE association: "verdant" means green and nothing else, so every clue \
about it collapses into the same idea. Before you answer, check each word: can I \
write three genuinely different clues about it without repeating myself?
- NO CLOSED-CATEGORY WORDS (learned the hard way). Neither word may belong to a \
SMALL CLOSED SET that a player can enumerate: family titles (uncle, aunt, cousin, \
granny, nephew...), colours, numbers, days, months, seasons, compass directions, \
planets. Such a word may be RICH in association and still be worthless as a puzzle: \
the moment any clue gestures at the category, the answer collapses to a handful of \
candidates and the hunt is over. THE REAL TEST IS: can the word be POINTED AT \
without being IDENTIFIED? A clue can circle "kettlewright" for hours and you still \
have to work; one clue about kinship hands you "uncle". Choose words that live in \
OPEN fields — things, actions, textures, creatures, states — where a hundred \
candidates fit every angle.
- STAY MEME-NATIVE, and note that meme culture is WIDE: animals and everyday \
absurdity, yes, but also fantasy, manga/anime, medieval, sci-fi, mythology — all \
of it is fair game and full of meme material. The test is NOT the genre, it is \
whether the name is FUNNY. A fantasy-flavoured name with a joke in it works \
("Gandalf the Rugged"); an earnest ornate one with no joke does not ("Bilgewright \
Tallowscript" — pretty, pretentious, not a meme). Funny first, findable second, \
never solemn.
- description: 1-3 sentences of in-character MEME lore for the NFT description. \
It must NOT state or spell out who/what the relic "really" references — that is \
the puzzle. No URLs, no hashtags, no @handles, no square brackets. (A short claim \
code is appended later by the system — do not invent one.)
- image_prompt: a vivid prompt for the relic's artwork in THIS EXACT visual \
style: "{style}". No text in the image, no real-person likeness. Make the style \
directive land — pool diversity matters.
- solution_terms: JSON array of the literal answer words/names that would solve \
the puzzle outright (the true reference and its direct give-aways). Clues never \
contain these — be thorough but do not include generic words.

Respond with ONLY a JSON object, keys: archetype, name, description, \
image_prompt, solution_terms."""


def _extract_json(text: str) -> dict:
    start = text.find("{")
    end = text.rfind("}")
    if start == -1 or end == -1 or end < start:
        raise ValueError(f"no JSON object in model response: {text[:200]!r}")
    return json.loads(text[start : end + 1])


def _clean_line(s: str) -> str:
    # Strip markup the marketplaces render literally / that could break search.
    bad = ["[", "]", "#", "@", "\n", "\r", "\t"]
    out = str(s)
    for b in bad:
        out = out.replace(b, " ")
    return " ".join(out.split()).strip()


def _validate(data: dict, *, style: str, name_check: NameAvailability) -> GeneratedRelic:
    required = {"archetype", "name", "description", "image_prompt", "solution_terms"}
    missing = required - data.keys()
    if missing:
        raise ValueError(f"relic JSON missing keys: {sorted(missing)}")

    name = _clean_line(data["name"])
    if not name:
        raise ValueError("empty name")
    if len(name.split()) != 2:
        raise ValueError(
            f"name must be exactly two words (got {len(name.split())}: {name!r})"
        )
    if len(name) > 50:
        raise ValueError(f"name too long ({len(name)} > 50): {name!r}")

    # A word from a small closed set can be POINTED AT and IDENTIFIED in the same
    # clue — it makes the hunt unwinnable-in-reverse (solved far too fast).
    # Match on LETTER RUNS, not on the whole token: stripping punctuation would
    # glue a possessive on ("tuesday's" -> "tuesdays") and slip past the set.
    closed = sorted(
        {
            run
            for run in re.findall(r"[a-z]+", name.lower())
            if run in CLOSED_CATEGORY_WORDS
        }
    )
    if closed:
        raise ValueError(
            f"name uses closed-category word(s) {closed} — a player can enumerate the "
            f"whole category, so one clue solves the word: {name!r}"
        )

    raw_terms = data["solution_terms"]
    if not isinstance(raw_terms, list):
        raise ValueError("solution_terms must be a list")
    solution_terms = [str(t).strip() for t in raw_terms if str(t).strip()]
    if not solution_terms:
        raise ValueError("solution_terms is empty — the answer must be specified")

    description = _clean_line(data["description"])
    if not description:
        raise ValueError("empty description after cleaning")
    low = description.lower()
    if any(t.lower() in low for t in solution_terms):
        raise ValueError("description leaks a solution term — it must not reveal the answer")

    # Live cleanliness gate: the name must be a clean search test. Done LAST so we
    # don't spend a lookup on a name that already failed structural checks.
    if not name_check.is_available(name):
        raise ValueError(f"name not available/non-googlable: {name!r}")

    image_prompt = _clean_line(data["image_prompt"])
    if not image_prompt:
        raise ValueError("empty image_prompt")

    return GeneratedRelic(
        name=name,
        description=description,
        image_prompt=image_prompt,
        image_style=style,
        solution_terms=solution_terms,
    )


_MAX_GENERATE_ATTEMPTS = 3


class RelicGenerator:
    """Samples an original meme identity. Same retry-on-validation pattern as the
    persona generator; a fresh style is drawn per attempt so a retry also varies
    the look."""

    def __init__(self, anthropic_client, model: str, name_check: NameAvailability):
        self._client = anthropic_client
        self._model = model
        self._name_check = name_check

    def _pick_style(self) -> str:
        return STYLE_DIRECTIVES[secrets.randbelow(len(STYLE_DIRECTIVES))]

    def _pick_archetype(self) -> str:
        return RELIC_ARCHETYPES[secrets.randbelow(len(RELIC_ARCHETYPES))]

    def generate(
        self,
        *,
        register: str | None = None,
        avoid_recent: list[str] | None = None,
    ) -> GeneratedRelic:
        reg = REGISTERS.get((register or "medium").lower(), REGISTERS["medium"])
        avoid = ", ".join(avoid_recent or []) or "(none yet)"
        last_err: Exception | None = None
        for _ in range(_MAX_GENERATE_ATTEMPTS):
            style = self._pick_style()
            system = SYSTEM_PROMPT.replace("{style}", style)
            user = (
                f"Difficulty: {reg}\n"
                f"Suggested archetype (you may adapt): {self._pick_archetype()}\n"
                f"Avoid names/themes too close to these recent relics: {avoid}\n"
                "Invent one original meme relic now."
            )
            try:
                resp = self._client.messages.create(
                    model=self._model,
                    max_tokens=900,
                    system=system,
                    messages=[{"role": "user", "content": user}],
                )
                text = resp.content[0].text if resp.content else ""
                return _validate(_extract_json(text), style=style, name_check=self._name_check)
            except Exception as e:  # noqa: BLE001 — regenerate on any validation/parse failure
                last_err = e
                continue
        raise RuntimeError(
            f"relic generation failed after {_MAX_GENERATE_ATTEMPTS} attempts: {last_err!r}"
        )
