"""Generate a full sequence of clues, to eyeball the ramp/easing curve.

No X writes, no images, no DB writes. Shows each clue, its facet/obliqueness
target, the taunt, and how clues 1 and 2 look once wrapped by the post
templates. Every clue is guardrail-validated by the engine before it is
returned.

Two modes:

    # A fresh generated persona (as before):
    python scripts/generate_clues_sample.py [n_clues] [accessible|medium|cerebral]

    # A DRESSED persona from the pool (read-only; oldest dress first, or a
    # specific @handle) — the ramp exactly as /launch would run it (R2: the
    # context is rebuilt from the persisted descriptor: identity, handle_hint,
    # anchor posts). Nothing is written; the persona stays 'dressed'; the
    # claim code is never printed.
    python scripts/generate_clues_sample.py [n_clues] --dressed [@handle]
"""

from __future__ import annotations

import json
import sys
from datetime import datetime, timezone

from anthropic import Anthropic

from finding_memeland.config import get_settings
from finding_memeland.content.clue_engine import (
    ClueEngine,
    PersonaContext,
    clue_vector_for,
    hint_terms,
    obliqueness_for,
    post_phase_start,
)
from finding_memeland.content.templates import clue_followup, clue_one


def _parse_args(argv: list[str]) -> tuple[int, str, bool, str | None]:
    """Returns (n_clues, register, dressed_mode, handle_filter)."""
    n_clues, register, dressed, handle = 5, "medium", False, None
    positional: list[str] = []
    it = iter(argv)
    for a in it:
        if a == "--dressed":
            dressed = True
            nxt = next(it, None)
            if nxt is not None:
                if nxt.startswith("@"):
                    handle = nxt
                else:
                    positional.append(nxt)
        else:
            positional.append(a)
    if positional:
        n_clues = int(positional[0])
    if len(positional) > 1:
        register = positional[1]
    return n_clues, register, dressed, handle


def _load_dressed_context(s, handle_filter: str | None) -> tuple[PersonaContext, dict]:
    """Read-only: pick a row from the dressed pool and rebuild the clue
    context the same way Orchestrator._prepare_predressed does (R2)."""
    from finding_memeland.db.client import Repo, make_client
    from finding_memeland.persona.generator import GeneratedPersona

    repo = Repo(make_client(s.supabase_url, s.supabase_service_role_key))
    pool = repo.dressed_personas()
    if not pool:
        raise SystemExit("FAIL — dressed pool is empty (nothing to sample). Run /dress first.")
    if handle_filter:
        want = handle_filter.lstrip("@").lower()
        rows = [r for r in pool if str(r.get("handle") or "").lstrip("@").lower() == want]
        if not rows:
            have = ", ".join("@" + str(r.get("handle") or "").lstrip("@") for r in pool)
            raise SystemExit(f"FAIL — {handle_filter} is not in the dressed pool (pool: {have}).")
        row = rows[0]
    else:
        row = pool[0]   # oldest dress first — exactly what /launch would pick

    payload = row.get("persona_identity")
    if isinstance(payload, str):
        payload = json.loads(payload)
    if not payload:
        raise SystemExit(f"FAIL — @{row.get('handle')} has no persona_identity descriptor.")
    identity = GeneratedPersona(**payload)
    handle = "@" + str(row.get("handle") or "").lstrip("@")
    ctx = PersonaContext.from_generated(
        identity, handle, handle_hint=str(row.get("handle_hint") or "")
    )
    anchors_raw = row.get("anchor_posts")
    if isinstance(anchors_raw, str):
        try:
            anchors_raw = json.loads(anchors_raw)
        except ValueError:
            anchors_raw = []
    ctx.anchor_posts = [
        str(a.get("text")) for a in (anchors_raw or [])
        if isinstance(a, dict) and a.get("text")
    ]
    return ctx, row


def _print_dressed_header(ctx: PersonaContext, row: dict) -> None:
    dressed_at = row.get("dressed_at")
    age = ""
    if dressed_at:
        try:
            dt = datetime.fromisoformat(str(dressed_at).replace("Z", "+00:00"))
            age = f" ({(datetime.now(timezone.utc) - dt).days}d ago)"
        except ValueError:
            pass
    # The applied bio carries the claim code — mask it. The code is radioactive
    # (never in terminals, logs or chats); the operator can read it on X.
    bio = str(row.get("applied_bio") or "")
    code = str(row.get("claim_code") or "")
    if code and code in bio:
        bio = bio.replace(code, "*" * len(code))
    print("mode        : DRESSED POOL (read-only)")
    print(f"handle      : {ctx.handle}")
    print(f"name        : {ctx.display_name}")
    print(f"applied bio : {bio or '(none)'}")
    print(f"avatar      : {ctx.avatar_description}")
    print(f"backstory   : {ctx.backstory}")
    print(f"dressed_at  : {dressed_at}{age}")
    print(f"handle_hint : {ctx.handle_hint or '(none)'}")
    print(f"anchors ({len(ctx.anchor_posts)}):")
    for a in ctx.anchor_posts:
        print(f"   - {a}")
    print(f"answer terms (banned in clues): {ctx.solution_terms}")
    print(f"hint terms   (banned in clues): {hint_terms(ctx.handle_hint)}")
    print(f"ramp head (shuffled per run) : {ctx.clue_facet_plan}")
    print(f"post phase starts at clue    : {post_phase_start(ctx)}")


def main() -> int:
    n_clues, register, dressed, handle_filter = _parse_args(sys.argv[1:])
    s = get_settings()
    if not s.anthropic_api_key or s.anthropic_api_key.startswith("sk-ant-xxx"):
        print("FAIL — set a real ANTHROPIC_API_KEY in .env first.")
        return 2

    client = Anthropic(api_key=s.anthropic_api_key)

    if dressed:
        ctx, row = _load_dressed_context(s, handle_filter)
        _print_dressed_header(ctx, row)
    else:
        from finding_memeland.persona.generator import PersonaGenerator

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
        print(f"\n--- clue {i}  (obliqueness {obliqueness_for(i, ctx)}, facet: {vector}) ---")
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
