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
    # The axes this identity was drawn on. `archetype` is the model's own one-line
    # summary of the character — it is what feeds `avoid_recent` next time, so the
    # pool stops repeating THEMES and not just names.
    archetype: str = ""
    domains: tuple[str, ...] = ()
    tone: str = ""
    register: str = ""
    # Words in the name that a player could enumerate (kinship, colours, days,
    # crypto jargon...). NOT a defect — a signal for the clue writer: for these
    # words, never gesture at the category.
    enumerable_words: tuple[str, ...] = ()

    def theme_tag(self) -> str:
        """Compact 'what this one was' line for the anti-repetition list."""
        parts = [p for p in (self.name, self.tone, self.archetype) if p]
        return " — ".join(parts)

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

# Two ORTHOGONAL axes (Pedro, 2026-08-23), replacing the old free-form archetype
# list. That list leaned crypto-native and produced a monoculture: in a 17-name
# sample, SIX were the same stoic-animal-that-never-sells, two sharing the phrase
# "since the last ice age". Handing the model a fixed (domain x tone) pair forces
# the spread instead of hoping for it.
#
# A name may draw ONE word from each of two domains ("Napoleon Toad" = history x
# animal) — Pedro's idea, and the better one: it makes the two clue tracks demand
# different kinds of knowledge.
# MEME is NOT one of these — it is mandatory in every single name (Pedro,
# 2026-08-23, after the first axes sample came back beautiful and completely
# unfunny: "hollow quorum", "latent vigil", "charnel proxy"). Every relic is
# MEME x ONE of these worlds. "internet folklore" was dropped from the list
# because it now applies to all of them.
NAME_DOMAINS = (
    "crypto",
    "cyberpunk",
    "fantasy",
    "sci-fi",
    "anime / manga",
    "literature & cinema",
    "history",
)

NAME_TONES = (
    "hilarious",
    "grim",
    "mysterious",
    "absurd",
    "mock-epic",
    "pathetic",
)

# 10 / 20 / 70 (Pedro). A hard name does NOT make a hunt unwinnable — the ramp
# keeps easing until someone wins — it makes it LONGER, which means more clue
# posts and more reach. Easy names are where closed-category words belong.
DIFFICULTY_WEIGHTS = (("accessible", 10), ("medium", 20), ("cerebral", 70))

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


# Crypto jargon is a CLOSED SET too — "hodl", "rug", "pump", "wallet" each fall to
# a single clue that gestures at crypto vocabulary. Measured 2026-08-23: with the
# crypto domain in play the model produced "Hodl Tortoise", "Rug Shrine" and
# "Pumping Cassowary" — the last one reusing the very word that lost mini hunt #1.
# The crypto DOMAIN stays; it must express itself in the lore and the art, never
# by putting the jargon in the name.
CRYPTO_JARGON_WORDS = frozenset(
    """hodl hodler hodling rug rugged rugpull pump pumping pumped dump dumping
    moon mooning lambo ape aped aping degen degens wagmi ngmi fud fudder shill
    shilling whale whales bags bagholder wallet wallets satoshi altcoin shitcoin
    memecoin airdrop tokenomics defi gm gn wen ser fren anon normie""".split()
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
- AT MOST ONE IDENTIFIABLE WORD. A word is IDENTIFIABLE when a single clue can \
pin it exactly: a historical first name, a character, a colour, a number. You may \
use ONE — "Napoleon Toad" is a fine name — but then the OTHER word must live in \
an OPEN field where a hundred candidates fit every angle. Mini hunt #1 died \
because BOTH words fell fast. One identifiable word makes the hunt warm; two end it.
- THE DOMAINS ARE FLAVOUR, NOT SOURCE MATERIAL. For "history" and "literature & \
cinema" especially: EVOKE the world, never lift the thing. A real full name \
("Napoleon Bonaparte") is googlable, so marketplace search returns thousands of \
results and the final step of the hunt breaks — besides being someone else's IP. \
Borrow a flavour, a first name, a cadence; invent the rest.
- NO CRYPTO JARGON IN THE NAME, even when the domain is crypto. Words like hodl, \
rug, pump, moon, ape, degen, wallet, whale, bag, wagmi are a closed vocabulary a \
player can enumerate — one clue gesturing at crypto-speak solves the word. The \
crypto domain belongs in the LORE and the ARTWORK. The name stays clean.
- MEME IS MANDATORY, IN EVERY SINGLE NAME. This is the project's whole identity \
and the one rule that outranks the others. Every relic is MEME x one other world; \
the other world supplies the flavour, the meme supplies the joke. Meme culture is \
WIDE — animals, everyday absurdity, fantasy, manga, medieval, sci-fi, mythology \
are all fair game — so the test is NOT the genre, it is whether the name makes \
someone SMILE. "Gandalf the Rugged" works. "Bilgewright Tallowscript" does not: \
pretty, pretentious, no joke. Nor do "hollow quorum", "latent vigil" or "charnel \
proxy" — those are literary, and literary is the failure mode to avoid. If you \
cannot imagine the name as a profile picture someone would actually use, start again.
- THE NAME MUST BE DEPICTABLE, and the artwork must show the NAME. Two of the \
hunt's clues describe the picture, so if the name is an abstract idea there is \
nothing to draw and those clues become useless. Name a THING, a CREATURE, a \
CHARACTER doing something — never a mood, a state or a concept. "Napoleon Toad" \
you can draw in one second; "latent vigil" you cannot draw at all.
- THE TONE BELOW GOVERNS THE LORE AND THE ARTWORK, NOT THE NAME. A grim relic may \
— and often should — carry a ridiculous name; that contrast is more meme than \
either half alone. Never let a serious tone make the name serious.
- VARY THE CAPITALISATION deliberately across relics (Title Case, lowercase, Mixed) \
— a pool where every name looks the same is a signature an indexer can filter on.
- VARY THE GRAMMAR too. "texture adjective + famous name" is a good shape and you \
reach for it far too often (five of nineteen in one sample: melting Ahab, crumpled \
Bonaparte, soggy hamlet, brackish cassandra). Rotate through the others: noun+noun, \
verb+noun, occupation+creature, compound coinage, place+thing. A pool with one \
grammar is as guessable as a pool with one theme.
- NEVER REUSE A WORD the pool has already spent, and resist your own favourites: \
"brackish", "hollow", "stale", "soggy", "glitch", "molten", "damp", "frayed" are \
textures you return to constantly. Reach past the first adjective that arrives.
- description: 1-3 sentences of in-character MEME lore for the NFT description. \
It must NOT state or spell out who/what the relic "really" references — that is \
the puzzle. CRITICALLY: the description must not contain EITHER WORD OF THE NAME, \
nor any solution term, in any form — not the plural, not the verb, not inside a \
longer word. Write the lore as if the name were secret, because it is. No URLs, \
no hashtags, no @handles, no square brackets. (A short claim code is appended \
later by the system — do not invent one.)
- image_prompt: a vivid prompt for the relic's artwork in THIS EXACT visual \
style: "{style}". No text in the image, no real-person likeness. Make the style \
directive land — pool diversity matters.
- solution_terms: JSON array, AT MOST 8 entries, of the literal answer words that \
would solve the puzzle outright — the name's own words and their direct \
give-aways. Every term here is BANNED from every clue, so a bloated list strangles \
the clue writer before it starts: do not list adjacent references, inspirations or \
themes, only words that hand over the answer.

Respond with ONLY a JSON object and NOTHING ELSE — no preamble, no reasoning, no \
"let me think", no markdown fence. Start your reply at the opening brace. Keys: \
archetype, name, description, image_prompt, solution_terms."""


def _extract_json(text: str) -> dict:
    start = text.find("{")
    end = text.rfind("}")
    if start == -1 or end == -1 or end < start:
        raise ValueError(f"no JSON object in model response: {text[:200]!r}")
    return json.loads(text[start : end + 1])


# Too short / too generic to be worth reserving across the whole pool.
# 3, not 4: "rat", "owl", "orb", "ape", "god" are exactly the short concrete nouns
# a meme name lives on, and letting them repeat freely would defeat the rule.
_WORD_MIN_LEN = 3
_WORD_STOPLIST = frozenset(
    """the and of a an for with from that this into over under his her its not
    but all out are was who whose""".split()
)


def name_words(text: str) -> set[str]:
    """Letter runs worth reserving. Used both to record what a relic consumed and
    to refuse a candidate that reuses it."""
    return {
        w
        for w in re.findall(r"[a-z]+", str(text).lower())
        if len(w) >= _WORD_MIN_LEN and w not in _WORD_STOPLIST
    }


def _clean_line(s: str) -> str:
    # Strip markup the marketplaces render literally / that could break search.
    bad = ["[", "]", "#", "@", "\n", "\r", "\t"]
    out = str(s)
    for b in bad:
        out = out.replace(b, " ")
    return " ".join(out.split()).strip()


def _validate(
    data: dict,
    *,
    style: str,
    name_check: NameAvailability,
    register: str = "medium",
    domains: tuple[str, ...] = (),
    tone: str = "",
    avoid_words: set[str] | None = None,
) -> GeneratedRelic:
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
    # WORD-LEVEL uniqueness across the pool. The model has favourite textures and
    # reuses them: "brackish" landed in three separate samples, "sensei", "hollow",
    # "stale", "soggy" and "glitch" in two each (measured 2026-08-23). Theme-level
    # anti-repetition did not catch it because the themes really were different.
    # Two relics sharing a word also make marketplace search ambiguous.
    reused = sorted(name_words(name) & (avoid_words or set()))
    if reused:
        raise ValueError(
            f"name reuses word(s) {reused} already spent by an earlier relic — every "
            f"word in the pool must be used once: {name!r}"
        )

    jargon = sorted(
        {run for run in re.findall(r"[a-z]+", name.lower()) if run in CRYPTO_JARGON_WORDS}
    )
    # NOT a rejection (Pedro, 2026-08-23). Knowing ONE of the two words gets a
    # player nowhere — marketplace search needs both, measured on OpenSea — so an
    # enumerable word costs half a solution and only if the CLUES hand over its
    # category. The fix belongs in the clue writer, not in a banned-words list.
    # We record which words are enumerable so the clue engine can be told: for
    # these, never gesture at the category; attack by concrete anchor, cultural
    # use, sound, or relation to the other word.
    enumerable = tuple(sorted(set(closed) | set(jargon)))

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
        archetype=_clean_line(data["archetype"]),
        domains=tuple(domains),
        tone=tone,
        register=register,
        enumerable_words=enumerable,
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

    def _pick_domains(self, sequence: int | None) -> tuple[str, str]:
        """Every relic is MEME x one world. The world ROTATES rather than being
        sampled: random draws cluster, and three fantasies in a row is how the
        monoculture happened. `sequence` is the pool's relic count, so seven
        consecutive relics visit all seven worlds."""
        n = len(NAME_DOMAINS)
        seq = secrets.randbelow(10_000) if sequence is None else int(sequence)
        return "meme", NAME_DOMAINS[seq % n]

    def _pick_tone(self) -> str:
        return NAME_TONES[secrets.randbelow(len(NAME_TONES))]

    def _pick_register(self) -> str:
        """Weighted 10/20/70 toward hard."""
        total = sum(w for _, w in DIFFICULTY_WEIGHTS)
        roll = secrets.randbelow(total)
        upto = 0
        for name, weight in DIFFICULTY_WEIGHTS:
            upto += weight
            if roll < upto:
                return name
        return DIFFICULTY_WEIGHTS[-1][0]

    def generate(
        self,
        *,
        register: str | None = None,
        avoid_recent: list[str] | None = None,
        sequence: int | None = None,
        avoid_words: set[str] | None = None,
    ) -> GeneratedRelic:
        # An explicit register wins (so a 1B hunt can demand a hard name); otherwise
        # roll the 10/20/70.
        reg_key = (register or self._pick_register()).lower()
        reg = REGISTERS.get(reg_key, REGISTERS["medium"])
        avoid = ", ".join(avoid_recent or []) or "(none yet)"
        spent_words = {w.lower() for w in (avoid_words or set())}
        # Show the model the reserved words too — the code check is the net, but a
        # rejected attempt costs a whole call, so it is worth avoiding up front.
        spent = (
            "Words ALREADY SPENT by earlier relics — every one of them is forbidden, "
            "no exceptions, not even as part of a compound: "
            + ", ".join(sorted(spent_words)[:80])
            + "\n"
            if spent_words
            else ""
        )
        d1, d2 = self._pick_domains(sequence)
        tone = self._pick_tone()
        last_err: Exception | None = None
        for _ in range(_MAX_GENERATE_ATTEMPTS):
            style = self._pick_style()
            system = SYSTEM_PROMPT.replace("{style}", style)
            user = (
                f"Difficulty: {reg}\n"
                f"This relic is {d1} x {d2}. The meme half is not optional — it is "
                f"what makes the name land; {d2} supplies the world it borrows from. "
                f"The character must plausibly belong to both.\n"
                f"Tone (for the LORE and the ARTWORK — never for the name): {tone}.\n"
                f"Avoid names AND themes too close to these recent relics: {avoid}\n"
                f"{spent}"
                "Invent one original meme relic now. Reply with the JSON object only."
            )
            try:
                resp = self._client.messages.create(
                    model=self._model,
                    # Roomy on purpose. The 3 failures in 24 (2026-08-23) were the
                    # model narrating its reasoning first and running out of budget
                    # before it ever reached the JSON — not a refusal. Assistant
                    # prefill would be the tidy fix but this model rejects it, so we
                    # pay for the preamble and let _extract_json skip past it.
                    max_tokens=2000,
                    system=system,
                    messages=[{"role": "user", "content": user}],
                )
                text = "".join(
                    b.text for b in resp.content if getattr(b, "type", "text") == "text"
                )
                return _validate(
                    _extract_json(text),
                    style=style,
                    name_check=self._name_check,
                    register=reg_key,
                    avoid_words=spent_words,
                    domains=(d1, d2),
                    tone=tone,
                )
            except Exception as e:  # noqa: BLE001 — regenerate on any validation/parse failure
                last_err = e
                continue
        raise RuntimeError(
            f"relic generation failed after {_MAX_GENERATE_ATTEMPTS} attempts: {last_err!r}"
        )
