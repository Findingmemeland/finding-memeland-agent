"""Sonda: descobrir o formato do filtro de pesquisa por NOME na API da Rarible.

Porquê uma sonda e não código directo: a documentação confirma o endpoint
(`POST /v0.1/items/search`) mas não mostra o campo do filtro de texto. Já hoje
adivinhei um contrato de API (prefill do assistente) e custou 21 falhas em 21 —
por isso mede-se primeiro.

Alvo: o relic Uncle Pump, que sabemos estar indexado na Rarible.

    python probe_rarible.py

Não precisa de chave nem de .env. Só faz leituras.
Imprime, para cada formato tentado: código HTTP, nº de itens e se o NOSSO
contrato aparece na resposta. O formato certo é o que devolve 200 E encontra o
contrato.
"""

from __future__ import annotations

import json
import ssl
import urllib.error
import urllib.request

# O Python do macOS não traz cadeia de certificados utilizável, e o urllib rebenta
# com CERTIFICATE_VERIFY_FAILED. O certifi já está no venv (vem com o SDK), por
# isso usamos os certificados dele. Sem certifi, o contexto por omissão — e se
# falhar, o erro impresso diz porquê. Nunca se desliga a verificação.
try:
    import certifi

    _SSL = ssl.create_default_context(cafile=certifi.where())
except Exception:  # noqa: BLE001
    _SSL = ssl.create_default_context()

URL = "https://api.rarible.org/v0.1/items/search"
NAME = "Uncle Pump"
CONTRACT = "0x692f42dd372ae65f696c7e53083a2c915cb7c8ec"

# Formatos plausíveis para o filtro de texto. Um deles deve responder 200 com o
# nosso contrato lá dentro; os outros devem dar 400 (campo desconhecido).
SHAPES: dict[str, dict] = {
    "filter.text + blockchains":  {"size": 20, "filter": {"text": NAME, "blockchains": ["BASE"]}},
    "filter.text (sem chain)":    {"size": 20, "filter": {"text": NAME}},
    "filter.names":               {"size": 20, "filter": {"names": [NAME], "blockchains": ["BASE"]}},
    "filter.fullText":            {"size": 20, "filter": {"fullText": {"text": NAME}, "blockchains": ["BASE"]}},
    "filter.text + sort NAME_ASC": {"size": 20, "sort": "NAME_ASC",
                                    "filter": {"text": NAME, "blockchains": ["BASE"]}},
}


# Cloudflare 1010 = "browser signature" bloqueada. O User-Agent do urllib
# (Python-urllib/3.x) é o suspeito óbvio, por isso mandamos um de browser.
BROWSER_HEADERS = {
    "Content-Type": "application/json",
    "Accept": "application/json",
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
    ),
    "Origin": "https://rarible.com",
    "Referer": "https://rarible.com/",
}

# Chave grátis em https://api.rarible.org/dashboard.
#   .venv/bin/python probe_rarible.py <CHAVE>
# ou export RARIBLE_API_KEY=... antes de correr.
import os  # noqa: E402
import sys  # noqa: E402

API_KEY = (sys.argv[1] if len(sys.argv) > 1 else os.environ.get("RARIBLE_API_KEY", "")).strip()
# O nome do header não está na doc que li; testamos os dois formatos usuais e
# ficamos com o que responder 200. Medir, não adivinhar.
KEY_HEADERS = {
    "X-API-KEY": lambda k: {"X-API-KEY": k},
    "Authorization Bearer": lambda k: {"Authorization": f"Bearer {k}"},
}


def headers_with(key_style: str) -> dict:
    h = dict(BROWSER_HEADERS)
    if API_KEY:
        h.update(KEY_HEADERS[key_style](API_KEY))
    return h


def probe_get(label: str, url: str, key_style: str = "X-API-KEY") -> None:
    """Um GET simples diz se a chave é aceite e em que header."""
    req = urllib.request.Request(url, headers=headers_with(key_style))
    try:
        with urllib.request.urlopen(req, timeout=25, context=_SSL) as resp:
            raw = resp.read().decode("utf-8", "ignore")
        print(f"  {label:30} 200  {raw[:120]}")
    except urllib.error.HTTPError as e:
        detail = e.read()[:120].decode("utf-8", "ignore").replace("\n", " ")
        print(f"  {label:30} HTTP {e.code}  {detail}")
    except Exception as e:  # noqa: BLE001
        print(f"  {label:30} ERRO {type(e).__name__}: {str(e)[:80]}")


def try_shape(label: str, body: dict, key_style: str = "X-API-KEY") -> None:
    req = urllib.request.Request(
        URL, data=json.dumps(body).encode(), headers=headers_with(key_style),
    )
    try:
        with urllib.request.urlopen(req, timeout=25, context=_SSL) as resp:
            raw = resp.read().decode("utf-8", "ignore")
    except urllib.error.HTTPError as e:
        detail = e.read()[:200].decode("utf-8", "ignore").replace("\n", " ")
        print(f"  {label:30} HTTP {e.code}  {detail}")
        return
    except Exception as e:  # noqa: BLE001
        print(f"  {label:30} ERRO {type(e).__name__}: {str(e)[:90]}")
        return

    try:
        data = json.loads(raw)
    except ValueError:
        print(f"  {label:30} 200 mas a resposta não é JSON")
        return

    items = data.get("items") or []
    found = CONTRACT in raw.lower()
    flag = "✅ ENCONTROU O NOSSO" if found else "— não encontrou"
    print(f"  {label:30} 200  itens={len(items):<3} {flag}")
    if found:
        # Mostra a forma real do item, que é o que o adaptador vai ter de ler.
        for item in items:
            if CONTRACT in json.dumps(item).lower():
                print("       chaves do item:", sorted(item.keys()))
                print("       amostra:", json.dumps(item)[:300])
                break


def main() -> int:
    if not API_KEY:
        print(
            "\n⚠ Sem chave. Vai a https://api.rarible.org/dashboard (grátis, 1 min) e corre:\n"
            "   .venv/bin/python probe_rarible.py <CHAVE>\n"
        )
        return 1

    print("\n1) Em que header vai a chave?\n")
    item_url = f"https://api.rarible.org/v0.1/items/BASE:{CONTRACT}:1"
    for style in KEY_HEADERS:
        probe_get(f"GET item via {style}", item_url, style)

    print(f"\n2) Formato do filtro de pesquisa por nome ({NAME!r})\n")
    for style in KEY_HEADERS:
        print(f"  — com {style} —")
        for label, body in SHAPES.items():
            try_shape(label, body, style)

    print(
        "\nCola-me o output. Preciso de duas coisas:\n"
        "• qual o header da chave que dá 200\n"
        "• qual o formato do filtro que encontra o nosso contrato\n"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
