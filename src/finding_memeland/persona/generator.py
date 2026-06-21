"""Persona generator — LLM-driven identity creation.

Produces a plausible, internally-consistent fictional identity for one hunt. The
persona is the *hidden target*: players must identify it by inference from
oblique clues (name, @, avatar, posts), never by direct lookup. So a good
persona has "texture" — paradoxes, structural patterns, two-vector identifiers.

Persona-safety policy (memory: account labels) — to avoid X's mandatory Parody
label, which would break immersion:
  PREFER  historical figures dead >=50y; fully invented fictional characters;
          abstract concepts / animals / objects; old fictional characters with
          no active IP holder.
  AVOID   real living people; trademarks; modern IP-held characters.

Bios stay short and ambiguous ("just here for the vibes"); never assert false
humanity; X-safe characters only (see x_text). The avatar is image-generated
from `avatar_prompt` by a separate backend (TODO: pick the image service).
"""

from __future__ import annotations

import json
from dataclasses import dataclass

from ..social.x_text import MAX_BIO_LEN, MAX_NAME_LEN, sanitize_bio, sanitize_name


@dataclass
class GeneratedPersona:
    display_name: str
    bio: str
    avatar_prompt: str        # fed to the image generator
    voice: str
    backstory: str            # internal — drives clue generation, never posted
    archetype: str
    solution_terms: list[str]  # the literal answer words a clue must NEVER contain


ARCHETYPES = (
    "historical figure dead at least 50 years",
    "fully invented fictional character",
    "abstract concept, animal, or object given a voice",
    "old fictional character with no active IP holder",
    "mythological or folklore figure",
    "invented crypto-native archetype (anon degen, rugged dev, diamond-hands "
    "grandma, perma-bull oracle, etc.) — the SAFEST meme-native route",
    "genuinely ownerless folklore or pre-internet phenomenon (NOT character "
    "memes with an identifiable creator — see forbidden list)",
)

# Difficulty registers the pipeline cycles through to keep the pool varied
# (Pedro's call, 2026-06-19: balanced mix). The caller picks one per hunt.
REGISTERS = {
    "accessible": (
        "ACCESSIBLE: a broad crypto-Twitter audience should crack this within a "
        "few clues. Lean meme-native — crypto-culture archetypes, internet "
        "folklore, animals, well-known public-domain or mythological figures. "
        "Fun and fast, still inferential (not a bare name drop)."
    ),
    "medium": (
        "MEDIUM: solvable by an attentive player who connects 2-3 vectors. A "
        "recognizable figure or concept hidden behind an oblique framing."
    ),
    "cerebral": (
        "CEREBRAL: a hard, erudite puzzle for the hardcore — abstract concepts or "
        "niche history requiring several combined inferences. Rewards clever "
        "players and solver-agent tooling."
    ),
}

SYSTEM_PROMPT = """You design fictional X (Twitter) personas for "Finding Memeland", \
an AI-run treasure-hunt game. Each persona is a HIDDEN account that players must \
identify by INFERENCE from oblique clues — never by a direct name lookup. A great \
persona therefore has texture: paradoxes, structural quirks, and an identity that \
can be hinted at through at least two combined vectors (e.g. name + avatar).

Hard safety rules (to avoid X's Parody label, which breaks the game):
- ALLOWED: historical figures dead at least 50 years; fully invented fictional \
characters; abstract concepts/animals/objects; old fictional characters with no \
active IP holder; mythological/folklore figures; invented crypto-native \
archetypes; public-domain or internet-folklore figures.
- FORBIDDEN: real living people; trademarks/brands; modern IP-held characters \
(Disney, contemporary anime); and faithful reproductions of a SPECIFIC owned \
meme character or its trade dress — e.g. the specific Pepe the Frog character / \
"feels good man" face / "rare Pepe" branding, the specific Wojak characters, the \
specific Doge dog photo. The line is COPYING a specific owned character, not the \
subject matter: generic ORIGINAL subjects are fine, including original frogs in \
your own distinct art style (frogs are on-brand for this project). When you use a \
common animal/subject, make it your own, not a clone of a known meme character.

Coherence rule: the declared `archetype` MUST be the TRUE category of the hidden \
identity. Do NOT label something "invented" if it secretly points to a real \
person, artifact, or concept — pick the archetype that honestly matches.

Tone/difficulty: a balanced pool — sometimes erudite and hard, sometimes light \
and meme-native. Follow the difficulty register given in the user message.

Output rules:
- display_name: max 50 chars, plain characters only (letters, digits, spaces, and \
simple punctuation . , ' -). NO square brackets, emoji, or markup characters.
- bio: max 130 chars, short and ambiguous, in-character, plausible as a normal \
niche account. Must NOT state or imply it is a real living human. No URLs, no \
@handles, no hashtags, no square brackets or special markup characters.
- The display_name and bio must NOT openly reveal who the persona "really" is — \
that is the puzzle. Keep it inferable, not stated.
- Vary the naming style. Do NOT default to the "The ___" construction; mix in \
plain names, lowercase handles-as-names, one-word names, phrases, etc., so the \
pool has no predictable naming pattern players could filter on.
- backstory: 2-4 sentences, the real hidden identity and the facts/paradoxes that \
clues will later draw on. This is INTERNAL and never published.
- voice: one short line describing how this persona posts.
- avatar_prompt: a vivid image-generation prompt for a fitting profile picture, \
no real-person likenesses.
- solution_terms: a JSON array of the literal answer words/names that would solve \
the puzzle outright — the true identity's name and its most direct give-aways \
(e.g. for Icarus: ["Icarus", "Daedalus"]). Clues must NEVER contain these, so be \
thorough. Do NOT include generic words that would over-block normal clue writing.

Respond with ONLY a JSON object, no prose, with keys: archetype, display_name, \
bio, backstory, voice, avatar_prompt, solution_terms."""


def _extract_json(text: str) -> dict:
    """Pull the JSON object out of a model response, tolerant of stray prose."""
    start = text.find("{")
    end = text.rfind("}")
    if start == -1 or end == -1 or end < start:
        raise ValueError(f"no JSON object in model response: {text[:200]!r}")
    return json.loads(text[start : end + 1])


def _to_persona(data: dict) -> GeneratedPersona:
    """Validate + sanitize raw model output into a safe GeneratedPersona."""
    required = {
        "archetype", "display_name", "bio", "backstory", "voice",
        "avatar_prompt", "solution_terms",
    }
    missing = required - data.keys()
    if missing:
        raise ValueError(f"persona JSON missing keys: {sorted(missing)}")

    name = sanitize_name(str(data["display_name"]))
    bio = sanitize_bio(str(data["bio"]))  # leaves room for the claim code
    if not name:
        raise ValueError("empty display_name after sanitization")
    if not bio:
        raise ValueError("empty bio after sanitization")

    raw_terms = data["solution_terms"]
    if not isinstance(raw_terms, list):
        raise ValueError("solution_terms must be a list")
    solution_terms = [str(t).strip() for t in raw_terms if str(t).strip()]
    if not solution_terms:
        raise ValueError("solution_terms is empty — the answer must be specified")

    return GeneratedPersona(
        display_name=name,
        bio=bio,
        avatar_prompt=str(data["avatar_prompt"]).strip(),
        voice=str(data["voice"]).strip(),
        backstory=str(data["backstory"]).strip(),
        archetype=str(data["archetype"]).strip(),
        solution_terms=solution_terms,
    )


class PersonaGenerator:
    def __init__(self, anthropic_client, model: str):
        self._client = anthropic_client
        self._model = model

    def generate(
        self,
        *,
        register: str | None = None,
        avoid_recent: list[str] | None = None,
    ) -> GeneratedPersona:
        """Sample an identity.

        `register` ∈ REGISTERS ("accessible"|"medium"|"cerebral") sets difficulty;
        the pipeline cycles it to keep the pool varied. None lets the model choose.
        `avoid_recent` lists recent archetypes/themes to steer away from, so
        consecutive hunts don't feel repetitive.
        """
        if register is not None and register not in REGISTERS:
            raise ValueError(f"unknown register {register!r}; pick one of {list(REGISTERS)}")

        user_msg = (
            "Generate one persona. Pick whichever allowed archetype fits a strong, "
            "oblique puzzle."
        )
        if register:
            user_msg += "\n\nDifficulty register — " + REGISTERS[register]
        if avoid_recent:
            user_msg += "\n\nAvoid repeating these recent themes: " + "; ".join(avoid_recent)

        resp = self._client.messages.create(
            model=self._model,
            max_tokens=1024,
            system=SYSTEM_PROMPT,
            messages=[{"role": "user", "content": user_msg}],
        )
        text = "".join(block.text for block in resp.content if getattr(block, "type", "") == "text")
        return _to_persona(_extract_json(text))

    # Avatar generation lives in persona/avatar.py (AvatarGenerator, OpenAI
    # gpt-image). It consumes GeneratedPersona.avatar_prompt.
