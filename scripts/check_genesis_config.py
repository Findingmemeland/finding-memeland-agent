"""Pre-flight de configuração do Genesis Hunt.

Corre ANTES do /launch. Lê o .env como o agente o lê e confirma que:
  1. os valores do Genesis estão de facto carregados
  2. a cadência das pistas é aleatória e dentro da banda pedida
  3. HOLDING_HOURS cobre o PIOR caso do hunt — se não cobrir, a regra pública
     "hold antes da primeira pista" torna-se falsa e alguém pode comprar a meio
     do hunt e ganhar à mesma

Uso:
    python scripts/check_genesis_config.py
"""

from __future__ import annotations

import random
import statistics
import sys

from finding_memeland.config import get_settings

EXPECTED_GENESIS = {
    "min_prize_usd": 100.0,
    "holding_floor_fmml": 9_000_000,
    "holding_hours": 8,
    "clue_min_gap_s": 600,
    "clue_max_gap_s": 3600,
}
PRIZE_FMML = 1_000_000_000  # 1B $FIND prometidos publicamente
MAX_CLUES_ASSUMED = 8


def main() -> int:
    s = get_settings()
    problems: list[str] = []
    warnings: list[str] = []

    print("=" * 62)
    print("GENESIS HUNT — pre-flight de configuração")
    print("=" * 62)

    # ---- 1. valores carregados -------------------------------------------
    print("\n[1] Valores lidos do ambiente:")
    for key, expected in EXPECTED_GENESIS.items():
        actual = getattr(s, key, None)
        ok = actual == expected
        mark = "OK " if ok else "!! "
        print(f"  {mark}{key:22} = {actual!r:>12}   (Genesis espera {expected!r})")
        if not ok:
            problems.append(f"{key} está {actual!r}, devia ser {expected!r}")

    # ---- 2. cadência das pistas ------------------------------------------
    lo, hi = s.clue_min_gap_s, s.clue_max_gap_s
    print(f"\n[2] Cadência das pistas: sorteio uniforme em [{lo//60}min, {hi//60}min] por pista")
    if lo >= hi:
        problems.append(f"CLUE_MIN_GAP_S ({lo}) >= CLUE_MAX_GAP_S ({hi}) — random.randint rebenta")
    else:
        gaps = [random.randint(lo, hi) for _ in range(MAX_CLUES_ASSUMED)]
        t = 0.0
        for i, g in enumerate(gaps, start=2):
            t += g
            print(f"      clue {i}: +{g // 60:>2}min   (hora {t / 3600:.1f}h)")
        spread = (hi - lo) / 60
        if spread < 20:
            warnings.append(
                f"banda de apenas {spread:.0f}min — previsível demais, os jogadores "
                "conseguem antecipar a próxima pista"
            )

    # ---- 3. holding_hours cobre o pior caso? -----------------------------
    worst_case_h = MAX_CLUES_ASSUMED * hi / 3600
    print(f"\n[3] Duração do hunt vs janela de holding ({MAX_CLUES_ASSUMED} pistas):")
    trials = [sum(random.randint(lo, hi) for _ in range(5)) for _ in range(10_000)]
    print(f"      mediana a 5 pistas : {statistics.median(trials) / 3600:.1f}h")
    print(f"      PIOR caso a {MAX_CLUES_ASSUMED} pistas: {worst_case_h:.1f}h")
    print(f"      HOLDING_HOURS      : {s.holding_hours}h")
    if s.holding_hours < worst_case_h:
        problems.append(
            f"HOLDING_HOURS ({s.holding_hours}h) NÃO cobre o pior caso ({worst_case_h:.1f}h): "
            'se o hunt se arrastar, a regra pública "hold antes da Clue 1" fica FALSA '
            "e um comprador a meio do hunt pode ganhar"
        )
    else:
        print(f"      -> OK: a janela cobre o pior caso (folga {s.holding_hours - worst_case_h:.1f}h)")

    # ---- 4. prémio: 1B tokens continua a caber no MIN_PRIZE_USD? ---------
    print("\n[4] Prémio (prometemos 1B $FIND, mas /launch leva DÓLARES):")
    if not s.fmml_usd_price:
        problems.append("FMML_USD_PRICE não está definido — /launch não consegue converter $ -> $FIND")
    else:
        prize_usd = PRIZE_FMML * s.fmml_usd_price
        print(f"      FMML_USD_PRICE = {s.fmml_usd_price}")
        print(f"      1B $FIND       = ${prize_usd:.2f}  ->  /launch {prize_usd:.0f}")
        if prize_usd < s.min_prize_usd:
            problems.append(
                f"o prémio de 1B (${prize_usd:.0f}) está ABAIXO do MIN_PRIZE_USD "
                f"(${s.min_prize_usd:.0f}) — o agente vai RECUSAR o /launch"
            )
        if s.payout_cap_fmml and PRIZE_FMML > s.payout_cap_fmml:
            problems.append(
                f"1B $FIND excede o PAYOUT_CAP_FMML ({s.payout_cap_fmml:,}) — pagamento seria cortado"
            )

    # ---- veredicto --------------------------------------------------------
    print("\n" + "=" * 62)
    for w in warnings:
        print(f"AVISO: {w}")
    if problems:
        print(f"FALHOU — {len(problems)} problema(s):")
        for p in problems:
            print(f"  - {p}")
        print("=" * 62)
        return 1
    print("TUDO OK — pronto para /launch 🐸")
    print("=" * 62)
    return 0


if __name__ == "__main__":
    sys.exit(main())
