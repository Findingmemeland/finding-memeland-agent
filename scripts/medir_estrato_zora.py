#!/usr/bin/env python3
"""Censo do estrato Zora 721 em Base — a primeira plataforma da época 1.

Ao contrário da amostragem cega (esgotada, Veredicto_Passo0), isto é um
CENSO: todos os drops 721 da Zora em Base nascem no factory
ZORA_NFT_CREATOR_PROXY (0x899c…f4b8, fonte: repo oficial zora-drops-contracts,
addresses/8453.json) e cada criação emite

    CreatedDrop(address indexed creator, address indexed edition, uint256 editionSize)
    topic0 = 0xad59ebba8bfb06ba01a615a611467ca3bef86a275bd5e9704d3b295112550ba5
    (keccak verificado contra o topic conhecido do Transfer)

Uma passagem de eth_getLogs sobre o factory dá o número EXACTO de drops, a
distribuição de tamanhos de edição (1 = 1/1 verdadeiro; uint64.max = open
edition) e a idade de cada um. Chain-native: zero quota de marketplace.

Depois amostra 1/1s elegíveis (>180 dias), resolve o metadata canónico e
aplica o filtro de nome-base; escreve candidatos_zora.json para o
testar_escrevibilidade.py medir a taxa DENTRO do estrato — a segunda metade
do gate.

Corre do raiz do repo (BASE_RPC_URL no .env; Alchemy recomendado):

    .venv/bin/python scripts/medir_estrato_zora.py [--sample 40]
"""

from __future__ import annotations

import argparse
import json
import random
import sys
import time

from medir_universo_alvos import (  # mesmo directório
    SEL_TOKENURI,
    Rpc,
    decode_string_result,
    hex_int,
    name_ok,
    normalize_name,
    read_env,
    resolve_token_uri,
)

FACTORY = "0x899ce31df6c6af81203acaad285bf539234ef4b8"
TOPIC_CREATED_DROP = ("0xad59ebba8bfb06ba01a615a611467ca3bef86a275bd5e9704d"
                      "3b295112550ba5")
OPEN_EDITION = (1 << 64) - 1          # type(uint64).max
BLOCKS_180D = 180 * 86400 // 2        # Base: ~2s/bloco

# Endpoints públicos para VARRER LOGS (a Alchemy gratuita corta getLogs a 10
# blocos — medido 04/09; fica só para eth_call/metadata). Cada um tem um
# limite de janela não documentado: sonda-se e usa-se o mais largo.
LOGS_RPCS = [
    "https://base-rpc.publicnode.com",
    "https://base.llamarpc.com",
    "https://base.drpc.org",
    "https://mainnet.base.org",
]
WINDOWS = [1_000_000, 250_000, 50_000, 10_000, 2_000]


def probe_logs_rpcs(head: int) -> list[list]:
    """Sonda cada endpoint com janelas decrescentes; devolve TODOS os que
    aceitam alguma janela, como [rpc, janela, cooldown_até]. O varrimento
    roda por todos — o limite de ritmo de um endpoint não pára o censo."""
    probe_from = head - 4_000_000
    workers: list[list] = []
    for url in LOGS_RPCS:
        rpc = Rpc(url)
        for win in WINDOWS:
            try:
                rpc("eth_getLogs", [{
                    "address": FACTORY,
                    "topics": [TOPIC_CREATED_DROP],
                    "fromBlock": hex(probe_from),
                    "toBlock": hex(probe_from + win - 1),
                }], tries=1)
            except Exception:  # noqa: BLE001
                continue
            print(f"  logs via {url} (janela {win:,} blocos)")
            workers.append([rpc, win, 0.0])
            break
    if not workers:
        raise SystemExit("nenhum RPC público aceitou janelas de logs — "
                         "tenta mais tarde ou usa uma conta paga")
    return workers


def fetch_drops(workers: list[list],
                to_block: int) -> list[tuple[int, str, int]]:
    """(bloco, contrato_do_drop, edition_size) para TODOS os CreatedDrop."""
    drops: list[tuple[int, str, int]] = []
    start, calls, wi = 1, 0, 0
    while start <= to_block:
        # próximo worker fora de cooldown; se todos arrefecem, espera o mínimo
        now = time.time()
        ready = [w for w in workers if w[2] <= now]
        if not ready:
            time.sleep(max(0.5, min(w[2] for w in workers) - now))
            continue
        worker = ready[wi % len(ready)]
        wi += 1
        rpc, window, _ = worker
        calls += 1
        end = min(start + window - 1, to_block)
        try:
            logs = rpc("eth_getLogs", [{
                "address": FACTORY,
                "topics": [TOPIC_CREATED_DROP],
                "fromBlock": hex(start), "toBlock": hex(end),
            }], tries=1)
        except Exception as e:  # noqa: BLE001
            msg = str(e)
            if "429" in msg or "rate limit" in msg.lower():
                worker[2] = time.time() + 20.0     # arrefece; outro continua
            elif worker[1] > 2_000:
                worker[1] //= 2                    # janela grande p/ ESTE rpc
            else:
                worker[2] = time.time() + 60.0     # doente; volta mais tarde
            continue                               # o troço fica para repetir
        for lg in logs or []:
            edition = "0x" + lg["topics"][2][-40:]
            size = int(lg["data"], 16) if lg.get("data") and lg["data"] != "0x" else 0
            drops.append((hex_int(lg["blockNumber"]), edition.lower(), size))
        start = end + 1
        if calls % 25 == 0:
            pct = 100 * end / to_block
            print(f"  …{pct:4.1f}% ({end:,}/{to_block:,}) | {len(drops)} drops")
    return drops


def bucket(size: int) -> str:
    if size == OPEN_EDITION or size == 0:
        return "open/ilimitada"
    if size == 1:
        return "1 (1/1)"
    if size <= 10:
        return "2-10"
    if size <= 100:
        return "11-100"
    return ">100"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--sample", type=int, default=40)
    ap.add_argument("--out", default="candidatos_zora.json")
    ap.add_argument("--seed", type=int, default=None)
    args = ap.parse_args()
    rng = random.Random(args.seed)

    # rpc do .env (Alchemy): eth_call/metadata. Logs: endpoint público sondado.
    rpc = Rpc(read_env("BASE_RPC_URL") or "https://mainnet.base.org")
    head = hex_int(rpc("eth_blockNumber", []))
    cutoff = head - BLOCKS_180D
    print(f"factory Zora 721: {FACTORY}\nhead={head:,}  elegível até ao "
          f"bloco {cutoff:,} (>180 dias)\na sondar RPCs públicos para logs…")
    workers = probe_logs_rpcs(head)

    drops = fetch_drops(workers, head)
    print(f"\nCENSO: {len(drops):,} drops 721 criados via factory em Base")

    dist: dict[str, int] = {}
    for _, _, size in drops:
        dist[bucket(size)] = dist.get(bucket(size), 0) + 1
    for k in sorted(dist):
        print(f"  {k:14} {dist[k]:,}")

    eligible = [(b, c, s) for b, c, s in drops if b <= cutoff]
    ones = [(b, c) for b, c, s in eligible if s == 1]
    small = [(b, c, s) for b, c, s in eligible if 1 <= s <= 10 and s != 0]
    print(f"\nelegíveis (>180d): {len(eligible):,} drops | "
          f"1/1 verdadeiros: {len(ones):,} | edições 1-10: {len(small):,}")

    # ---- amostra de 1/1s: nome canónico + filtro de nome-base ------------- #
    rng.shuffle(ones)
    sample = ones[: args.sample]
    kept, no_token, bad_name = [], 0, 0
    for i, (_, contract) in enumerate(sample, 1):
        try:
            data = rpc("eth_call", [{
                "to": contract,
                "data": SEL_TOKENURI + (1).to_bytes(32, "big").hex(),
            }, "latest"], tries=2)
            uri = decode_string_result(data)
            meta = resolve_token_uri(uri) if uri else None
        except Exception:  # noqa: BLE001
            meta = None
        if not (isinstance(meta, dict) and meta.get("image")):
            no_token += 1
            continue
        name = str(meta.get("name") or "").strip()
        base = normalize_name(name)
        mark = "✅" if name_ok(base) else "—"
        print(f"[{i:2}/{len(sample)}] {mark} {base[:52]!r}")
        if not name_ok(base):
            bad_name += 1
            continue
        kept.append({
            "chain": "base", "contract": contract, "tokenId": 1,
            "name": base, "name_onchain": name,
            "description": str(meta.get("description") or "")[:600],
            "image": str(meta.get("image") or ""),
            "platform": "zora-721-drop",
        })
        time.sleep(0.1)

    print("\n================= RESULTADO =================")
    n = len(sample)
    if n:
        print(f"amostra de 1/1s ({n}): sem token/metadata {no_token} | "
              f"nome-base reprovado {bad_name} | candidatos {len(kept)} "
              f"({len(kept) / n:.0%})")
        est = int(len(ones) * len(kept) / n)
        print(f"estrato Zora estimado (1/1, >180d, nome ok): ~{est:,}")
        print("gate: este número × taxa do testar_escrevibilidade.py sobre "
              f"{args.out} (+ unicidade, que aqui tende a ser alta — 1/1s "
              "com nome próprio). Fasquia: ≥100k verde / <20k vermelho.")
    with open(args.out, "w", encoding="utf-8") as f:
        json.dump(kept, f, ensure_ascii=False, indent=1)
    print(f"{len(kept)} candidatos → {args.out}")
    logs_calls = sum(w[0].calls for w in workers)
    print(f"chamadas RPC: {logs_calls} (logs) + {rpc.calls} (metadata)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
