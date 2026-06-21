"""Dry-run a full hunt locally (checklist step 29).

Uses the REAL Persona Generator + Clue Engine (Anthropic) so you see genuine
content, but fakes X, the chain, DMs and the DB — nothing is published, no money
moves. Prints every post the agent would make, then verifies the integrity hash.

    python scripts/simulate_hunt.py [accessible|medium|cerebral]

Costs a few Anthropic calls (persona + clues). No image, no X writes.
"""

from __future__ import annotations

import sys

from anthropic import Anthropic

from finding_memeland.config import get_settings
from finding_memeland.content.clue_engine import ClueEngine
from finding_memeland.content.integrity import verify_integrity_hash
from finding_memeland.orchestrator.simulation import build_simulation
from finding_memeland.persona.generator import PersonaGenerator


def main() -> int:
    register = sys.argv[1] if len(sys.argv) > 1 else "medium"
    s = get_settings()
    if not s.anthropic_api_key or s.anthropic_api_key.startswith("sk-ant-xxx"):
        print("FAIL — set a real ANTHROPIC_API_KEY in .env first.")
        return 2

    client = Anthropic(api_key=s.anthropic_api_key)
    rig = build_simulation(
        persona_generator=PersonaGenerator(client, s.anthropic_model),
        clue_engine=ClueEngine(client, s.anthropic_model),
        register=register,
        win_after_polls=4,   # a few clues post before the winner shows up
        verbose=True,        # the fake publisher/notifier print the narrative
    )

    print(f"=== DRY-RUN HUNT (register={register}) ===")
    hunt = rig.orchestrator.run_hunt()

    print("\n" + "=" * 60)
    print("INTEGRITY CHECK (what anyone could verify after the hunt):")
    ok = verify_integrity_hash(
        hunt.persona.x_user_id, hunt.claim_code, hunt.salt, hunt.integrity_hash
    )
    print(f"  user_id={hunt.persona.x_user_id}  claim_code={hunt.claim_code}")
    print(f"  salt={hunt.salt}")
    print(f"  hash={hunt.integrity_hash}")
    print(f"  recomputes correctly: {ok}")

    print("\nSUMMARY")
    print(f"  final state : {hunt.state.value}")
    print(f"  clues posted: {len(hunt.clues)}")
    print(f"  prize       : {hunt.prize_fmml:,} $FMML")
    print(f"  winner paid : {rig.payout.sent[0]['wallet'] if rig.payout.sent else 'none'}")
    print(f"  submissions : {len(rig.repo.submissions)} logged")
    print(f"  persona retired: {hunt.persona.id in rig.persona_source.retired}")
    return 0 if ok and hunt.state.value == "done" else 1


if __name__ == "__main__":
    sys.exit(main())
