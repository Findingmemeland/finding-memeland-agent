"""Persona generator — LLM-driven identity creation.

Produces a plausible, internally-consistent fictional identity sampled from a
tunable distribution of archetypes (niche meme account, hobbyist, anon trader).

Persona-safety policy (memory: account labels):
- Prefer historical figures dead >=50y, fully invented fictional characters,
  abstract concepts/animals/objects, or old fictional characters with no active
  IP holder. AVOID real living people, trademarks, and modern IP-held characters
  (those would force a Parody label and break immersion).
- Bios stay short/ambiguous ('just here for the vibes'); never assert false
  humanity.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class GeneratedPersona:
    display_name: str
    bio: str
    avatar_prompt: str        # fed to the image generator
    voice: str
    backstory: str            # internal — drives clue generation, never posted
    archetype: str


class PersonaGenerator:
    def __init__(self, anthropic_client, model: str):
        self._client = anthropic_client
        self._model = model

    def generate(self, *, avoid_recent: list[str] | None = None) -> GeneratedPersona:
        """Sample an archetype and produce a full identity.

        TODO(step 23/24): prompt the LLM with the archetype distribution and the
        persona-safety policy; return a GeneratedPersona. `avoid_recent` lets the
        orchestrator prevent repeating recent themes.
        """
        raise NotImplementedError("persona generation — implemented in step 23/24")

    def generate_avatar(self, avatar_prompt: str) -> bytes:
        """Return PNG bytes for the persona avatar.

        TODO(step 23): call the image generation backend; return image bytes for
        the dresser to upload via the X API.
        """
        raise NotImplementedError("avatar generation — implemented in step 23")
