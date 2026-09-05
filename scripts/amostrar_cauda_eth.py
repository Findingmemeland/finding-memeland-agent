#!/usr/bin/env python3
"""Amostra de nomes da CAUDA do mapa de famílias ETH (contratos com ≤2 mints
na era 2021 — os contratos próprios de artistas). Lê familias_eth.json do run
anterior: zero re-varrimento, só resolução de nomes (~40 chamadas, 2-3 min).

    .venv/bin/python scripts/amostrar_cauda_eth.py [--sample 40]
"""

from __future__ import annotations

import argparse
import json
import random
import sys
import time

from medir_plataformas_eth import meta_name_image, resolve_flexible
from medir_universo_alvos import (
    SEL_TOKENURI,
    Rpc,
    decode_string_result,
    name_ok,
    normalize_name,
    read_env,
)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--sample", type=int, default=40)
    ap.add_argument("--seed", type=int, default=None)
    args = ap.parse_args()
    rng = random.Random(args.seed)

    url = read_env("ETH_RPC_URL")
    if not url:
        print("ETH_RPC_URL não está no .env.")
        return 2
    rpc = Rpc(url)

    with open("familias_eth.json", encoding="utf-8") as f:
        fams = json.load(f)
    # cauda = famílias cujo nº de mints amostrados ≤ 2×nº de contratos e ≤4
    tail_tokens = []
    for h, fam in fams.items():
        if fam["mints"] <= max(2 * len(fam["contracts"]), 4) and fam["mints"] <= 4:
            tail_tokens.extend((c, t) for c, t in fam["examples"])
    print(f"cauda: {len(tail_tokens)} tokens de contratos avulsos no JSON")
    rng.shuffle(tail_tokens)

    ok = shown = repeated = 0
    seen_names: dict[str, int] = {}
    for c, tid in tail_tokens:
        if shown >= args.sample:
            break
        try:
            uri = decode_string_result(rpc("eth_call", [{
                "to": c, "data": SEL_TOKENURI + int(tid).to_bytes(32, "big").hex(),
            }, "latest"], tries=1))
            meta = resolve_flexible(uri)
        except Exception:  # noqa: BLE001
            meta = None
        name, _ = meta_name_image(meta)
        if not name:
            continue
        shown += 1
        base = normalize_name(name)
        good = name_ok(base)
        ok += good
        seen_names[base.casefold()] = seen_names.get(base.casefold(), 0) + 1
        print(f"[{shown:2}/{args.sample}] {'✅' if good else '—'} {base[:52]!r}")
        time.sleep(0.05)

    repeated = sum(1 for v in seen_names.values() if v > 1)
    print("\n================= RESULTADO =================")
    if shown:
        print(f"nomes-base 2+ palavras na CAUDA: {ok}/{shown} ({ok / shown:.0%})"
              f" | nomes repetidos dentro da amostra: {repeated}")
        print("Esta taxa × ~5,2M brutos da cauda × unicidade × EOA × "
              "escrevibilidade fecha a conta do gate.")
    else:
        print("nenhum nome resolvido — metadata da cauda inacessível.")
    print(f"chamadas RPC: {rpc.calls}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
