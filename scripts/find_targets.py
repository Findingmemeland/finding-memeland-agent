#!/usr/bin/env python3
"""find_targets.py — colhe ALVOS, não fontes.

A MUDANÇA DE UNIDADE (23/09, decisão do Pedro). Até aqui procurávamos
CONTRATOS grandes para sortear lá dentro. Isso afunila o jogo: o nosso
disfarce é o alvo poder ser qualquer NFT alguma vez mintado, e se o universo
são três contratos, o disfarce não existe — foi o que os hunts #12 e #13
mostraram, ambos do mesmo contrato do SuperRare.

Uma colecção com UM único NFT é perfeita para nós. Não precisa de render
alvos indefinidamente: precisa de render um. E um contrato obscuro de uma
peça é melhor esconderijo do que o Foundation.

Por isso a unidade aqui é o TOKEN. A colecção é só o saco onde ele estava.

A REGRA ÚNICA: O NOME TEM DE SER ÚNICO DENTRO DA SUA PRÓPRIA COLECÇÃO.
Isto substitui a percentagem de nomes distintos da versão anterior, que
media a coisa errada. Uma peça cujo nome-base se repete na colecção é uma
edição, um bilhete, um pack ou um PFP — e as pistas cifram o NOME, portanto
uma pista sobre um nome com mil portadores não tem resposta certa. Um nome
que aparece uma vez só é uma obra.

A regra funciona nos dois extremos, que é o que a percentagem não fazia:
numa colecção de 5.000 cartas mata-as todas, e numa colecção de um token
aprova-o sem precisar de amostra nenhuma.

O QUE ESTE SCRIPT NÃO FAZ. Não fala com a cadeia. Portanto não sabe se o
tokenURI é endereçado por conteúdo, se a imagem se lê, se o dono é EOA, nem
se a idade chega — as outras quatro verificações que o /fill corre sobre
qualquer candidato. Aqui respondem-se três perguntas só, as que o Pedro
enumerou: o nome dá para pista, é próprio, e o marketplace consegue vê-lo.
E não escreve nada em lado nenhum.

    python scripts/find_targets.py
    python scripts/find_targets.py --pages 6 --order-by num_owners
    python scripts/find_targets.py --chain base --out alvos_base.txt
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
from finding_memeland.target.selector import name_qualifies         # noqa: E402

OK, NO, MEH, WARN = "✓", "✗", "·", "⚠️"
UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/125.0 Safari/537.36")
BASE_URL = "https://api.opensea.io/api/v2"
PAUSE_S = 1.1        # 120 pedidos/60 s medidos no OpenSeaSearch (10/09)

# --------------------------------------------------------------------------- #
# Nome-base                                                                     #
# --------------------------------------------------------------------------- #
#
# O normalize_name de produção só arranca serial NO FIM e só em DÍGITOS. A
# corrida de 23/09 mostrou o que isso deixa passar, e não é pouco:
#
#     "Nemesis Ticket #MMC"                 numeração romana
#     "#3000 - Candy Stamps"                serial à frente
#     "NFT.NYC … NFT Ticket #100/"          barra atrás do número
#     "Black_Tag_Swiss_Quality (32) Large"  serial entre parênteses
#
# Os quatro contaram como nomes PRÓPRIOS E DISTINTOS. São o mesmo nome
# repetido. Aqui a tira é mais agressiva porque a decisão é só nossa e
# errar para o lado de rejeitar não custa nada.
#
# ISTO NÃO CORRIGE PRODUÇÃO. O selector continua com a versão antiga, e é
# deliberado: essa função decide o nome sobre o qual TODAS as pistas são
# escritas, e mexer-lhe tem de ser à parte, com testes próprios. Fica
# registado como dívida, não silenciado.
_TAIL = re.compile(
    r"[\s\-–—_#/|]*"
    r"(?:#\s*[A-Za-z0-9]+|\(\s*\d+\s*\)|\[\s*\d+\s*\]|\bno\.?\s*\d+\b|\d+)"
    r"\s*[/\\|]?\s*$", re.I)
_HEAD = re.compile(r"^\s*#\s*[A-Za-z0-9]+\s*[-–—:|]\s*")
_WS = re.compile(r"[\s_]+")


def base_name(name: str) -> str:
    """O nome sem serial, de qualquer um dos lados. Se tirar tudo, fica o
    original — um nome que é só um número não é um nome, mas é melhor
    devolvê-lo e deixá-lo cair na regra das duas palavras."""
    s = _WS.sub(" ", name).strip()
    s = _HEAD.sub("", s).strip()
    while True:
        shorter = _TAIL.sub("", s).strip(" -–—_#/|")
        if shorter == s or not shorter:
            break
        s = shorter
    return s or _WS.sub(" ", name).strip()


# --------------------------------------------------------------------------- #
# Contratos hostis                                                              #
# --------------------------------------------------------------------------- #
#
# 27 colecções "internalprobe" apareceram na listagem de Base (22-23/09) com
# tokens chamados text-exfil-ext, svg-beacon, nip-gcpmd, rebind-ssl,
# metadata-goog, lo-port. Não é arte: é um kit de testes de SSRF, DNS
# rebinding e exfiltração, e o alvo desses payloads é qualquer serviço que
# vá buscar metadata e imagens a partir de um tokenURI — ou seja, este.
#
# A v2 marcava pela palavra solta e deu falso positivo em "parallel-echos",
# uma colecção de cartas onde "beacon" é só uma palavra inglesa. Agora há
# dois níveis:
#   · INEQUÍVOCO — termos que não aparecem em títulos de obra, em forma
#     nenhuma (serviços de metadata da cloud, IPs internos, nip.io).
#   · SÓ EM FORMA DE SLUG — palavras normais que só acusam quando o nome
#     inteiro é um identificador técnico em minúsculas com hífens.
ALWAYS = ("gcpmd", "ecsmd", "169.254", "127.0.0.1", "nip.io", "ssrf",
          "exfil", "rebind", "httpbin", "metadata-goog", "localhost")
SLUG_ONLY = ("beacon", "redir", "iframe", "script", "data-url", "lo-port",
             "svg-internal", "nip", "dns", "ctl")
_SLUGGY = re.compile(r"^[a-z0-9][a-z0-9.\-]*$")

JUNK_SLUG = re.compile(
    r"(^|[-_])(test|demo|sample|staging|dummy)([-_]|\d|$)|probe", re.I)

# --------------------------------------------------------------------------- #
# Nomes que passam a regra e mesmo assim não são obras                          #
# --------------------------------------------------------------------------- #
#
# A corrida de 23/09 devolveu 429 candidatos, e entre eles coisas que são
# nomes próprios e únicos mas não são peças:
#
#   "yoso167.base.eth"        um NOME DE UTILIZADOR. São identidades de
#                             pessoas, há milhões, e apontar uma hunt à
#                             conta de alguém é outra coisa que não um jogo.
#   "0.64% Voting Power"      recibo de governação
#   "Lv. 1 Power Gem - (7,54)" coordenadas de jogo
#
# Não é gosto: nenhum destes dá uma pista que se possa escrever, e o
# primeiro dá um problema que não queremos ter.
_DOMAINISH = re.compile(r"\.(eth|sol|xyz|com|io|crypto|nft|dao|x)$", re.I)
_PERCENTISH = re.compile(r"^\s*\d+([.,]\d+)?\s*%")
_COORDISH = re.compile(r"\(\s*-?\d+\s*,\s*-?\d+\s*\)")
# Lixo que o marketplace cola ao nome — avisos de imitação, verificações.
# Sem isto o teste do domínio falhava: `perúesclave.eth ⚠` não acaba em
# `.eth`, acaba num triângulo, e dois nomes do ENS passaram (23/09).
_TRAILING_JUNK = re.compile(r"[\s​-‏⁠]*[⚠️✅❗‼⁉️🔺]*\s*$")


def usable_name(raw: str) -> bool:
    """Nome próprio E único ainda não chega: tem de ser um TÍTULO."""
    s = _TRAILING_JUNK.sub("", raw.strip()).strip()
    return not (_DOMAINISH.search(s) or _PERCENTISH.match(s)
                or _COORDISH.search(s))


def hostile_hits(names: list[str]) -> list[str]:
    """As palavras de ataque encontradas — lista, nunca um carimbo: quem lê
    a tabela tem de ver a prova e poder discordar."""
    hits: set[str] = set()
    for raw in names:
        low = raw.strip().lower()
        hits.update(w for w in ALWAYS if w in low)
        if _SLUGGY.match(low) and "-" in low:
            hits.update(w for w in SLUG_ONLY if w in low)
    return sorted(hits)


# --------------------------------------------------------------------------- #
# OpenSea                                                                       #
# --------------------------------------------------------------------------- #


class ApiTrouble(RuntimeError):
    """O PEDIDO falhou — do nosso lado ou do deles. R8: não diz nada sobre
    a colecção e não pode virar um veredicto sobre ela."""


def _get(path: str, key: str, **params) -> dict:
    # O CAMINHO TEM DE SER CITADO (23/09). A corrida de Ethereum perdeu 266
    # de 300 colecções com UnicodeEncodeError, e a causa era esta linha: o
    # slug ia cru para dentro do URL. Slugs com acentos ou emoji — que em
    # Ethereum são muitos — rebentavam no urlopen antes de sair pedido
    # nenhum. Não era o OpenSea nem a cadeia: era o meu f-string.
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
        # A MENSAGEM VAI JUNTO. A v3 guardava só o nome do tipo, e o
        # relatório dizia "266 UnicodeEncodeError" sem uma palavra sobre
        # ONDE. Fiquei a adivinhar; é a causa engolida outra vez, um nível
        # abaixo. O tipo diz o quê, a mensagem diz onde.
        raise ApiTrouble(f"{type(e).__name__}: {e}"[:120]) from e


def harvest(slug: str, contract: str, key: str, *, limit: int) -> dict:
    """Os tokens desta colecção cujo nome-base é ÚNICO lá dentro."""
    out: dict = {"why": "", "seen": 0, "exhaustive": False,
                 "hostile": [], "targets": []}
    try:
        data = _get(f"collection/{urllib.parse.quote(slug, safe='')}/nfts",
                    key, limit=limit)
    except ApiTrouble as e:
        out["why"] = str(e)
        return out
    nfts = data.get("nfts") or []
    if not nfts:
        out["why"] = "a colecção não devolveu tokens"
        return out

    named = [(str(n.get("identifier") or ""), str(n.get("name") or "").strip())
             for n in nfts if n.get("name") and n.get("identifier")]
    out["seen"] = len(nfts)
    # Pedimos `limit` e vieram menos: vimos a colecção inteira, portanto a
    # unicidade que medimos é exacta e não uma estimativa de amostra.
    out["exhaustive"] = len(nfts) < limit
    if not named:
        out["why"] = f"{len(nfts)} token(s), nenhum com título"
        return out

    out["hostile"] = hostile_hits([n for _i, n in named])
    if out["hostile"]:
        return out

    counts = Counter(base_name(n).lower() for _i, n in named)
    for tid, raw in named:
        base = base_name(raw)
        if counts[base.lower()] != 1:
            continue                      # edição, bilhete, pack ou PFP
        if not name_qualifies(base):
            continue                      # menos de duas palavras a sério
        if not usable_name(raw):
            continue                      # domínio, percentagem, identificador
        # O NOME QUE SAI É O CRU (23/09). A tira de serial serve para
        # AGRUPAR — para saber se este nome se repete na colecção — e não
        # para rebaptizar a peça. A primeira corrida mostrou porquê:
        #
        #   "$243M Theft - August 19, 2024"  →  "$243M Theft - August 19,"
        #   "Onchain Gaias - April 2024"     →  "Onchain Gaias - April"
        #   "… Vaporeon #168 CGC 9.5"        →  "… Vaporeon #168 CGC 9."
        #
        # A tira comeu anos e classificações que fazem parte do título. Se
        # um desses nomes truncados fosse para a frente, o commitment selava
        # um nome que a peça NÃO tem e o reveal afirmava uma falsidade
        # verificável por qualquer pessoa. Agrupar por uma chave agressiva
        # não custa nada; baptizar por ela custa a credibilidade do jogo.
        out["targets"].append((tid, raw, base))
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--chain", default="base")
    ap.add_argument("--pages", type=int, default=4)
    ap.add_argument("--order-by", default="market_cap",
                    help="market_cap (default), num_owners, created_date")
    ap.add_argument("--limit", type=int, default=50,
                    help="tokens a pedir por colecção, máx 50")
    ap.add_argument("--max-per-collection", type=int, default=6,
                    help="quantos alvos aceitar da mesma colecção (default 6) "
                         "— a variedade é o ponto, não encher de um sítio só")
    ap.add_argument("--out", default="",
                    help="ficheiro para a lista chain:contract:tokenId")
    args = ap.parse_args()

    s = get_settings()
    key = getattr(s, "opensea_api_key", "") or ""
    if not key:
        print(f"{NO} sem OPENSEA_API_KEY no .env")
        return 2

    print("=" * 76)
    print(f"ALVOS EM {args.chain.upper()} — nome próprio, único na colecção")
    print("=" * 76)

    cols: list[tuple[str, str]] = []
    cursor = ""
    try:
        for _page in range(max(1, args.pages)):
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
        print(f"\n{MEH} a API respondeu sem colecções para {args.chain}.")
        return 1

    print(f"  {len(cols)} colecção(ões) por {args.order_by}\n")

    targets: list[tuple[str, str, str, str]] = []   # contrato, tid, base, slug
    hostile: list[tuple[str, str, list[str]]] = []
    failures: Counter = Counter()
    junk = allrepeat = 0

    for slug, addr in cols:
        if JUNK_SLUG.search(slug):
            junk += 1
            continue
        h = harvest(slug, addr, key, limit=min(args.limit, 50))
        time.sleep(PAUSE_S)

        if h["why"]:
            failures[h["why"]] += 1
            continue
        if h["hostile"]:
            hostile.append((slug, addr, h["hostile"]))
            print(f"  {WARN} {slug[:40]:40} HOSTIL: {', '.join(h['hostile'])}")
            continue
        if not h["targets"]:
            allrepeat += 1
            continue

        keep = h["targets"][:args.max_per_collection]
        mark = "toda" if h["exhaustive"] else f"amostra de {h['seen']}"
        print(f"  {OK} {slug[:40]:40} {len(keep)} alvo(s) · {mark}")
        for tid, raw, key in keep:
            extra = "" if key == raw else f"   [agrupado por {key!r}]"
            print(f"        #{tid:<10} {raw}{extra}")
            targets.append((addr, tid, raw, slug))

    # ------------------------------------------------------------------ #
    print("\n" + "=" * 76)
    print("VEREDICTO")
    print("=" * 76)
    if failures:
        print(f"{sum(failures.values())} colecção(ões) NÃO MEDIDAS — por tipo:")
        for why, n in failures.most_common():
            print(f"    {n:>4}  {why}")
        print()
    if junk:
        print(f"{junk} com nome de teste/demo/probe, postas de lado.")
    if allrepeat:
        print(f"{allrepeat} onde TODOS os nomes se repetem — edições, "
              "bilhetes, packs, PFPs.")
    if hostile:
        print(f"\n{WARN} {len(hostile)} colecção(ões) com payloads de ataque "
              "nos nomes. NÃO tocar:")
        for slug, addr, words in hostile:
            print(f"    {slug[:34]:34} {addr}  ({', '.join(words)})")

    if not targets:
        print("\nNenhum alvo. Tenta --order-by num_owners ou mais --pages.")
        return 1

    cons = len({c for c, _t, _b, _s in targets})
    print(f"\n{len(targets)} alvo(s) candidato(s) em {cons} contrato(s).")
    print("Comparação: a despensa de hoje são 3 contratos, todos Ethereum.\n")

    lines = [f"{args.chain}:{c}:{t}" for c, t, _b, _s in targets]
    if args.out:
        with open(args.out, "w", encoding="utf-8") as f:
            f.write("\n".join(lines) + "\n")
        print(f"Lista escrita em {args.out} ({len(lines)} linhas).")
    else:
        for line, (_c, _t, base, _s) in zip(lines, targets):
            print(f"    {line}    # {base}")

    print("\nO QUE FALTA, e não é pouco: isto não falou com a cadeia. Os")
    print("outros quatro testes — tokenURI endereçado por conteúdo, imagem")
    print("legível, dono EOA, idade mínima — e a unicidade do nome no")
    print("mercado inteiro correm no depósito, não aqui. Um alvo desta lista")
    print("ainda não é um alvo: é um candidato a candidato.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
