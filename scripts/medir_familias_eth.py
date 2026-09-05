#!/usr/bin/env python3
"""Mapa de famílias de bytecode da ERA 1/1 em Ethereum (2021 — meados 2022):
o instrumento que mede a Manifold-2021 sem enumerar contratos.

Mesmo método do medir_familias_base (que desmascarou Base em 10 min):
amostra de blocos da era → mints ERC-721 → eth_getCode por contrato →
agrupar por hash do bytecode. A família com MUITOS CONTRATOS (muitos
artistas) e nomes ✅ é o estrato Manifold/artistas; a dimensão estima-se por
mints-da-era × fatia da família × taxas da amostra.

Corre do raiz do repo (~10-15 min; RPC público de ETH, zero marketplace):

    .venv/bin/python scripts/medir_familias_eth.py [--blocks 300]
"""

from __future__ import annotations

import argparse
import hashlib
import json
import random
import sys
import time

from amostrar_foundation import pick_eth_rpc
from medir_plataformas_eth import meta_name_image, resolve_flexible
from medir_universo_alvos import (
    SEL_TOKENURI,
    TRANSFER,
    ZERO32,
    Rpc,
    decode_string_result,
    hex_int,
    name_ok,
    normalize_name,
    read_env,
)

# era 1/1 em ETH: ~Jan 2021 (bloco ~11,5M) a ~Jun 2022 (bloco ~15,0M)
ERA_LO, ERA_HI = 11_500_000, 15_000_000


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--blocks", type=int, default=300)
    ap.add_argument("--names-per-family", type=int, default=5)
    ap.add_argument("--top", type=int, default=12)
    ap.add_argument("--seed", type=int, default=None)
    args = ap.parse_args()
    rng = random.Random(args.seed)

    # Logs de 2021 exigem nó de ARQUIVO: os RPCs públicos falharam (medido
    # 05/09, 300/300 blocos). Preferir ETH_RPC_URL (app Alchemy de ETH —
    # o arquivo dela foi o que fez o mapa de Base funcionar à primeira).
    eth_url = read_env("ETH_RPC_URL")
    if eth_url:
        rpc = Rpc(eth_url)
        print(f"RPC ETH (arquivo): {eth_url.split('/v2/')[0]}/v2/…")
    else:
        print("⚠️ ETH_RPC_URL não está no .env — a usar RPC público, que "
              "provavelmente NÃO serve logs históricos de 2021.")
        rpc = pick_eth_rpc()
    span = ERA_HI - ERA_LO
    print(f"era 1/1: blocos [{ERA_LO:,}, {ERA_HI:,}] "
          f"({args.blocks} blocos ao acaso)")

    # ---- fase 1: mints ERC-721 na era ------------------------------------- #
    pool: list[tuple[str, int]] = []
    per_block: list[int] = []
    first_err_shown = False
    for i in range(args.blocks):
        b = rng.randint(ERA_LO, ERA_HI)
        try:
            logs = rpc("eth_getLogs", [{
                "fromBlock": hex(b), "toBlock": hex(b),
                "topics": [TRANSFER, ZERO32],
            }])
        except Exception as e:  # noqa: BLE001
            if not first_err_shown:
                print(f"  (getLogs falhou no bloco {b:,}: {str(e)[:160]})")
                first_err_shown = True
            continue
        mints = [(l["address"].lower(), hex_int(l["topics"][3]))
                 for l in logs or [] if len(l.get("topics", [])) == 4]
        per_block.append(len(mints))
        pool.extend(mints)
        if (i + 1) % 50 == 0:
            print(f"  fase 1: {i + 1}/{args.blocks} blocos… ({len(pool)} mints)")
    if not per_block:
        print("sem dados — RPC inutilizável")
        return 2
    mean = sum(per_block) / len(per_block)
    era_total = int(mean * span)
    contracts = sorted({c for c, _ in pool})
    print(f"\n{len(pool)} mints amostrados | {len(contracts)} contratos | "
          f"média {mean:.2f} mints/bloco → ~{era_total:,} mints ERC-721 na era")

    # ---- fase 2: famílias por bytecode ------------------------------------ #
    fam_of: dict[str, tuple[str, int]] = {}
    for j, c in enumerate(contracts, 1):
        try:
            code = rpc("eth_getCode", [c, "latest"], tries=2)
        except Exception:  # noqa: BLE001
            continue
        raw = bytes.fromhex(code[2:]) if code and code != "0x" else b""
        fam_of[c] = (hashlib.sha256(raw).hexdigest()[:10], len(raw))
        if j % 40 == 0:
            print(f"  fase 2: {j}/{len(contracts)} contratos…")

    fams: dict[str, dict] = {}
    for (c, tid) in pool:
        if c not in fam_of:
            continue
        h, ln = fam_of[c]
        f = fams.setdefault(h, {"len": ln, "contracts": set(), "mints": 0,
                                "examples": []})
        f["contracts"].add(c)
        f["mints"] += 1
        if len(f["examples"]) < 40:
            f["examples"].append((c, tid))
    total_mints = sum(f["mints"] for f in fams.values())

    # ---- fase 3: nomes por família, DUAS vistas --------------------------- #
    def sample_names(f, n, spread_contracts):
        """Amostra nomes; spread_contracts=True tira de contratos DISTINTOS
        (vista de artistas: nomes distintos entre contratos = 1/1s)."""
        rng.shuffle(f["examples"])
        picked, seen_c = [], set()
        for c, tid in f["examples"]:
            if spread_contracts and c in seen_c:
                continue
            picked.append((c, tid))
            seen_c.add(c)
            if len(picked) >= n:
                break
        out = []
        for c, tid in picked:
            try:
                uri = decode_string_result(rpc("eth_call", [{
                    "to": c,
                    "data": SEL_TOKENURI + tid.to_bytes(32, "big").hex(),
                }, "latest"], tries=1))
                meta = resolve_flexible(uri)
            except Exception:  # noqa: BLE001
                meta = None
            name, _ = meta_name_image(meta)
            if name:
                out.append(normalize_name(name))
            time.sleep(0.05)
        return out

    def show_family(h, f, spread):
        share = f["mints"] / max(total_mints, 1)
        print(f"\nfamília {h} | {f['len']} bytes | {len(f['contracts'])} "
              f"contratos | {f['mints']} mints ({share:.0%})")
        names = sample_names(f, args.names_per_family, spread)
        ok = sum(name_ok(b) for b in names)
        for b in names:
            print(f"   {'✅' if name_ok(b) else '—'} {b[:52]!r}")
        if not names:
            print("   (sem nomes legíveis)")
            return
        if len(set(n.casefold() for n in names)) == 1 and len(names) > 1:
            print("   ⚠️ MESMO nome repetido = colecção → morre na unicidade; "
                  "estimativa irrelevante para o pool")
            return
        est = int(era_total * share * ok / len(names))
        print(f"   nomes ok {ok}/{len(names)} → bruto da família na era: "
              f"~{est:,} (antes de unicidade/EOA/escrevibilidade)")

    print("\n========== VISTA A: por MINTS (colecções dominam) ==========")
    for h, f in sorted(fams.items(), key=lambda kv: -kv[1]["mints"])[:6]:
        show_family(h, f, spread=False)

    print("\n========== VISTA B: por CONTRATOS (artistas) ==========")
    multi = [(h, f) for h, f in fams.items() if len(f["contracts"]) >= 2]
    for h, f in sorted(multi, key=lambda kv: -len(kv[1]["contracts"]))[: args.top]:
        show_family(h, f, spread=True)

    # cauda: contratos com 1-2 mints na amostra = actividade de artista avulso
    per_contract: dict[str, int] = {}
    for c, _ in pool:
        per_contract[c] = per_contract.get(c, 0) + 1
    tail_contracts = [c for c, n in per_contract.items() if n <= 2]
    tail_mints = sum(per_contract[c] for c in tail_contracts)
    print(f"\nCAUDA (contratos com ≤2 mints na amostra): "
          f"{len(tail_contracts)}/{len(per_contract)} contratos, "
          f"{tail_mints} mints ({tail_mints / max(total_mints, 1):.0%} da era "
          f"≈ ~{int(era_total * tail_mints / max(total_mints, 1)):,} tokens) — "
          "é aqui que vivem os contratos próprios de artistas.")

    with open("familias_eth.json", "w", encoding="utf-8") as fjs:
        json.dump({h: {"len": f["len"], "contracts": sorted(f["contracts"]),
                       "mints": f["mints"], "examples": f["examples"][:20]}
                   for h, f in fams.items()}, fjs, ensure_ascii=False, indent=1)
    print("\nfamílias completas → familias_eth.json (para análise sem re-correr)")
    print(f"chamadas RPC: {rpc.calls}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
