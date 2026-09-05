#!/usr/bin/env python3
"""Censo de FAMÍLIAS de bytecode em Base — o método do atacante virado
telescópio.

Pergunta do Pedro (04/09): «não é possível a Zora ter poucos e outros
protocolos terem mais? não conseguimos medir todos?» Medir os 125M+ de NFTs
um a um não dá; mas cada plataforma minta por proxies BYTE-IDÊNTICOS (foi
assim que o atacante do Hunt #10 filtrou os nossos relics Manifold — proxy
de 298 bytes). Logo: amostra cega de mints → eth_getCode de cada contrato →
agrupar por hash do bytecode = mapa empírico de QUEM minta em Base, sem
precisar de conhecer factories à partida. Para cada família grande,
amostram-se nomes e mede-se a qualidade — e a que prometer, ganha censo
exacto via factory depois.

Corre do raiz do repo (usa BASE_RPC_URL do .env para eth_call; os logs de
blocos individuais são leves e vão pelo mesmo endpoint):

    .venv/bin/python scripts/medir_familias_base.py [--blocks 300]
"""

from __future__ import annotations

import argparse
import hashlib
import random
import sys
import time

from medir_universo_alvos import (  # mesmo directório
    SEL_TOKENURI,
    TRANSFER,
    ZERO32,
    Rpc,
    decode_string_result,
    hex_int,
    name_ok,
    normalize_name,
    read_env,
    resolve_token_uri,
)

BLOCKS_180D = 180 * 86400 // 2
MIN_AGE = BLOCKS_180D
OLDEST = 1_500_000            # mesma janela do medidor original


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--blocks", type=int, default=300)
    ap.add_argument("--names-per-family", type=int, default=6)
    ap.add_argument("--top", type=int, default=12)
    ap.add_argument("--seed", type=int, default=None)
    args = ap.parse_args()
    rng = random.Random(args.seed)

    rpc = Rpc(read_env("BASE_RPC_URL") or "https://mainnet.base.org")
    head = hex_int(rpc("eth_blockNumber", []))
    lo, hi = OLDEST, head - MIN_AGE
    print(f"janela elegível: [{lo:,}, {hi:,}]  ({args.blocks} blocos ao acaso)")

    # ---- fase 1: mints ERC-721 em blocos aleatórios ----------------------- #
    pool: list[tuple[str, int]] = []
    for i in range(args.blocks):
        b = rng.randint(lo, hi)
        try:
            logs = rpc("eth_getLogs", [{
                "fromBlock": hex(b), "toBlock": hex(b),
                "topics": [TRANSFER, ZERO32],
            }])
        except Exception:  # noqa: BLE001
            continue
        pool.extend((l["address"].lower(), hex_int(l["topics"][3]))
                    for l in logs or [] if len(l.get("topics", [])) == 4)
        if (i + 1) % 50 == 0:
            print(f"  fase 1: {i + 1}/{args.blocks} blocos… "
                  f"({len(pool)} mints)")
    contracts = sorted({c for c, _ in pool})
    print(f"\n{len(pool)} mints ERC-721 amostrados, "
          f"{len(contracts)} contratos distintos")

    # ---- fase 2: impressão digital do bytecode ---------------------------- #
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
    ranked = sorted(fams.items(), key=lambda kv: -kv[1]["mints"])[: args.top]

    # ---- fase 3: nomes por família ---------------------------------------- #
    print("\n================= FAMÍLIAS =================")
    print("(família = bytecode idêntico; 298 bytes = proxy tipo Manifold)")
    for h, f in ranked:
        share = 100 * f["mints"] / max(total_mints, 1)
        tag = "  ← 298b, Manifold?" if f["len"] == 298 else ""
        print(f"\nfamília {h} | {f['len']} bytes | "
              f"{len(f['contracts'])} contratos | {f['mints']} mints "
              f"({share:.0f}% da amostra){tag}")
        rng.shuffle(f["examples"])
        shown = ok = 0
        for c, tid in f["examples"]:
            if shown >= args.names_per_family:
                break
            try:
                data = rpc("eth_call", [{
                    "to": c,
                    "data": SEL_TOKENURI + tid.to_bytes(32, "big").hex(),
                }, "latest"], tries=1)
                uri = decode_string_result(data)
                meta = resolve_token_uri(uri) if uri else None
            except Exception:  # noqa: BLE001
                meta = None
            if not isinstance(meta, dict) or not meta.get("name"):
                continue
            shown += 1
            base = normalize_name(str(meta["name"]))
            good = name_ok(base)
            ok += good
            print(f"   {'✅' if good else '—'} {base[:52]!r}")
            time.sleep(0.05)
        if shown:
            print(f"   nomes-base 2+ palavras: {ok}/{shown}")
        else:
            print("   (sem nomes legíveis na amostra)")

    print(f"\nchamadas RPC: {rpc.calls}")
    print("Leitura: a família que combinar muitos CONTRATOS (muitos artistas)"
          " com nomes ✅ é o estrato a censar a sério; famílias de 1-2 "
          "contratos com milhares de mints são open editions.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
