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
from collections import Counter

sys.path.insert(0, "src")

from anthropic import Anthropic  # noqa: E402

from finding_memeland.config import Settings  # noqa: E402
from finding_memeland.persona.relic_generator import (  # noqa: E402
    RelicGenerator,
    name_words,
)


class AlwaysAvailable:
    """Sem gate de findability/googlabilidade: isto é calibração de GOSTO, e uma
    ida à rede por nome só tornava o ciclo lento. O gate real corre em produção."""

    def is_available(self, name: str) -> bool:  # noqa: D102
        return True


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
    themes: list[str] = []
    spent_words: set[str] = set()
    failures = 0
    by_register: Counter[str] = Counter()
    by_domain: Counter[str] = Counter()

    for i in range(n):
        try:
            # `sequence` faz rodar os domínios tal como fará em produção (contagem
            # de relics do pool); `avoid_recent` leva TEMAS, não só nomes.
            r = gen.generate(
                sequence=i, avoid_recent=themes[-40:], avoid_words=spent_words
            )
        except Exception as e:  # noqa: BLE001
            failures += 1
            print(f"{i + 1:>3}. [FALHOU] {e!r}")
            continue

        seen.append(r.name)
        themes.append(r.theme_tag())
        spent_words |= name_words(r.name)
        by_register[r.register] += 1
        for d in r.domains:
            by_domain[d] += 1

        mark = (
            "   [enumerável: " + ", ".join(r.enumerable_words) + " — as pistas nunca "
            "podem aludir à categoria]" if r.enumerable_words else ""
        )
        print(f"{i + 1:>3}. {r.name}{mark}")
        print(f"     {r.register} · {r.tone} · {' x '.join(r.domains)}")
        print(f"     estilo:  {r.image_style[:52]}")
        print(f"     lore:    {r.description}")
        print(f"     termos:  {', '.join(r.solution_terms)}")
        print()

    dupes = len(seen) - len({x.lower() for x in seen})
    print("-" * 68)
    print(f"geradas: {len(seen)}   falhas: {failures}   nomes repetidos: {dupes}")
    print("dificuldade:", dict(by_register), " (alvo ~10/20/70)")
    print("domínios:   ", dict(by_domain))
    print("Nenhuma destas identidades foi gravada. Descarta o output.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
