#!/usr/bin/env python3
"""Re-verifica a unicidade do nome-base SÓ dos candidatos já apurados
(candidatos_alvo.json), com ritmo lento para não tropeçar no rate limit da
Rarible — o run grande fez as chamadas em rajada e 33/37 ficaram por verificar.

Corre do raiz do repo (usa as funções do medidor, no mesmo directório):

    .venv/bin/python scripts/verificar_unicidade.py [candidatos_alvo.json]

Reescreve o ficheiro com name_unique preenchido e imprime o resumo. Os que
ficarem False são removidos do ficheiro (deixavam de ser candidatos), para o
testar_escrevibilidade.py medir só sobre quem sobrevive aos filtros duros.
"""

from __future__ import annotations

import json
import sys
import time

from medir_universo_alvos import name_is_unique, read_env  # mesmo directório
from medir_universo_alvos import RARIBLE_BASE, UA, _SSL_CTX  # diagnóstico


def diagnose(key: str, name: str) -> bool:
    """UMA chamada crua com o erro à vista — o name_is_unique esconde-o por
    desenho. Devolve True se a API respondeu 200 (vale a pena continuar)."""
    import urllib.error
    import urllib.request

    body = json.dumps({
        "size": 25,
        "filter": {"fullText": {"text": name}, "blockchains": ["BASE"]},
    }).encode()
    req = urllib.request.Request(
        f"{RARIBLE_BASE}/items/search", data=body,
        headers={"Content-Type": "application/json",
                 "Accept": "application/json", "X-API-KEY": key, **UA},
    )
    try:
        with urllib.request.urlopen(req, timeout=25, context=_SSL_CTX) as r:
            raw = r.read(400).decode("utf-8", "replace")
        print(f"diagnóstico: HTTP 200 — resposta começa com: {raw[:200]}")
        return True
    except urllib.error.HTTPError as e:
        detail = e.read(400).decode("utf-8", "replace") if e.fp else ""
        print(f"diagnóstico: HTTP {e.code} {e.reason} — corpo: {detail[:200]}")
        return False
    except Exception as e:  # noqa: BLE001
        print(f"diagnóstico: excepção {type(e).__name__}: {e}")
        return False


def main() -> int:
    path = sys.argv[1] if len(sys.argv) > 1 else "candidatos_alvo.json"
    key = read_env("RARIBLE_API_KEY") or ""
    if not key:
        print("RARIBLE_API_KEY não encontrada.")
        return 2
    with open(path, encoding="utf-8") as f:
        cands = json.load(f)

    pending = [c for c in cands if c.get("name_unique") is not True]
    if pending and not diagnose(key, pending[0]["name"]):
        print("A API não está a responder 200 — corrige a causa acima antes de "
              "gastar as 33 chamadas. (403 = chave inválida/bloqueada; 429 = "
              "quota esgotada, espera; outro = mostra-me o diagnóstico.)")
        return 1

    kept, dead, unknown = [], 0, 0
    for i, c in enumerate(cands, 1):
        uniq = c.get("name_unique")
        if uniq is not True:          # re-testa os ? e confirma os x
            uniq = name_is_unique(key, c["name"], c["contract"], c["tokenId"])
            time.sleep(1.6)           # ritmo: ~37 chamadas ≈ 1 min, sem 429
        mark = "U" if uniq is True else ("?" if uniq is None else "x")
        print(f"[{i:2}/{len(cands)}] [{mark}] {c['name'][:52]!r}")
        c["name_unique"] = uniq
        if uniq is True:
            kept.append(c)
        elif uniq is None:
            unknown += 1
            kept.append(c)            # inconclusivo fica, marcado — nunca falso STOP
        else:
            dead += 1

    print("\n================= RESULTADO =================")
    print(f"únicos confirmados: {sum(1 for c in kept if c['name_unique'] is True)}"
          f" | mortos (não únicos): {dead} | inconclusivos: {unknown}")
    n = len(cands)
    r = sum(1 for c in kept if c["name_unique"] is True) / n
    print(f"taxa de unicidade real nesta amostra de candidatos: {r:.0%}")
    with open(path, "w", encoding="utf-8") as f:
        json.dump(kept, f, ensure_ascii=False, indent=1)
    print(f"{len(kept)} candidatos → {path} (não-únicos removidos)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
