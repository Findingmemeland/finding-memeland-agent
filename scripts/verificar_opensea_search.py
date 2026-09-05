#!/usr/bin/env python3
"""Mede a forma do endpoint de PESQUISA da OpenSea v2 — para decidir se a
unicidade pode medir-se por lá (a Rarible está sem quota). Nunca usámos
este endpoint além de um contains-check; antes de assentar unicidade nele,
vemos a estrutura real da resposta.

Corre do raiz do repo (OPENSEA_API_KEY no .env):

    .venv/bin/python scripts/verificar_opensea_search.py
"""

from __future__ import annotations

import json
import sys
import urllib.error
import urllib.parse
import urllib.request

from medir_universo_alvos import _SSL_CTX, read_env  # mesmo directório

BASE = "https://api.opensea.io"
UA = "fml-refresh-probe/1.0"          # medido 04/09: passa o Cloudflare

# Três queries com resposta conhecida à partida:
#  - título Foundation provavelmente único (do censo de ontem)
#  - título que colide de certeza (Untitled)
#  - nome de colecção seriada (deve devolver muitos)
QUERIES = ["Burning in the Undertow of God", "Untitled", "Tiny Punk"]


def probe(key: str, query: str) -> None:
    q = urllib.parse.quote(query)
    for variant in (f"/api/v2/search?query={q}&asset_types=nft&limit=10",
                    f"/api/v2/search?query={q}&limit=10"):
        url = BASE + variant
        req = urllib.request.Request(url, headers={
            "X-API-KEY": key, "Accept": "application/json", "User-Agent": UA})
        try:
            with urllib.request.urlopen(req, timeout=25, context=_SSL_CTX) as r:
                payload = json.load(r)
        except urllib.error.HTTPError as e:
            body = e.read(200).decode("utf-8", "replace") if e.fp else ""
            print(f"  {variant.split('?')[0]}?…: HTTP {e.code} — {body[:120]}")
            continue
        except Exception as e:  # noqa: BLE001
            print(f"  excepção {type(e).__name__}: {e}")
            continue
        print(f"  variante OK: {variant[:60]}…")
        print(f"  chaves de topo: {sorted(payload.keys())}")
        _walk_first_items(payload)
        return
    print("  nenhuma variante respondeu 200")


def _walk_first_items(node, depth=0, shown=[0]):  # noqa: B006 — contador
    """Mostra os primeiros objectos-folha com campos que pareçam identificar
    um NFT (contract/address/identifier/token/chain/name)."""
    if shown[0] >= 4 or depth > 6:
        return
    if isinstance(node, dict):
        keys = {k.lower() for k in node}
        if keys & {"contract", "address", "identifier", "token_id", "chain"}:
            shown[0] += 1
            compact = {k: (str(v)[:48]) for k, v in node.items()
                       if not isinstance(v, (dict, list))}
            print(f"   · item: {json.dumps(compact, ensure_ascii=False)[:220]}")
            return
        for v in node.values():
            _walk_first_items(v, depth + 1, shown)
    elif isinstance(node, list):
        for v in node[:6]:
            _walk_first_items(v, depth + 1, shown)


def main() -> int:
    key = read_env("OPENSEA_API_KEY") or ""
    if not key:
        print("OPENSEA_API_KEY não encontrada no .env.")
        return 2
    for q in QUERIES:
        print(f"\npesquisa: {q!r}")
        probe(key, q)
    print("\nSe os itens trouxerem contract+identifier+chain, a unicidade "
          "mede-se pela OpenSea e a Rarible deixa de ser bloqueante.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
