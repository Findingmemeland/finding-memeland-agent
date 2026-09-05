#!/usr/bin/env python3
"""Captura a resposta CRUA da pesquisa OpenSea para 2 queries e grava em
opensea_search_raw.json — para construir o parser contra a realidade em vez
de suposições. Custa 2 chamadas.

    .venv/bin/python scripts/capturar_opensea_search.py
"""
import json
import sys
import urllib.parse
import urllib.request

from medir_universo_alvos import _SSL_CTX, read_env

key = read_env("OPENSEA_API_KEY") or ""
if not key:
    sys.exit("OPENSEA_API_KEY não encontrada no .env.")

out = {}
for q in ("art", "Calm Night"):
    url = ("https://api.opensea.io/api/v2/search?query="
           + urllib.parse.quote(q) + "&asset_types=nft&limit=10")
    req = urllib.request.Request(url, headers={
        "X-API-KEY": key, "Accept": "application/json",
        "User-Agent": "fml-refresh-probe/1.0"})
    try:
        with urllib.request.urlopen(req, timeout=25, context=_SSL_CTX) as r:
            out[q] = json.load(r)
        print(f"{q!r}: 200, {len(out[q].get('results', []))} resultados")
    except Exception as e:  # noqa: BLE001
        out[q] = {"_erro": f"{type(e).__name__}: {e}"}
        print(f"{q!r}: {out[q]['_erro']}")

with open("opensea_search_raw.json", "w", encoding="utf-8") as f:
    json.dump(out, f, ensure_ascii=False, indent=1)
print("resposta crua → opensea_search_raw.json (eu leio-a da pasta)")
