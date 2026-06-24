"""Generate a few sample personas with the real LLM and print them.

Lets us eyeball persona quality (safety, obliqueness, voice) and confirm bios are
X-safe and within limits — WITHOUT writing to any X account. No dressing here;
that needs an authorized test persona (step 16, after warmup).

Run from the repo root with the Anthropic key available (in .env locally):

    python scripts/generate_persona_sample.py [count]
"""

from __future__ import annotations

import sys

from anthropic import Anthropic

from finding_memeland.config import get_settings
from finding_memeland.persona.dresser import compose_bio
from finding_memeland.persona.generator import REGISTERS, PersonaGenerator
from finding_memeland.social.x_text import (
    BIO_FORBIDDEN_CHARS,
    MAX_BIO_LEN,
    MAX_NAME_LEN,
)


def main() -> int:
    count = int(sys.argv[1]) if len(sys.argv) > 1 else 3
    s = get_settings()
    if not s.anthropic_api_key or s.anthropic_api_key.startswith("sk-ant-xxx"):
        print("FAIL — set a real ANTHROPIC_API_KEY in .env first.")
        return 2

    gen = PersonaGenerator(Anthropic(api_key=s.anthropic_api_key), s.anthropic_model)
    registers = list(REGISTERS)  # cycle accessible -> medium -> cerebral
    seen: list[str] = []
    all_ok = True

    for i in range(1, count + 1):
        register = registers[(i - 1) % len(registers)]
        try:
            p = gen.generate(register=register, avoid_recent=seen)
        except Exception as e:  # noqa: BLE001
            print(f"[{i}] FAIL — generation/parsing error: {e!r}")
            all_ok = False
            continue
        seen.append(p.archetype)

        # Validate X-safety locally.
        sample_bio = compose_bio(p.bio, "ABCDEFGH")  # how it looks once dressed
        problems = []
        if len(p.display_name) > MAX_NAME_LEN:
            problems.append(f"name too long ({len(p.display_name)})")
        if len(sample_bio) > MAX_BIO_LEN:
            problems.append(f"dressed bio too long ({len(sample_bio)})")
        if set(sample_bio) & BIO_FORBIDDEN_CHARS:
            problems.append("dressed bio still has forbidden chars")
        flag = "OK" if not problems else "CHECK: " + ", ".join(problems)
        if problems:
            all_ok = False

        print(f"\n===== persona [{i}]  register={register}  ({flag}) =====")
        print(f"archetype    : {p.archetype}")
        print(f"name         : {p.display_name}")
        print(f"bio          : {p.bio}")
        print(f"voice        : {p.voice}")
        print(f"backstory    : {p.backstory}")
        print(f"avatar       : {p.avatar_prompt}")
        print(f"banner       : {p.banner_prompt}")
        print(f"findable post: {p.findable_post}")
        print(f"--- dressed bio preview (with claim code) ---\n{sample_bio}")

    print("\nALL OK" if all_ok else "\nSOME ISSUES — see CHECK notes above")
    return 0 if all_ok else 1


if __name__ == "__main__":
    sys.exit(main())
