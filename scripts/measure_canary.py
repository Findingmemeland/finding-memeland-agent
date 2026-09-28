#!/usr/bin/env python3
"""measure_canary.py — mede o bloco-canário do /harvest, por cadeia.

PARA QUE SERVE. O /harvest lê mints de blocos ao calhas. Um RPC que devolve
listas vazias — nó sem arquivo, filtro errado, provedor a truncar em
silêncio — é INDISTINGUÍVEL de "neste bloco não houve mints". Sem uma prova
contra isso, a colheita pode passar semanas a devolver zero com ar saudável.

A prova é o canário: um bloco FIXO cuja contagem de mints ERC-721 se conhece,
MEDIDA, nunca estimada. Antes de cada colheita o bot relê esse bloco e exige
EXACTAMENTE a mesma contagem. Igualdade, não ">= 1": um provedor que trunca
devolve uma página parcial — o bloco TEM mints, só que menos — e passaria um
teste de existência com folga.

PORQUE É QUE TEM DE SER ESTE SCRIPT. Usa o mesmo JsonRpc, o mesmo filtro de
tópicos e o mesmo `mints_in_logs` que a produção. Contar mints à mão num
explorador mediria outra coisa, e o canário recusava-se a passar.

E TEM DE SER COM O RPC DE PRODUÇÃO. O que o Railway usa (Doppler dev).
Corre-o com o .env apontado para o mesmo fornecedor, senão o número pode não
bater certo lá.

Lê o bloco escolhido DUAS VEZES e só o aceita se as duas leituras coincidirem.
Um bloco que dá números diferentes em duas leituras seguidas diz que o
fornecedor não é determinístico — e nesse caso o canário não protegia nada.

    python scripts/measure_canary.py --chain ethereum
    python scripts/measure_canary.py --chain base
    python scripts/measure_canary.py --chain ethereum --block 15000000
"""
from __future__ import annotations

import argparse
import random
import sys

sys.path.insert(0, "src")

from finding_memeland.config import get_settings                    # noqa: E402
from finding_memeland.target.adapters import JsonRpc                # noqa: E402
from finding_memeland.target.harvest import (                       # noqa: E402
    HARVEST_SAFE_DEPTH,
    HARVEST_SPAN_START,
    TRANSFER_TOPIC,
    ZERO_TOPIC,
    mints_in_logs,
)

OK, NO = "✓", "✗"


def _http_post(url: str, body: bytes, headers: dict) -> str:
    import urllib.request
    req = urllib.request.Request(url, data=body, headers=headers, method="POST")
    with urllib.request.urlopen(req, timeout=30) as r:
        return r.read().decode("utf-8", "replace")


def count_mints(node: JsonRpc, block: int) -> int:
    logs = node.get_logs(from_block=block, to_block=block,
                         topics=[TRANSFER_TOPIC, ZERO_TOPIC])
    return len(mints_in_logs(logs))


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--chain", required=True, choices=["ethereum", "base"])
    ap.add_argument("--block", type=int, default=0,
                    help="medir este bloco em vez de procurar um")
    ap.add_argument("--min-mints", type=int, default=3,
                    help="mints mínimos para um bloco servir (default 3)")
    ap.add_argument("--max-mints", type=int, default=200,
                    help="mints máximos — um drop enorme torna o canário lento")
    ap.add_argument("--tries", type=int, default=60)
    ap.add_argument("--seed", type=int, default=0)
    args = ap.parse_args()

    s = get_settings()
    url = {"ethereum": s.eth_rpc_url, "base": s.base_rpc_url}[args.chain]
    if not url:
        print(f"{NO} sem RPC para {args.chain} no .env")
        return 2
    node = JsonRpc(url=url, http_post=_http_post, label=f"canary:{args.chain}")

    print("=" * 66)
    print(f"CANÁRIO DO /harvest — {args.chain}")
    print("=" * 66)

    try:
        latest = int(str(node.call("eth_blockNumber", [])), 16)
    except Exception as e:  # noqa: BLE001
        print(f"{NO} NÃO MEDIDO — o RPC não respondeu: {type(e).__name__}: {e}")
        print("Isto não diz nada sobre a cadeia. Se for certificado:")
        print('  export SSL_CERT_FILE=$(python -c "import certifi; '
              'print(certifi.where())")')
        return 2

    # Bem para trás do que a colheita usa: um canário nunca pode estar
    # sujeito a reorg, senão a contagem muda e a colheita pára sem razão.
    hi = latest - HARVEST_SAFE_DEPTH * 10
    lo = HARVEST_SPAN_START[args.chain]

    if args.block:
        candidates = [args.block]
    else:
        rng = random.Random(args.seed)
        candidates = [rng.randrange(lo, hi) for _ in range(args.tries)]

    chosen, first = 0, 0
    for i, block in enumerate(candidates, 1):
        try:
            n = count_mints(node, block)
        except Exception as e:  # noqa: BLE001
            print(f"  bloco {block:>10,}: não lido ({type(e).__name__})")
            continue
        fits = args.min_mints <= n <= args.max_mints
        if args.block or fits:
            print(f"  bloco {block:>10,}: {n} mint(s) ERC-721"
                  + ("" if fits else "  (fora do intervalo pedido)"))
        if args.block or fits:
            chosen, first = block, n
            break
        if i % 10 == 0:
            print(f"  … {i} blocos vistos, nenhum com "
                  f"{args.min_mints}-{args.max_mints} mints")

    if not chosen:
        print(f"\n{NO} nenhum bloco com {args.min_mints}-{args.max_mints} mints "
              f"em {len(candidates)} tentativas. Sobe --tries ou baixa --min-mints.")
        return 1
    if first == 0:
        print(f"\n{NO} o bloco {chosen:,} tem 0 mints — um canário de zero não "
              "distingue 'não havia' de 'não consegui ler'. Escolhe outro.")
        return 1

    # A SEGUNDA LEITURA. É ela que dá valor ao número.
    try:
        second = count_mints(node, chosen)
    except Exception as e:  # noqa: BLE001
        print(f"\n{NO} a segunda leitura falhou ({type(e).__name__}) — "
              "não aceito um canário lido uma vez só.")
        return 2
    if second != first:
        print(f"\n{NO} o bloco {chosen:,} deu {first} e depois {second} mints. "
              "O fornecedor não é determinístico — um canário assim não protege "
              "nada. Tenta outro bloco, ou outro RPC.")
        return 1

    var = f"HARVEST_CANARY_{args.chain.upper()}"
    print(f"\n{OK} bloco {chosen:,}: {first} mints, igual nas duas leituras.")
    print("\nPõe isto no Doppler (config dev):\n")
    print(f"    {var}={chosen}:{first}")
    print("\nO bloco é imutável: este número não muda nunca. Se um dia o")
    print("/harvest recusar com 'canário falhou', o problema é o RPC, não isto.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
