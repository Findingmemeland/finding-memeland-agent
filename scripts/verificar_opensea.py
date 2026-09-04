#!/usr/bin/env python3
"""Mede a forma REAL da API v2 da OpenSea contra o que o adaptador do
refresh (OpenSeaContractLister) assume da documentação — a lição da Rarible
(`text` vs `fullText`) e do BaseScan: forma documentada não é forma medida.

Usa contratos verdadeiros do candidatos_alvo.json e a OPENSEA_API_KEY do
.env. Corre do raiz do repo:

    .venv/bin/python scripts/verificar_opensea.py

Imprime, por contrato: o código HTTP, os campos presentes em cada NFT, se
`identifier`/`name` existem com esses nomes, se a paginação traz `next`, e
os headers de rate limit. No fim, veredicto: o adaptador serve como está ou
há campos a corrigir.
"""

from __future__ import annotations

import json
import sys
import urllib.error
import urllib.request

from medir_universo_alvos import _SSL_CTX, read_env  # mesmo directório

BASE = "https://api.opensea.io"

# Cloudflare 1010 barra o urllib sem User-Agent. Testamos do mais honesto
# para o mais browser, e reportamos qual passou — o adaptador de produção
# usa depois o primeiro que funcionar.
UAS = [
    ("probe", "fml-refresh-probe/1.0"),
    ("browser", "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/127.0 Safari/537.36"),
]


def call(key: str, contract: str) -> None:
    url = f"{BASE}/api/v2/chain/base/contract/{contract}/nfts?limit=3"
    payload = rl = None
    for label, ua in UAS:
        req = urllib.request.Request(url, headers={
            "X-API-KEY": key, "Accept": "application/json", "User-Agent": ua})
        try:
            with urllib.request.urlopen(req, timeout=25, context=_SSL_CTX) as r:
                payload = json.load(r)
                rl = {k: v for k, v in r.headers.items()
                      if k.lower().startswith("x-ratelimit")}
            print(f"  passou com User-Agent '{label}'")
            break
        except urllib.error.HTTPError as e:
            body = e.read(300).decode("utf-8", "replace") if e.fp else ""
            print(f"  UA '{label}': HTTP {e.code} {e.reason} — {body[:120]}")
        except Exception as e:  # noqa: BLE001
            print(f"  UA '{label}': excepção {type(e).__name__}: {e}")
    if payload is None:
        return

    nfts = payload.get("nfts")
    print(f"  HTTP 200 | chave 'nfts': {'sim' if isinstance(nfts, list) else 'NÃO'} "
          f"({len(nfts) if isinstance(nfts, list) else '—'} itens) | "
          f"'next': {'sim' if payload.get('next') else 'não/vazio'}")
    if rl:
        print(f"  rate limit: {rl}")
    for nft in (nfts or [])[:2]:
        fields = sorted(nft.keys())
        ident = nft.get("identifier")
        name = nft.get("name")
        print(f"  · identifier={ident!r} name={name!r}")
        print(f"    campos: {', '.join(fields)}")
    ok_id = all("identifier" in n for n in (nfts or []))
    ok_nm = all("name" in n for n in (nfts or []))
    print(f"  veredicto parcial: identifier {'OK' if ok_id else 'FALTA'} | "
          f"name {'OK' if ok_nm else 'FALTA'}")


def main() -> int:
    key = read_env("OPENSEA_API_KEY") or ""
    if not key:
        print("OPENSEA_API_KEY não encontrada no .env.")
        return 2
    try:
        with open("candidatos_alvo.json", encoding="utf-8") as f:
            cands = json.load(f)
    except OSError:
        print("candidatos_alvo.json não encontrado — corre do raiz do repo.")
        return 2
    contracts = list(dict.fromkeys(c["contract"] for c in cands))[:3]
    for c in contracts:
        print(f"\ncontrato {c}:")
        call(key, c)
    print("\nSe os três disserem 'identifier OK | name OK' e 'nfts: sim', o "
          "adaptador do refresh serve tal como está.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
