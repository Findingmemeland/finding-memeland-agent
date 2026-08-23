"""Amostras DESCARTÁVEIS de identidades de relic — calibração do gerador.

Serve para afinar o PROMPT sem quebrar o blind mode: nada do que sai daqui é
gravado, cifrado ou mintado. São nomes que morrem no ecrã.

Correr (na raiz do repo, com o Doppler a injetar ANTHROPIC_API_KEY):

    doppler run -- python gerar_amostras_relic.py           # 20 amostras
    doppler run -- python gerar_amostras_relic.py 40        # 40 amostras

O que avaliar em cada nome:
  1. Tem PIADA? (o teste é ser meme, não o género)
  2. Cada palavra aguenta TRÊS ângulos diferentes de pista?
  3. Cada palavra pode ser APONTADA sem ser IDENTIFICADA? (o erro do "Uncle")
"""

from __future__ import annotations

import sys

sys.path.insert(0, "src")

from anthropic import Anthropic  # noqa: E402

from finding_memeland.config import Settings  # noqa: E402
from finding_memeland.persona.relic_generator import (  # noqa: E402
    CLOSED_CATEGORY_WORDS,
    RelicGenerator,
)


class AlwaysAvailable:
    """Sem gate de findability/googlabilidade: isto é calibração de GOSTO, e uma
    ida à rede por nome só tornava o ciclo lento. O gate real corre em produção."""

    def is_available(self, name: str) -> bool:  # noqa: D102
        return True


REGISTERS = ("accessible", "medium", "cerebral")


def main() -> int:
    n = int(sys.argv[1]) if len(sys.argv) > 1 else 20
    s = Settings()
    if not s.anthropic_api_key:
        print("ANTHROPIC_API_KEY em falta — corre com `doppler run --`.")
        return 1

    gen = RelicGenerator(
        Anthropic(api_key=s.anthropic_api_key),
        s.anthropic_model,
        AlwaysAvailable(),
    )

    print(f"\n{n} amostras descartáveis (nada disto é gravado ou mintado)\n")
    seen: list[str] = []
    failures = 0

    for i in range(1, n + 1):
        register = REGISTERS[i % len(REGISTERS)]
        try:
            # `avoid_recent` alimentado com as amostras anteriores: força variedade
            # dentro da própria corrida, tal como fará com o pool real.
            r = gen.generate(register=register, avoid_recent=seen[-40:])
        except Exception as e:  # noqa: BLE001
            failures += 1
            print(f"{i:>3}. [FALHOU] {register:<11} {e!r}")
            continue

        seen.append(r.name)
        w1, w2 = (w for w in r.name.split())
        flags = [
            w for w in (w1.lower(), w2.lower()) if w in CLOSED_CATEGORY_WORDS
        ]
        mark = "  ⚠ CATEGORIA FECHADA: " + ", ".join(flags) if flags else ""
        print(f"{i:>3}. {r.name}{mark}")
        print(f"     registo: {register}   estilo: {r.image_style[:44]}")
        print(f"     lore:    {r.description}")
        print(f"     termos:  {', '.join(r.solution_terms)}")
        print()

    dupes = len(seen) - len(set(x.lower() for x in seen))
    print("-" * 60)
    print(f"geradas: {len(seen)}   falhas: {failures}   nomes repetidos: {dupes}")
    print("Nenhuma destas identidades foi gravada. Descarta o output.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
