#!/usr/bin/env python3
"""probe_sources.py — que contratos podem ser FONTES da despensa?

O PORQUÊ (22/09). Os hunts #12 e #13 saíram do mesmo contrato partilhado do
SuperRare, na mesma cadeia, e o contador novo do /status mostrou a razão:

    despensa: 25 alvo(s) · composição: 2 contrato(s) · maior 13 (52%)
              · ethereum 25

Não era amostragem enviesada. Era o universo inteiro:

    SOURCES = (foundation/ethereum, superrare2/ethereum)

Duas fontes, uma cadeia. Nenhum filtro no /prepare consegue produzir
variedade que a despensa não pode conter — e a frase que o site e o
litepaper passaram a dizer hoje, "the treasure can be anywhere onchain",
não é entregue por um pipeline que só sabe ler Ethereum.

O REQUISITO DURO. Uma fonte tem de ENUMERAR: responder a `totalSupply()`
e a `tokenByIndex(i)`. É por isso que a lista é curta — a maioria dos
contratos NFT não implementa ERC721Enumerable, e sem isso não há forma de
sortear uma peça sem indexar a colecção inteira primeiro.

O que esta sonda faz, e só isto: mede. Para cada candidato pergunta à
cadeia se enumera, qual é o tamanho, e se um token à sorte devolve um
tokenURI legível. Não escreve nada, não altera SOURCES, não decide nada.
A decisão de quais entram é do Pedro, com a tabela à frente.

Não inventa endereços: parte dos que o projecto já ratificou
(EPOCH1_CLASSIC) e aceita mais por argumento.

    python scripts/probe_sources.py
    python scripts/probe_sources.py --add base:0x… --add zora:0x…
    python scripts/probe_sources.py --samples 5
"""
from __future__ import annotations

import argparse
import random
import sys

sys.path.insert(0, "src")

from finding_memeland.config import get_settings                    # noqa: E402
from finding_memeland.target.adapters import (                      # noqa: E402
    abi_uint,
    chain_rpcs,
    decode_abi_string,
)
from finding_memeland.target.sources import (                       # noqa: E402
    EPOCH1_CLASSIC,
    SEL_TOKENBYINDEX,
    SEL_TOKENURI,
    SEL_TOTAL,
    ChainUnavailable,
)

OK, NO, MEH = "✓", "✗", "·"


def _http_post(url: str, body: bytes, headers: dict) -> str:
    import urllib.request
    req = urllib.request.Request(url, data=body, headers=headers, method="POST")
    with urllib.request.urlopen(req, timeout=20) as r:
        return r.read().decode("utf-8", "replace")


def probe(rpc, contract: str, *, samples: int, rng: random.Random) -> dict:
    """Uma fonte é utilizável se enumera E se os tokens se lêem.

    Três medições, cada uma com a sua causa própria — nunca um 'falhou'
    genérico, que é o que manda alguém procurar no sítio errado."""
    out: dict = {"total": None, "enumerates": False, "read": 0,
                 "tried": 0, "why": "", "ours": False, "by_id": 0}
    try:
        out["total"] = int(rpc.eth_call(contract, SEL_TOTAL), 16)
    except ChainUnavailable as e:
        # R8, aplicada à própria sonda. A primeira versão disto (22/09)
        # devolveu "nenhum candidato utilizável" quando o que falhou foi o
        # certificado SSL deste portátil. Um veredicto sobre o contrato a
        # partir de uma avaria nossa é o erro que o agente tem proibido —
        # não é aceitável numa ferramenta que existe para decidir.
        out["ours"] = True
        out["why"] = f"NÃO MEDIDO — transporte nosso ({type(e).__name__}: {e})"
        return out
    except Exception as e:  # noqa: BLE001 — o contrato recusou; isso é dele
        out["why"] = f"totalSupply reverteu ({type(e).__name__})"
        return out
    if not out["total"]:
        out["why"] = "totalSupply devolveu 0"
        return out

    try:
        rpc.eth_call(contract, SEL_TOKENBYINDEX + abi_uint(0))
        out["enumerates"] = True
    except Exception:  # noqa: BLE001
        # Não enumera — mas isso pode não ser o fim. Muitas plataformas 1/1
        # emitem ids SEQUENCIAIS e implementam tokenURI sem pagar o custo do
        # ERC721Enumerable. Se um id à sorte dentro do intervalo responder,
        # dá para sortear sem indexar nada. Medido aqui, nunca assumido:
        # um contrato com ids esparsos falha isto e fica de fora.
        out["why"] = "não enumera (tokenByIndex reverte)"
        hits = 0
        for _ in range(samples):
            tid = rng.randrange(1, max(2, out["total"] + 1))
            out["tried"] += 1
            try:
                if decode_abi_string(
                        rpc.eth_call(contract, SEL_TOKENURI + abi_uint(tid))):
                    hits += 1
            except Exception:  # noqa: BLE001
                pass
        out["by_id"] = hits
        if hits:
            out["why"] += (f" — MAS tokenURI respondeu a {hits}/{out['tried']}"
                           " ids ao calhas: utilizável por sorteio de id")
        return out

    # Enumera — mas o pipeline também tem de conseguir LER as peças.
    for _ in range(samples):
        idx = rng.randrange(out["total"])
        out["tried"] += 1
        try:
            tid = int(rpc.eth_call(contract, SEL_TOKENBYINDEX + abi_uint(idx)), 16)
            uri = decode_abi_string(rpc.eth_call(contract, SEL_TOKENURI + abi_uint(tid)))
            if uri:
                out["read"] += 1
        except Exception:  # noqa: BLE001 — conta como ilegível, sem detalhe
            pass
    if not out["read"]:
        out["why"] = "enumera mas nenhum tokenURI se leu"
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--add", action="append", default=[],
                    metavar="CHAIN:0xADDR",
                    help="candidato extra; repetível")
    ap.add_argument("--samples", type=int, default=3,
                    help="tokens a ler por candidato (default 3)")
    ap.add_argument("--seed", type=int, default=0)
    args = ap.parse_args()

    s = get_settings()
    urls = {"ethereum": getattr(s, "eth_rpc_url", "") or "",
            "base": getattr(s, "base_rpc_url", "") or ""}
    rpcs = chain_rpcs({k: v for k, v in urls.items() if v}, http_post=_http_post)

    cands: list[tuple[str, str, str]] = [
        (slug, chain, addr) for slug, chain, addr in EPOCH1_CLASSIC
    ]
    for raw in args.add:
        chain, _, addr = raw.partition(":")
        chain, addr = chain.strip().lower(), addr.strip().lower()
        if not (chain and addr.startswith("0x") and len(addr) == 42):
            print(f"{NO} ignorado (formato CHAIN:0x… com 40 hex): {raw!r}")
            continue
        cands.append((f"add:{addr[:10]}", chain, addr))

    print("=" * 74)
    print("FONTES CANDIDATAS — enumera? lê-se?")
    print("=" * 74)
    print(f"  RPCs disponíveis: {', '.join(sorted(rpcs)) or '(nenhum)'}")
    print(f"  candidatos: {len(cands)}  ·  amostras por candidato: {args.samples}")

    rng = random.Random(args.seed)
    usable: list[tuple[str, str, str, int, str]] = []
    blind: list[str] = []
    for slug, chain, addr in cands:
        if chain not in rpcs:
            print(f"\n  {MEH} {slug:14} {chain:9} — SEM RPC configurado para "
                  f"esta cadeia; a sonda não pode medir")
            continue
        r = probe(rpcs[chain], addr, samples=args.samples, rng=rng)
        good = (r["enumerates"] and r["read"]) or r["by_id"]
        mark = MEH if r["ours"] else (OK if good else NO)
        print(f"\n  {mark} {slug:14} {chain:9} {addr}")
        if r["total"] is not None:
            print(f"      totalSupply: {r['total']:,}")
        if r["enumerates"]:
            print(f"      tokenURI legível em {r['read']}/{r['tried']} amostras")
        if r["why"]:
            print(f"      {r['why']}")
        if r["ours"]:
            blind.append(slug)
        elif mark == OK:
            how = "índice" if r["enumerates"] else "id ao calhas"
            usable.append((slug, chain, addr, r["total"], how))

    print("\n" + "=" * 74)
    print("VEREDICTO")
    print("=" * 74)
    if blind:
        print(f"{len(blind)} candidato(s) NÃO FORAM MEDIDOS — o transporte")
        print("falhou do nosso lado. Isto não diz nada sobre os contratos.")
        if len(blind) == len(cands):
            print("\nNENHUM foi medido. Quase de certeza o certificado deste")
            print("Mac, o mesmo do check_search_guard:")
            print('  export SSL_CERT_FILE=$(python -c "import certifi; '
                  'print(certifi.where())")')
            print("\nCorre outra vez antes de concluir seja o que for.")
            return 2
        print()
    if not usable:
        print("Dos que foram medidos, nenhum é utilizável. Não mexer no SOURCES.")
        return 1

    chains = sorted({c for _s, c, _a, _t, _h in usable})
    print(f"{len(usable)} utilizável(eis), em {len(chains)} cadeia(s): "
          f"{', '.join(chains)}\n")
    for slug, chain, addr, total, how in usable:
        print(f'    Source("{slug}", "{chain}", "{addr}"),'
              f'   # {total:,} tokens · por {how}')
    if any(h != "índice" for *_x, h in usable):
        print("\n  ⚠️ os marcados 'por id ao calhas' NÃO enumeram: entram só")
        print("     se o TargetFinder passar a saber sortear assim. É código")
        print("     novo, não é acrescentar uma linha ao SOURCES.")
    print("\nO que ISTO resolve e o que NÃO resolve:")
    print("  · mais contratos = a despensa deixa de poder ser um só contrato")
    print("  · mais CADEIAS = a promessa \"anywhere onchain\" passa a ser real")
    if len(chains) == 1:
        print("  ⚠️ tudo numa cadeia só: a variedade de contratos melhora, a")
        print("     de cadeias não. Para rodar entre redes falta uma fonte")
        print("     fora desta — corre outra vez com --add <cadeia>:<contrato>")
    print("\nAcrescentar ao SOURCES é decisão do Pedro. A sonda não escreve nada.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
