#!/usr/bin/env python3
"""Amostra da Foundation (ETH) com as DUAS medições no MESMO conjunto —
condição 1 do Opus (04/09): em Base o problema era único-mas-inescrevível;
em ETH o risco plausível é escrevível-mas-não-único (títulos de arte
colidem: Untitled, Genesis, Nocturne). Mede os dois eixos juntos.

O que faz: amostra N tokens da Foundation, resolve nome+imagem pela chain,
testa a UNICIDADE do nome-base na pesquisa da OPENSEA (forma medida a 05/09
com verificar_opensea_search.py: /api/v2/search?query=…&asset_types=nft,
X-API-KEY + User-Agent, resposta em `results` com contract/identifier/name;
a pesquisa é GLOBAL — todas as cadeias — que é exactamente como um caçador
pesquisa), e escreve candidatos_foundation.json para o
testar_escrevibilidade.py. (A Rarible ficou sem quota; a regra de unicidade
é a mesma do medidor v5: homónimos por nome-base normalizado, único =
exactamente um e é o nosso token.)

Corre do raiz do repo (OPENSEA_API_KEY no .env; usa RPC público de ETH):

    .venv/bin/python scripts/amostrar_foundation.py
    .venv/bin/python scripts/testar_escrevibilidade.py candidatos_foundation.json

Fasquia do Opus: unicidade < 50% na amostra → os 198k de ETH são ilusão.
"""

from __future__ import annotations

import argparse
import json
import random
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

from medir_universo_alvos import (  # mesmo directório
    _SSL_CTX,
    SEL_TOKENURI,
    Rpc,
    decode_string_result,
    name_ok,
    normalize_name,
    read_env,
    resolve_token_uri,
)

OPENSEA = "https://api.opensea.io"
OS_UA = "fml-refresh-probe/1.0"       # medido: passa o Cloudflare

FOUNDATION = "0x3b3ee1931dc30c1957379fac9aba94d1c48a5405"
SEL_TOTAL = "0x18160ddd"
SEL_TOKENBYINDEX = "0x4f6ccce7"
ETH_RPCS = ["https://ethereum-rpc.publicnode.com", "https://eth.drpc.org",
            "https://cloudflare-eth.com"]


def pick_eth_rpc() -> Rpc:
    for url in ETH_RPCS:
        rpc = Rpc(url)
        try:
            rpc("eth_blockNumber", [], tries=1)
            print(f"RPC ETH: {url}")
            return rpc
        except Exception as e:  # noqa: BLE001
            print(f"  ({url}: {str(e)[:60]})")
    raise SystemExit("sem RPC ETH")


def opensea_search(key: str, text: str) -> list[tuple[str, str]] | None:
    """[('contract:identifier', nome)] da pesquisa global da OpenSea;
    None = inverificável (fail-closed do lado de quem chama)."""
    q = urllib.parse.quote(text)
    # limit=10 é a forma MEDIDA (prober 05/09); limit=25 devolve 200 com
    # lista vazia — a API não recusa, esvazia em silêncio
    url = f"{OPENSEA}/api/v2/search?query={q}&asset_types=nft&limit=10"
    req = urllib.request.Request(url, headers={
        "X-API-KEY": key, "Accept": "application/json", "User-Agent": OS_UA})
    for attempt in (1, 2, 3):
        try:
            with urllib.request.urlopen(req, timeout=25, context=_SSL_CTX) as r:
                out = json.load(r)
            items = []
            for it in out.get("results", []) or []:
                nft = _extract_nft(it)
                if nft:
                    items.append(nft)
            return items
        except urllib.error.HTTPError as e:
            if e.code == 429 and attempt < 3:
                time.sleep(5.0 * attempt)
                continue
            return None
        except Exception:  # noqa: BLE001
            return None
    return None


def _extract_nft(node, depth: int = 0) -> tuple[str, str] | None:
    """Primeiro dict aninhado com contract+identifier (o prober de 05/09
    encontrou os campos por descida recursiva — eles NÃO estão no topo de
    cada resultado). Devolve ('contract:identifier', nome) ou None."""
    if depth > 6:
        return None
    if isinstance(node, dict):
        c = str(node.get("contract") or "").lower()
        i = str(node.get("identifier") or "")
        if c and i:
            # SEM exigir 0x: um homónimo em Solana é um homónimo que o
            # caçador vê — excluí-lo enviesava a unicidade para cima
            # (foi o defeito do diagnóstico de 05/09: 'art' devolveu 10
            # NFTs de Solana e o filtro 0x rejeitou todos)
            return f"{c}:{i}", str(node.get("name") or "")
        for v in node.values():
            got = _extract_nft(v, depth + 1)
            if got:
                return got
    elif isinstance(node, list):
        for v in node[:8]:
            got = _extract_nft(v, depth + 1)
            if got:
                return got
    return None


def diagnose(key: str) -> bool:
    """Exige RESULTADOS, não só 200 ('art' tem de devolver itens), e mostra
    os headers de quota — a pesquisa da OpenSea esgotada devolve 200 com
    lista vazia em vez de 429 (medido 05/09), portanto o header é a única
    testemunha."""
    import datetime

    # 'Calm Night': resposta capturada 05/09 (opensea_search_raw.json) com
    # itens EVM garantidos — o teste de parser é contra forma conhecida
    q = urllib.parse.quote("Calm Night")
    url = f"{OPENSEA}/api/v2/search?query={q}&asset_types=nft&limit=10"
    req = urllib.request.Request(url, headers={
        "X-API-KEY": key, "Accept": "application/json", "User-Agent": OS_UA})
    try:
        with urllib.request.urlopen(req, timeout=25, context=_SSL_CTX) as r:
            payload = json.load(r)
            rl = {k.lower(): v for k, v in r.headers.items()
                  if k.lower().startswith("x-ratelimit")}
    except Exception as e:  # noqa: BLE001
        print(f"OpenSea search: {type(e).__name__}: {str(e)[:120]}")
        return False
    raw = payload.get("results", []) or []
    n = sum(1 for it in raw if _extract_nft(it))   # testa o PARSER, não só o 200
    if rl:
        reset = rl.get("x-ratelimit-reset", "")
        try:
            reset += ("  (" + datetime.datetime.fromtimestamp(int(reset))
                      .strftime("%H:%M:%S") + " local)")
        except (ValueError, OSError):
            pass
        print(f"quota: limite {rl.get('x-ratelimit-limit', '?')} | "
              f"restam {rl.get('x-ratelimit-remaining', '?')} | "
              f"repõe {reset}")
    if n:
        print(f"OpenSea search: {len(raw)} resultados, {n} com NFT "
              "extraído — instrumento E parser ok.")
        return True
    print(f"OpenSea search: {len(raw)} resultados em bruto mas 0 extraídos "
          "— se raw>0 o PARSER está errado para a forma actual; se raw=0, "
          "janela esgotada ou endpoint em baixo. Não gasto as 100 chamadas.")
    return False


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--sample", type=int, default=100)
    ap.add_argument("--out", default="candidatos_foundation.json")
    ap.add_argument("--seed", type=int, default=None)
    args = ap.parse_args()
    rng = random.Random(args.seed)

    key = read_env("OPENSEA_API_KEY") or ""
    if not key:
        print("OPENSEA_API_KEY não encontrada no .env.")
        return 2
    if not diagnose(key):
        return 1

    rpc = pick_eth_rpc()
    supply = int(rpc("eth_call", [{"to": FOUNDATION, "data": SEL_TOTAL},
                                  "latest"]), 16)
    print(f"Foundation totalSupply = {supply:,}\n")

    cands, uniq_yes, uniq_no, uniq_unk, name_bad, meta_bad = [], 0, 0, 0, 0, 0
    tried = 0
    while len(cands) + uniq_no + uniq_unk < args.sample and tried < args.sample * 4:
        tried += 1
        idx = rng.randrange(supply)
        try:
            tid = int(rpc("eth_call", [{
                "to": FOUNDATION,
                "data": SEL_TOKENBYINDEX + idx.to_bytes(32, "big").hex(),
            }, "latest"], tries=1), 16)
        except Exception:  # noqa: BLE001
            tid = idx + 1
        try:
            uri = decode_string_result(rpc("eth_call", [{
                "to": FOUNDATION,
                "data": SEL_TOKENURI + tid.to_bytes(32, "big").hex(),
            }, "latest"], tries=1))
            meta = resolve_token_uri(uri) if uri else None
        except Exception:  # noqa: BLE001
            meta = None
        if not (isinstance(meta, dict) and meta.get("name") and meta.get("image")):
            meta_bad += 1
            continue
        name = str(meta["name"]).strip()
        base = normalize_name(name)
        if not name_ok(base):
            name_bad += 1
            continue

        results = opensea_search(key, base)
        time.sleep(0.8)               # janela de 120 medida nos headers
        n_done = len(cands) + uniq_no + uniq_unk + 1
        if results is None or results == []:
            # vazio total = pesquisa avariada (nem o próprio token, que a
            # OpenSea indexa, apareceu) — inverificável, nunca "não-único"
            uniq_unk += 1
            print(f"[{n_done:3}/{args.sample}] [?] {base[:48]!r}"
                  + (" (0 resultados — instrumento?)" if results == [] else ""))
            continue
        # mesma regra do medidor v5: entre os resultados, os que têm o MESMO
        # nome-base; único = há exactamente um e é o nosso token
        want = f"{FOUNDATION}:{tid}".lower()
        same = [i.lower() for i, nm in results
                if normalize_name(nm).casefold() == base.casefold()]
        unique = len(same) == 1 and same[0] == want
        if unique:
            uniq_yes += 1
            mark = "U"
            cands.append({
                "chain": "ethereum", "contract": FOUNDATION, "tokenId": tid,
                "name": base, "name_onchain": name,
                "description": str(meta.get("description") or "")[:600],
                "image": str(meta.get("image") or ""),
                "platform": "foundation",
            })
        else:
            uniq_no += 1
            mark = "x" if same else "x·nem-indexado"
        print(f"[{n_done:3}/{args.sample}] [{mark}] {base[:48]!r} "
              f"({len(same)} homónimos em {len(results)} resultados)")

    n = uniq_yes + uniq_no + uniq_unk
    print("\n================= RESULTADO =================")
    print(f"amostra com nome válido: {n} | únicos {uniq_yes} "
          f"({(uniq_yes / n) if n else 0:.0%}) | não-únicos {uniq_no} | "
          f"inverificáveis {uniq_unk}")
    print(f"(descartados antes: metadata {meta_bad}, nome-base {name_bad})")
    print("Fasquia Opus: unicidade < 50% → os 198k são ilusão.")
    with open(args.out, "w", encoding="utf-8") as f:
        json.dump(cands, f, ensure_ascii=False, indent=1)
    print(f"{len(cands)} candidatos únicos → {args.out} "
          "(corre agora o testar_escrevibilidade.py sobre este ficheiro)")
    print(f"chamadas RPC: {rpc.calls}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
