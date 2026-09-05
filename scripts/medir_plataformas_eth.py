#!/usr/bin/env python3
"""Censo da condição 2 — todas as plataformas ETH, com custódia medida no
mesmo passo (sugestão Opus 05/09: o EOA é o filtro com maior variância entre
plataformas; medir ao 4.º contrato é barato, no fim é refazer o censo).

Por plataforma (contratos verificados contra fonte, 05/09):
  · dimensão EXACTA (totalSupply)
  · amostra de 30 tokens: nome-base (filtro v5) → DONO-EOA (filtro) →
    unicidade OpenSea nos primeiros 10 nomeados (forma medida 05/09;
    parser validado contra resposta crua)
  · dormência do dono: COLUNA INFORMATIVA, por instrumentar (precisa de
    Etherscan API p/ recência; guarda-se o endereço do dono no JSON para
    medir em lote depois, se o Opus reverter a despromoção de 04/09)
  · escreve candidatos_<plataforma>.json (nomeados+EOA+únicos) para o
    testar_escrevibilidade.py medir a taxa POR PLATAFORMA

Corre do raiz do repo (OPENSEA_API_KEY no .env; ~10-15 min):

    .venv/bin/python scripts/medir_plataformas_eth.py
"""

from __future__ import annotations

import argparse
import json
import random
import re
import sys
import time

from amostrar_foundation import (  # mesmo directório — parser/pesquisa medidos
    ETH_RPCS,
    SEL_TOKENBYINDEX,
    SEL_TOTAL,
    diagnose,
    opensea_search,
    pick_eth_rpc,
)
from medir_universo_alvos import (
    SEL_TOKENURI,
    decode_string_result,
    name_ok,
    normalize_name,
    read_env,
    resolve_token_uri,
)

SEL_OWNEROF = "0x6352211e"

# (slug, contrato, nota) — endereços verificados 04-05/09.
# rarible721 (0xd07d…2430) PARQUEADO: ids esparsos sem enumeração (captura
# 05/09: tokenURI(1)/ownerOf(1) revertem) — precisa de descoberta por logs.
PLATFORMS = [
    ("foundation",  "0x3b3ee1931dc30c1957379fac9aba94d1c48a5405", "censo 04/09"),
    ("superrare2",  "0xb932a70a57673d89f4acffbe830e8ed7f75fb9e0", "censo 04/09"),
    ("superrare1",  "0x41a322b28d0ff354040e2cbc676f0320d8c8850d", "censo 04/09"),
    ("knownorigin", "0xfbeef911dc5821886e1dda71586d90ed28174b7d", "censo 04/09"),
    ("makersplace", "0x2963ba471e265e5f51cafafca78310fe87f8e6d1", "Etherscan 05/09"),
    ("asyncart2",   "0xb6dae651468e9593e4581705a09c10a76ac1e0c8", "Etherscan 05/09"),
    ("asyncart1",   "0x6c424c25e9f1fff9642cb5b7750b0db7312c29ad", "Etherscan 05/09"),
]

# --------------------------------------------------------------------------- #
# Resolução flexível — formas MEDIDAS na captura de 05/09                      #
# --------------------------------------------------------------------------- #

_BARE_CID = re.compile(r"^(Qm[1-9A-HJ-NP-Za-km-z]{44}|baf[a-zA-Z0-9]{20,})")
_IPFS_PATH = re.compile(r"/ipfs/([^\s\"']+)")


def resolve_flexible(uri: str | None) -> dict | None:
    """Cobre o que a captura mostrou: CID nu (Async), gateway morto com CID
    no caminho (SuperRare/ipfs.pixura.io → refaz via gateway genérico), e o
    resto pelo resolvedor normal."""
    if not uri:
        return None
    u = uri.strip()
    if _BARE_CID.match(u):
        return resolve_token_uri("ipfs://" + u)
    if u.startswith("http"):
        m = _IPFS_PATH.search(u)
        if m:
            got = resolve_token_uri("ipfs://" + m.group(1))
            if got is not None:
                return got
    return resolve_token_uri(u)


def meta_name_image(meta) -> tuple[str, str]:
    """Nome e imagem com tolerância de esquema MEDIDA: a MakersPlace usa
    'title'+'imageUrl' (captura 05/09), outros 'name'+'image'."""
    if not isinstance(meta, dict):
        return "", ""
    name = str(meta.get("name") or meta.get("title") or "").strip()
    image = str(meta.get("image") or meta.get("imageUrl")
                or meta.get("image_url") or meta.get("animation_url") or "")
    return name, image


def estimate_max_id(rpc, contract, cap: int = 4_000_000) -> int | None:
    """Dimensão sem totalSupply (MakersPlace): maior tokenId existente por
    duplicação + busca binária sobre ownerOf. Tolerante a buracos (burns):
    um id 'existe' se ele ou um dos 2 seguintes responder."""
    def exists(tid: int) -> bool:
        for t in (tid, tid + 1, tid + 2):
            try:
                data = eth_call(rpc, contract,
                                SEL_OWNEROF + t.to_bytes(32, "big").hex())
                if data and data != "0x":
                    return True
            except Exception:  # noqa: BLE001
                continue
        return False

    if not exists(1):
        return None
    hi = 1
    while hi < cap and exists(hi * 2):
        hi *= 2
    lo, hi = hi, min(hi * 2, cap)
    while lo + 3 < hi:
        mid = (lo + hi) // 2
        if exists(mid):
            lo = mid
        else:
            hi = mid
    return lo


def eth_call(rpc, to, data, tries=1):
    return rpc("eth_call", [{"to": to, "data": data}, "latest"], tries=tries)


def sample_platform(rpc, key, slug, contract, *, sample, uniq_n, rng):
    estimated = False
    try:
        supply = int(eth_call(rpc, contract, SEL_TOTAL, tries=2), 16)
        print(f"  totalSupply = {supply:,}")
    except Exception:  # noqa: BLE001 — sem enumeração: estima por maxId
        supply = estimate_max_id(rpc, contract)
        if not supply:
            print("  sem totalSupply nem token 1 — plataforma saltada")
            return None
        estimated = True
        print(f"  dimensão ~{supply:,} (estimada por maxId; sem totalSupply)")

    named, meta_bad, name_bad, eoa_no, eoa_unk = [], 0, 0, 0, 0
    tried = 0
    first_rejected = None            # auto-diagnóstico p/ zeros mudos
    while len(named) < sample and tried < sample * 4:
        tried += 1
        idx = rng.randrange(supply)
        try:
            tid = int(eth_call(rpc, contract,
                               SEL_TOKENBYINDEX + idx.to_bytes(32, "big").hex()), 16)
        except Exception:  # noqa: BLE001
            tid = idx + 1
        try:
            uri = decode_string_result(eth_call(
                rpc, contract, SEL_TOKENURI + tid.to_bytes(32, "big").hex()))
            meta = resolve_flexible(uri)
        except Exception:  # noqa: BLE001
            meta = None
        name, image = meta_name_image(meta)
        if not (name and image):
            meta_bad += 1
            if first_rejected is None and isinstance(meta, dict):
                first_rejected = sorted(meta.keys())
            continue
        base = normalize_name(name)
        if not name_ok(base):
            name_bad += 1
            continue
        # ---- dono-EOA: o filtro de custódia, medido AQUI (Opus) ---------- #
        try:
            data = eth_call(rpc, contract,
                            SEL_OWNEROF + tid.to_bytes(32, "big").hex())
            owner = "0x" + data[-40:]
            code = rpc("eth_getCode", [owner, "latest"], tries=1)
            is_eoa = code in ("0x", "0x0", "", None)
        except Exception:  # noqa: BLE001
            eoa_unk += 1
            continue                      # inverificável não entra na taxa
        if not is_eoa:
            eoa_no += 1
            continue
        named.append({
            "chain": "ethereum", "contract": contract, "tokenId": tid,
            "name": base, "name_onchain": name,
            "description": str(meta.get("description") or "")[:600],
            "image": str(meta.get("image") or ""),
            "platform": slug,
            "owner": owner,               # p/ dormência em lote, se revertida
        })

    if not named and not eoa_no and first_rejected is not None:
        print(f"  (0 nomeados — chaves do 1.º metadata rejeitado: "
              f"{', '.join(first_rejected)[:160]})")
    n_named = len(named) + eoa_no        # com nome válido e EOA verificado
    r_name = (len(named) + eoa_no) / max(len(named) + eoa_no + name_bad + meta_bad, 1)
    r_eoa = len(named) / max(n_named, 1)

    # ---- unicidade nos primeiros uniq_n aprovados ------------------------- #
    uniq_yes = uniq_tested = 0
    for c in named[:uniq_n]:
        results = opensea_search(key, c["name"])
        time.sleep(1.2)
        if not results:
            continue                      # inverificável — fora da taxa
        want = f"{contract}:{c['tokenId']}".lower()
        same = [i.lower() for i, nm in results
                if normalize_name(nm).casefold() == c["name"].casefold()]
        uniq_tested += 1
        uniq_yes += (len(same) == 1 and same[0] == want)
        c["name_unique"] = (len(same) == 1 and same[0] == want)
    r_uniq = uniq_yes / uniq_tested if uniq_tested else None

    keep = [c for c in named if c.get("name_unique") is not False]
    out = f"candidatos_{slug}.json"
    with open(out, "w", encoding="utf-8") as f:
        json.dump(keep, f, ensure_ascii=False, indent=1)

    eff = None
    if r_uniq is not None:
        eff = int(supply * r_name * r_eoa * r_uniq)
    print(f"  nome-base ok {r_name:.0%} | dono-EOA {r_eoa:.0%} "
          f"(contratos: {eoa_no}, inverificáveis: {eoa_unk}) | "
          f"unicidade {r_uniq:.0%} ({uniq_tested} testados)"
          if r_uniq is not None else
          f"  nome-base ok {r_name:.0%} | dono-EOA {r_eoa:.0%} | "
          f"unicidade inverificável")
    if eff is not None:
        print(f"  PRÉ-ESCREVIBILIDADE: ~{eff:,} "
              f"(× taxa do testar_escrevibilidade.py {out})")
    return {"slug": slug, "supply": supply, "r_name": r_name, "r_eoa": r_eoa,
            "r_uniq": r_uniq, "pre_writability": eff, "out": out}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--sample", type=int, default=30)
    ap.add_argument("--uniq", type=int, default=10)
    ap.add_argument("--seed", type=int, default=None)
    ap.add_argument("--only", default="",
                    help="slugs separados por vírgula (ex.: makersplace,asyncart2)")
    args = ap.parse_args()
    rng = random.Random(args.seed)

    key = read_env("OPENSEA_API_KEY") or ""
    if not key or not diagnose(key):
        return 1
    rpc = pick_eth_rpc()

    wanted = {s.strip() for s in args.only.split(",") if s.strip()}
    rows = []
    for slug, contract, fonte in PLATFORMS:
        if wanted and slug not in wanted:
            continue
        print(f"\n=== {slug} ({fonte}) ===")
        row = sample_platform(rpc, key, slug, contract,
                              sample=args.sample, uniq_n=args.uniq, rng=rng)
        if row:
            rows.append(row)

    print("\n================= RESUMO (condição 2) =================")
    total = 0
    for r in rows:
        eff = r["pre_writability"]
        total += eff or 0
        print(f"  {r['slug']:12} supply {r['supply']:>8,} → "
              f"pré-escrevibilidade ~{(eff or 0):>8,}")
    print(f"  {'TOTAL':12} {'':>8} → ~{total:,} (antes da escrevibilidade "
          "por plataforma; fasquia: efectivo ≥100k)")
    print("Dormência: coluna informativa por instrumentar; owners guardados "
          "nos candidatos_*.json para medição em lote se for revertida.")
    print(f"chamadas RPC: {rpc.calls}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
