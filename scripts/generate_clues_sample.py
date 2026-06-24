"""Generate a persona and a full sequence of clues, to eyeball the easing curve.

No X writes, no images. Shows each clue, its obliqueness target, the taunt, and
how clues 1 and 2 look once wrapped by the post templates. Every clue is
guardrail-validated by the engine before it is returned.

    python scripts/generate_clues_sample.py [n_clues] [accessible|medium|cerebral]
"""

from __future__ import annotations

import sys

from anthropic import Anthropic

from finding_memeland.config import get_settings
from finding_memeland.content.clue_engine import (
    ClueEngine,
    PersonaContext,
    clue_vector_for,
    obliqueness_for,
)
from finding_memeland.content.templates import clue_followup, clue_one
from finding_memeland.persona.generator import PersonaGenerator


def main() -> int:
    n_clues = int(sys.argv[1]) if len(sys.argv) > 1 else 5
    register = sys.argv[2] if len(sys.argv) > 2 else "medium"
    s = get_settings()
    if not s.anthropic_api_key or s.anthropic_api_key.startswith("sk-ant-xxx"):
        print("FAIL — set a real ANTHROPIC_API_KEY in .env first.")
        return 2

    client = Anthropic(api_key=s.anthropic_api_key)
    persona = PersonaGenerator(client, s.anthropic_model).generate(register=register)
    ctx = PersonaContext.from_generated(persona, handle="@sample_persona")

    print(f"register   : {register}")
    print(f"archetype  : {persona.archetype}")
    print(f"name       : {persona.display_name}")
    print(f"bio        : {persona.bio}")
    print(f"backstory  : {persona.backstory}")
    print(f"answer terms (hidden from clues): {persona.solution_terms}")
    print("=" * 60)

    engine = ClueEngine(client, s.anthropic_model)
    prior: list[str] = []
    for i in range(1, n_clues + 1):
        try:
            draft = engine.next_clue(ctx, clue_index=i, prior_clues=prior)
        except Exception as e:  # noqa: BLE001
            print(f"\n[clue {i}] FAIL — {e!r}")
            return 1
        prior.append(draft.text)
        vector = clue_vector_for(i, ctx)
        print(f"\n--- clue {i}  (obliqueness {obliqueness_for(i)}, facet: {vector}) ---")
        print(draft.text)
        if draft.taunt:
            print(f"taunt: {draft.taunt}")

    # Show how the first two clues look once wrapped by the post templates.
    print("\n" + "=" * 60)
    print("WRAPPED PREVIEW — clue 1 (announcement + reshare gate + integrity hash):\n")
    print(clue_one(hunt_n=1, clue_text=prior[0], prize="250,000", integrity_hash="<sha256-hash-here>"))
    if len(prior) > 1:
        print("\nWRAPPED PREVIEW — clue 2:\n")
        print(clue_followup(2, prior[1], "c'mon you lazy degens"))

    print("\nALL CLUES PASSED GUARDRAILS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
