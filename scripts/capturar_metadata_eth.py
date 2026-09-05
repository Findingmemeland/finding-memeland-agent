#!/usr/bin/env python3
"""Captura CRUA do que falhou no censo da condição 2 — para corrigir os
instrumentos contra a realidade (regra de 05/09), sem gastar quota nenhuma
de marketplace: só RPC e fetches de metadata.

Para cada plataforma problemática: 3 tokens → tokenURI cru (string exacta) →
tentativa de resolução com o resultado/erro registado. Para MakersPlace e
Rarible721 (totalSupply reverte): testa também tokenURI/ownerOf em ids
baixos para confirmar que o contrato responde, e regista o erro exacto do
totalSupply. Tudo → metadata_eth_raw.json (eu leio da pasta).

    .venv/bin/python scripts/capturar_metadata_eth.py
"""

from __future__ import annotations

import json
import sys
import urllib.request

from amostrar_foundation import SEL_TOKENBYINDEX, SEL_TOTAL, pick_eth_rpc
from medir_universo_alvos import (
    _SSL_CTX,
    SEL_TOKENURI,
    decode_string_result,
)

SEL_OWNEROF = "0x6352211e"

PLATFORMS = [
    ("superrare2", "0xb932a70a57673d89f4acffbe830e8ed7f75fb9e0"),
    ("superrare1", "0x41a322b28d0ff354040e2cbc676f0320d8c8850d"),
    ("asyncart2",  "0xb6dae651468e9593e4581705a09c10a76ac1e0c8"),
    ("asyncart1",  "0x6c424c25e9f1fff9642cb5b7750b0db7312c29ad"),
    ("makersplace", "0x2963ba471e265e5f51cafafca78310fe87f8e6d1"),
    ("rarible721", "0xd07dc4262bcdbf85190c01c996b4c06a461d2430"),
]


def eth_call(rpc, to, data):
    return rpc("eth_call", [{"to": to, "data": data}, "latest"], tries=1)


def try_fetch(url: str) -> dict:
    req = urllib.request.Request(url, headers={
        "User-Agent": "fml-probe/1.0", "Accept": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=20, context=_SSL_CTX) as r:
            body = r.read(4000).decode("utf-8", "replace")
        return {"status": 200, "body_inicio": body[:1500]}
    except Exception as e:  # noqa: BLE001
        return {"erro": f"{type(e).__name__}: {str(e)[:200]}"}


def main() -> int:
    rpc = pick_eth_rpc()
    out: dict = {}
    for slug, contract in PLATFORMS:
        rec: dict = {"contract": contract}
        # totalSupply: valor ou erro exacto
        try:
            rec["totalSupply"] = int(eth_call(rpc, contract, SEL_TOTAL), 16)
        except Exception as e:  # noqa: BLE001
            rec["totalSupply_erro"] = str(e)[:200]
        # ids de teste: por índice se der, senão directos baixos e altos
        ids = []
        supply = rec.get("totalSupply")
        if supply:
            for idx in (0, supply // 2, supply - 1):
                try:
                    ids.append(int(eth_call(
                        rpc, contract,
                        SEL_TOKENBYINDEX + idx.to_bytes(32, "big").hex()), 16))
                except Exception:  # noqa: BLE001
                    ids.append(idx + 1)
        else:
            ids = [1, 100, 10_000]
        rec["tokens"] = []
        for tid in ids[:3]:
            t: dict = {"tokenId": tid}
            try:
                raw = eth_call(rpc, contract,
                               SEL_TOKENURI + tid.to_bytes(32, "big").hex())
                uri = decode_string_result(raw)
                t["tokenURI"] = uri[:400] if uri else f"(indecifrável: {raw[:80]})"
                if uri and uri.startswith("ipfs://"):
                    path = uri[7:]
                    gw = "https://ipfs.io/ipfs/" + (
                        path[5:] if path.startswith("ipfs/") else path)
                    t["fetch_ipfs_io"] = try_fetch(gw)
                elif uri and uri.startswith("http"):
                    t["fetch"] = try_fetch(uri)
            except Exception as e:  # noqa: BLE001
                t["tokenURI_erro"] = str(e)[:200]
            try:
                data = eth_call(rpc, contract,
                                SEL_OWNEROF + tid.to_bytes(32, "big").hex())
                t["ownerOf"] = "0x" + data[-40:]
            except Exception as e:  # noqa: BLE001
                t["ownerOf_erro"] = str(e)[:120]
            rec["tokens"].append(t)
        out[slug] = rec
        print(f"{slug}: supply={rec.get('totalSupply', 'ERRO')} | "
              f"{len(rec['tokens'])} tokens sondados")

    with open("metadata_eth_raw.json", "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=1)
    print("\ncaptura → metadata_eth_raw.json (eu leio da pasta)")
    print(f"chamadas RPC: {rpc.calls}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
