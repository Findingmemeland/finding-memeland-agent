#!/usr/bin/env python3
"""check_search_guard.py — why does the search guard get an HTTP 400?

MEASURED 17/09, live: `/prepare` refused with "unverifiable after 3
attempts (HTTP Error 400: Bad Request)". A 400 is not a rate limit and not
an outage — it is a request the server refuses to parse, and it will fail
identically for ever.

The suspicious part is that the SAME OpenSea surface answers the
uniqueness check happily (16 name rejections in the live /fill the same
day). The two calls differ by one parameter:

    uniqueness  /search?query=…&asset_types=nft&limit=50
    the guard   /search?query=…&asset_types=nft&limit=50&chains=ethereum

So this script takes the parameters apart one at a time, against the real
API, with the production adapter's own URL shape. It prints the status and
the first part of the error body — which is where an API says what it
actually disliked.

Nothing is written, nothing is published, no LLM is called. It spends a
handful of requests out of a 120/minute quota.

    python scripts/check_search_guard.py
    python scripts/check_search_guard.py --name "Salt Harbor"
"""
from __future__ import annotations

import argparse
import json
import sys
import urllib.error
import urllib.request
from urllib.parse import quote

sys.path.insert(0, "src")

from finding_memeland.config import get_settings                     # noqa: E402

OK, NO = "✓", "✗"
UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/124.0 Safari/537.36")


def call(url: str, key: str) -> tuple[int, str]:
    req = urllib.request.Request(url, headers={
        "X-API-KEY": key, "Accept": "application/json", "User-Agent": UA})
    try:
        with urllib.request.urlopen(req, timeout=15) as r:
            return r.status, r.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode("utf-8", "replace")
    except Exception as e:  # noqa: BLE001
        return 0, f"{type(e).__name__}: {e}"


def show(label: str, url: str, key: str) -> int:
    """Returns the HTTP status — 0 when we never reached the API at all.

    The distinction is the whole point of this script and I got it wrong
    the first time (17/09): an SSL failure on the laptop is not the API
    refusing anything, and reading it as "the key is wrong" sends you
    looking in the opposite direction. R8 applies to diagnostics too."""
    status, body = call(url, key)
    good = status == 200
    print(f"\n  {OK if good else NO} {label}")
    print(f"     {url.split('?', 1)[1] if '?' in url else url}")
    if good:
        try:
            n = len(json.loads(body).get("results") or [])
            print(f"     200 · {n} resultado(s)")
        except Exception:  # noqa: BLE001
            print(f"     200 · corpo ilegível ({len(body)} chars)")
    elif status == 0:
        print(f"     NÃO CHEGÁMOS À API · {body[:300]}")
    else:
        print(f"     {status} · {body[:300]}")
    return status


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--name", default="Salt Harbor",
                    help="the canary query (any piece name will do)")
    ap.add_argument("--base", default="https://api.opensea.io/api/v2")
    args = ap.parse_args()

    s = get_settings()
    key = getattr(s, "opensea_api_key", "") or ""
    if not key:
        print(f"{NO} OPENSEA_API_KEY vazia — a guarda usaria a Rarible")
        return 2
    q = quote(args.name)
    base = args.base.rstrip("/")

    print("=" * 68)
    print("A GUARDA DE PESQUISA — porque é que dá 400")
    print("=" * 68)
    print(f"  chave: …{key[-4:]}  ·  query: {args.name!r}")

    # Exactly the two URLs the production adapter builds, and then the
    # pieces of the difference between them, one at a time.
    results = {
        "unicidade (sem chains) — isto funcionou no /fill":
            show("unicidade", f"{base}/search?query={q}&asset_types=nft&limit=50", key),
        "guarda (com chains=ethereum) — isto dá 400 ao vivo":
            show("guarda", f"{base}/search?query={q}&asset_types=nft"
                           f"&limit=50&chains=ethereum", key),
        "só chains, sem asset_types":
            show("chains sozinho", f"{base}/search?query={q}&chains=ethereum", key),
        "chain= no singular, em vez de chains=":
            show("chain singular", f"{base}/search?query={q}&asset_types=nft"
                                   f"&limit=50&chain=ethereum", key),
        "limit menor (a quota documentada é 50)":
            show("limit=20", f"{base}/search?query={q}&asset_types=nft"
                             f"&limit=20&chains=ethereum", key),
    }

    # --------------------------------------------------------------- #
    # The URL shape is not what differs in production. The TEXT is.     #
    # --------------------------------------------------------------- #
    # Here the query is a two-word name. Live, the guard sends the whole
    # CLUE — a sentence with punctuation, dashes and quotes, and a length
    # nobody ever measured against this endpoint. A length limit or a
    # character the parser rejects would 400 every clue and never a name,
    # which is exactly the pattern: uniqueness fine, guard dead.
    print("\n" + "=" * 68)
    print("O TEXTO — é aqui que a guarda difere da unicidade")
    print("=" * 68)
    short = "A mineral the sea leaves behind, guarding ships"
    dashes = ("A mineral the sea leaves behind — guarding ships that "
              "haven't sailed since the lighthouse's keeper left")
    long_one = ("It sits where the water gives up its salt and the light "
                "gives up its watch, a place named twice over for what it "
                "keeps and what it cannot hold, and the second word is the "
                "one that remembers the first")
    texts = {
        "frase curta (46 chars)": short,
        "travessão + apóstrofo (105 chars)": dashes,
        f"frase longa ({len(long_one)} chars)": long_one,
        "muito longa (400 chars)": (long_one + " ") * 2,
        "com barra / e sinais": "a keeper's light / half a name #1 & a harbour",
    }
    text_results = {}
    for label, t in texts.items():
        text_results[label] = show(
            label, f"{base}/search?query={quote(t)}&asset_types=nft"
                   f"&limit=50&chains=ethereum", key)

    print("\n" + "=" * 68)
    for label, status in list(results.items()) + list(text_results.items()):
        mark = OK if status == 200 else NO
        print(f"  {mark} {label}" + ("" if status else "   (nem chegou à API)"))
    print("=" * 68)

    uniq = results["unicidade (sem chains) — isto funcionou no /fill"]
    bad_text = [k for k, v in text_results.items() if v != 200]
    if not any(list(results.values()) + list(text_results.values())):
        print("\nVEREDICTO: NENHUMA chegou a falar com a OpenSea — transporte,\n"
              "não a API. Se disser CERTIFICATE_VERIFY_FAILED, é o certificado\n"
              "deste Mac, e não tem nada a ver com a guarda:\n"
              '  export SSL_CERT_FILE=$(python -c "import certifi; '
              'print(certifi.where())")')
    elif bad_text:
        print("\nVEREDICTO: é o TEXTO, não a forma do URL. Estas falharam:")
        for k in bad_text:
            print(f"    · {k}")
        print("A guarda manda a pista inteira; a unicidade manda duas palavras.\n"
              "É por isso que uma morre e a outra vive.")
    elif all(v == 200 for v in list(results.values()) + list(text_results.values())):
        print("\nVEREDICTO: TUDO passa daqui. Então o 400 é do lado do Railway —\n"
              "transporte dele (proxy/UA/IP) ou uma pista com algo que nenhuma\n"
              "destas amostras tem. Próximo passo: imprimir o URL no erro.")
    else:
        print("\nVEREDICTO: ler a tabela — a variante que passa é a que fica.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
