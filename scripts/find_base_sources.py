#!/usr/bin/env python3
"""find_base_sources.py — que contratos de Base servem de fonte?

O PORQUÊ (22/09). A despensa é toda Ethereum: três contratos, uma cadeia.
A frase que o site publica — "the treasure can be anywhere onchain" — não é
entregue por um pipeline que só sabe ler uma rede.

Procurar isto à mão não dá. O BaseScan não tem lista de colecções (o menu
NFTs só tem Latest Transfers e Latest Mints), e o que aparece ordenado por
volume é PFP: dez mil peças com o mesmo nome e um número atrás. Essas são
inúteis aqui, e a razão não é gosto — AS PISTAS CIFRAM O NOME. Se dez mil
tokens se chamam "Based Punk", a pista aponta para dez mil peças ao mesmo
tempo e não há resposta certa.

Então isto mede o que decide: de cada colecção tira uma amostra de nomes e
conta QUANTOS SÃO DISTINTOS depois de lhes arrancar o serial. Uma colecção
1/1 dá perto de 100%. Uma PFP dá perto de 0%.

TRÊS CORRECÇÕES DEPOIS DA PRIMEIRA CORRIDA (22/09, 200 colecções):

1. AMOSTRA MÍNIMA. A v1 dava "100% distintos" a colecções com UM token —
   com n=1 isso é aritmética, não é medição. Calculava o n e não o imprimia,
   por isso nem dava para ver. Agora há um piso e o n vai em todas as linhas.

2. AS FALHAS TÊM DE DIZER PORQUÊ. 110 das 200 não foram medidas e a v1
   escreveu só o número. É a R8 outra vez, na minha própria ferramenta: a
   causa engolida manda quem lê procurar no sítio errado. Agora as causas
   são contadas por tipo e impressas.

3. CONTRATOS HOSTIS EXISTEM NA LISTAGEM. A v1 aprovou 20 colecções
   "internalprobe" cujos tokens se chamam text-exfil-ext, svg-beacon,
   nip-gcpmd, rebind-ssl, metadata-goog, lo-port. Não é arte: é um kit de
   testes de SSRF, DNS rebinding e exfiltração, mintado na cadeia, e o alvo
   desses payloads é qualquer serviço que vá buscar metadata e imagens a
   partir de um tokenURI — ou seja, nós. Um beacon que dispare do nosso
   servidor entrega a infraestrutura de um fundador anónimo.

   As defesas do pipeline aguentariam quase de certeza (só resolvemos URIs
   endereçados por conteúdo, o fetch da imagem recusa redirects e tem tecto
   de bytes). "Quase de certeza" não é como se trata um contrato chamado
   text-exfil-ext, e não há upside nenhum: nenhum deles tem arte.

   Por isso estes são SINALIZADOS, não silenciados. Esconder um contrato
   hostil da tabela seria o mesmo erro da causa engolida, ao contrário.

E continua a não escrever nada. Não toca no SOURCES. A decisão é do Pedro,
com a tabela à frente.

    python scripts/find_base_sources.py
    python scripts/find_base_sources.py --pages 4 --min-sample 8
    python scripts/find_base_sources.py --show-all
"""
from __future__ import annotations

import argparse
import json
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from collections import Counter

sys.path.insert(0, "src")

from finding_memeland.config import get_settings                    # noqa: E402
from finding_memeland.target.selector import (                      # noqa: E402
    name_qualifies,
    normalize_name,
)

OK, NO, MEH, WARN = "✓", "✗", "·", "⚠️"
UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/125.0 Safari/537.36")
BASE_URL = "https://api.opensea.io/api/v2"
# Medido pelo OpenSeaSearch (10/09): 120 pedidos por janela de 60 s. 0,6 s
# fica em 100/min — perto de mais do tecto quando uma corrida faz 200
# pedidos seguidos, e a primeira corrida perdeu 110. 1,1 s dá folga.
PAUSE_S = 1.1

# Vocabulário de ataque a infraestrutura que lê NFTs. Não é uma lista de
# "colecções más" — é o conjunto de palavras que só aparece num nome de
# token quando alguém está a testar SSRF, DNS rebinding ou exfiltração.
# Marcar, nunca esconder: a tabela tem de mostrar o que encontrou.
HOSTILE_WORDS = (
    "exfil", "beacon", "rebind", "gcpmd", "ecsmd", "metadata-goog",
    "nip-", "httpbin", "ssrf", "redir-", "iframe-", "lo-port", "localhost",
    "127.0.0.1", "169.254", "data-url", "script-", "svg-internal",
)
# Nomes de colecção que se declaram descartáveis. As fronteiras à esquerda
# não são opcionais: sem elas, "test" dentro de "contest" e "protest" mata
# arte a sério. A excepção é "probe", que aparece colado — as vinte colecções
# hostis da primeira corrida chamavam-se "internalprobe-…", e nenhuma palavra
# inglesa corrente acaba em -probe.
JUNK_SLUG = re.compile(
    r"(^|[-_])(test|demo|sample|staging|dummy)([-_]|\d|$)|probe", re.I)


class ApiTrouble(RuntimeError):
    """O PEDIDO falhou — do nosso lado ou do deles. R8: isto não diz nada
    sobre a colecção, e não pode virar um veredicto sobre ela."""


def _get(path: str, key: str, **params) -> dict:
    url = f"{BASE_URL}/{path.lstrip('/')}"
    if params:
        url += "?" + urllib.parse.urlencode(
            {k: v for k, v in params.items() if v not in (None, "")})
    req = urllib.request.Request(
        url, headers={"User-Agent": UA, "accept": "application/json",
                      "x-api-key": key})
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            return json.loads(r.read().decode("utf-8", "replace"))
    except urllib.error.HTTPError as e:
        raise ApiTrouble(f"HTTP {e.code}") from e
    except Exception as e:  # noqa: BLE001
        raise ApiTrouble(type(e).__name__) from e


def hostile_hits(names: list[str]) -> list[str]:
    """Que palavras de ataque aparecem nestes nomes. Lista, não booleano:
    quem lê a tabela tem de ver a prova, não um carimbo."""
    low = " ".join(names).lower()
    return [w for w in HOSTILE_WORDS if w in low]


def name_health(slug: str, key: str, *, sample: int) -> dict:
    """A medição que decide: dos nomes desta colecção, quantos são próprios?

    `n` é o tamanho da amostra e vai SEMPRE para a tabela — sem ele, "100%
    distintos" pode ser uma colecção de um token, que foi como vinte kits de
    intrusão passaram o filtro da v1."""
    out = {"n": 0, "distinct": 0.0, "qualifying": 0.0, "examples": [],
           "hostile": [], "why": ""}
    try:
        data = _get(f"collection/{slug}/nfts", key, limit=min(sample, 50))
    except ApiTrouble as e:
        out["why"] = str(e)
        return out
    nfts = data.get("nfts") or []
    names = [str(n.get("name") or "").strip() for n in nfts if n.get("name")]
    if not nfts:
        # DUAS CAUSAS DIFERENTES, e a v2 ainda as juntava num "sem nomes"
        # que apanhou 256 de 400 (23/09). Uma colecção que não devolve
        # tokens nenhuns não é o mesmo que uma cujos tokens não têm título,
        # e mandar quem lê tratar as duas como a mesma coisa é a R8 outra
        # vez, um nível mais fundo.
        out["why"] = "a colecção não devolveu tokens"
        return out
    if not names:
        out["why"] = f"{len(nfts)} token(s), nenhum com título"
        return out
    bases = [normalize_name(n) for n in names]
    out["n"] = len(bases)
    out["distinct"] = len(set(bases)) / len(bases)
    out["qualifying"] = sum(name_qualifies(b) for b in bases) / len(bases)
    out["hostile"] = hostile_hits(names)
    seen: list[str] = []
    for b in bases:
        if b not in seen:
            seen.append(b)
    out["examples"] = seen[:3]
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--chain", default="base")
    ap.add_argument("--pages", type=int, default=2,
                    help="páginas de 100 colecções (default 2)")
    ap.add_argument("--order-by", default="market_cap",
                    help="ordem da listagem do OpenSea (default market_cap; "
                         "'created_date' é o default DELES e traz lixo fresco)")
    ap.add_argument("--sample", type=int, default=30,
                    help="tokens a ler por colecção, máx 50 (default 30)")
    ap.add_argument("--min-sample", type=int, default=8,
                    help="amostra mínima para haver veredicto (default 8)")
    ap.add_argument("--min-distinct", type=float, default=0.9)
    ap.add_argument("--min-qualifying", type=float, default=0.6)
    ap.add_argument("--show-all", action="store_true",
                    help="mostrar também os rejeitados, com a razão")
    args = ap.parse_args()

    s = get_settings()
    key = getattr(s, "opensea_api_key", "") or ""
    if not key:
        print(f"{NO} sem OPENSEA_API_KEY no .env")
        return 2

    print("=" * 76)
    print(f"COLECÇÕES DE {args.chain.upper()} — os nomes prestam para pistas?")
    print("=" * 76)

    cols: list[tuple[str, str]] = []
    cursor = ""
    try:
        for _page in range(max(1, args.pages)):
            # A ORDEM DA LISTAGEM ERA O ERRO (23/09). Sem `order_by`, o
            # OpenSea devolve por data de criação — e a corrida de 400
            # colecções veio cheia de contratos mintados nos últimos dias:
            # 27 kits de intrusão, 84 com menos de 8 tokens, 256 sem
            # tokens nenhuns. Uma candidata em 400. Não era Base a não ter
            # arte: era eu a pedir o que tinha acabado de ser criado.
            data = _get("collections", key, chain=args.chain, limit=100,
                        order_by=args.order_by, next=cursor)
            for c in data.get("collections") or []:
                slug = c.get("collection") or ""
                addr = ""
                for con in c.get("contracts") or []:
                    if str(con.get("chain", "")).lower() == args.chain.lower():
                        addr = str(con.get("address") or "").lower()
                        break
                if slug and addr.startswith("0x") and len(addr) == 42:
                    cols.append((slug, addr))
            cursor = data.get("next") or ""
            if not cursor:
                break
            time.sleep(PAUSE_S)
    except ApiTrouble as e:
        print(f"\n{MEH} NÃO MEDIDO — a listagem falhou: {e}")
        print("Isto não diz nada sobre a cadeia.")
        print('  export SSL_CERT_FILE=$(python -c "import certifi; '
              'print(certifi.where())")')
        return 2

    if not cols:
        print(f"\n{MEH} a API respondeu sem colecções para {args.chain}. "
              "Verificar o slug da cadeia antes de concluir.")
        return 1

    print(f"  {len(cols)} colecção(ões)  ·  amostra até {args.sample} tokens "
          f"(mínimo {args.min_sample})")
    print(f"  corte: {args.min_distinct:.0%} distintos, "
          f"{args.min_qualifying:.0%} com 2+ palavras\n")

    good: list[tuple[str, str, dict]] = []
    hostile: list[tuple[str, str, dict]] = []
    failures: Counter = Counter()
    thin = junk = 0

    for slug, addr in cols:
        h = name_health(slug, key, sample=args.sample)
        time.sleep(PAUSE_S)

        if h["why"]:
            failures[h["why"]] += 1
            continue

        line = (f"{slug[:32]:32} n={h['n']:<3} {h['distinct']:>4.0%} dist · "
                f"{h['qualifying']:>4.0%} 2+pal")
        ex = ", ".join(h["examples"])

        # A ordem importa: hostil ANTES de qualquer aprovação.
        if h["hostile"]:
            hostile.append((slug, addr, h))
            print(f"  {WARN} {line}")
            print(f"      CONTRATO HOSTIL — palavras de ataque nos nomes: "
                  f"{', '.join(h['hostile'])}")
            print(f"      {addr}")
            print(f"      ex.: {ex}")
            continue

        if h["n"] < args.min_sample:
            thin += 1
            if args.show_all:
                print(f"  {MEH} {line}  — amostra pequena de mais; sem veredicto")
            continue

        if JUNK_SLUG.search(slug):
            junk += 1
            if args.show_all:
                print(f"  {MEH} {line}  — o nome diz que é teste/demo")
            continue

        if (h["distinct"] >= args.min_distinct
                and h["qualifying"] >= args.min_qualifying):
            good.append((slug, addr, h))
            print(f"  {OK} {line}")
            print(f"      {addr}")
            print(f"      ex.: {ex}")
        elif args.show_all:
            why = ("nomes repetidos" if h["distinct"] < args.min_distinct
                   else "nomes de uma palavra")
            print(f"  {NO} {line}  — {why}: {ex}")

    # ----------------------------------------------------------------- #
    print("\n" + "=" * 76)
    print("VEREDICTO")
    print("=" * 76)

    if failures:
        total = sum(failures.values())
        print(f"{total} colecção(ões) NÃO FORAM MEDIDAS. Não são rejeições — "
              "é o que falhou, por tipo:")
        for why, n in failures.most_common():
            print(f"    {n:>4}  {why}")
        if any(w.startswith("HTTP 429") for w in failures):
            print("    → 429 é o nosso ritmo: sobe o PAUSE_S ou baixa --pages.")
        if any(w.startswith("HTTP 404") for w in failures):
            print("    → 404 é a colecção não servir tokens por este caminho;"
                  " não diz nada sobre os nomes dela.")
        print()
    if thin:
        print(f"{thin} com amostra abaixo de {args.min_sample} — sem veredicto "
              "(--min-sample para baixar, --show-all para ver).")
    if junk:
        print(f"{junk} com nome de teste/demo, postas de lado.")

    if hostile:
        print(f"\n{WARN} {len(hostile)} CONTRATO(S) HOSTIL(EIS) na listagem.")
        print("Os nomes dos tokens são payloads de SSRF / DNS rebinding /")
        print("exfiltração. O alvo é quem for buscar metadata e imagens a")
        print("partir do tokenURI — ou seja, este pipeline. NÃO SONDAR, não")
        print("acrescentar ao SOURCES, e não é uma questão de gosto:")
        for slug, addr, h in hostile:
            print(f"    {slug[:34]:34} {addr}")

    if not good:
        print("\nNenhuma passou o corte. Sobe o --pages: a arte 1/1 não está")
        print("no topo do volume, e é por isso que este script existe.")
        return 1

    print(f"\n{len(good)} candidata(s) com nomes próprios e amostra a sério.\n")
    for slug, addr, h in good:
        print(f'    Source("{slug[:18]}", "{args.chain}", "{addr}"),'
              f'   # n={h["n"]} · {h["distinct"]:.0%} distintos')

    print("\nO PASSO SEGUINTE é a outra sonda — esta não falou com a cadeia,")
    print("por isso ainda não se sabe se estes contratos enumeram nem se os")
    print("tokens se lêem:\n")
    adds = " ".join(f"--add {args.chain}:{a}" for _s, a, _h in good[:12])
    print(f"    python scripts/probe_sources.py {adds} --samples 5\n")
    print("Só o que passar NAS DUAS pode entrar no SOURCES. E uma cadeia nova")
    print("exige o RPC com chave E o provedor público dessa cadeia — a guarda")
    print("do wiring recusa o arranque sem eles, de propósito.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
