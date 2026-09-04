#!/usr/bin/env python3
"""Teste de escrevibilidade — a métrica que interessa (Opus, revisão 03/09, Q4).

Para cada candidato do medir_universo_alvos.py, pergunta ao MESMO modelo que
escreve as pistas se consegue produzir uma rampa completa de 7 peças de puzzle
sobre aquele nome + arte, sob a doutrina em vigor. Mede a taxa de candidatos
"escrevíveis" — o universo EFECTIVO é o universo do filtro × esta taxa.

Corre no Mac (usa o venv do repo, precisa de ANTHROPIC_API_KEY no .env/ambiente):

    .venv/bin/python scripts/testar_escrevibilidade.py candidatos_alvo.json

Aproximação assumida (barata, 1 chamada por candidato): pedimos as 7 peças e um
auto-veredicto estruturado; não corremos os guardrails nem o blind solver — isso
é a fase de produção. Este teste responde só a "há matéria-prima para 7 pistas?".
A imagem vai por URL quando é https directo; ipfs:// é convertido para gateway.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time

try:
    import anthropic
except ImportError:
    print("corre com o venv do repo: .venv/bin/python …")
    sys.exit(2)

DOCTRINE = """You write puzzle clues for a treasure hunt. The target is an \
existing NFT: a name (two or more words) and its artwork. Doctrine, strict: \
7 puzzle pieces total; each piece is ONE oblique constraint on one of the \
name's words or on the artwork, from a distinct angle (semantic field, \
cultural use, structure, relation between words, visual detail); NEVER the \
word itself, a synonym, a rhyme or an emoji; each word needs at least 2 \
pieces; at least 1 piece on the artwork; no piece may allow the answer to be \
guessed alone, but all 7 together must intersect on exactly this name.

Decide honestly whether this target gives you enough material for a FULL \
7-piece ramp. A generic name ('Cool Cat', 'Token 123'-style) or abstract \
unreadable art usually does NOT.

Reply with ONLY a JSON object, no fences: {"writable": true/false, \
"reason": "<one line>", "pieces": ["...", … 7 items when writable, else []]}"""


def env_key() -> str | None:
    k = os.environ.get("ANTHROPIC_API_KEY")
    if k:
        return k
    try:
        with open(".env", encoding="utf-8") as f:
            for line in f:
                if line.startswith("ANTHROPIC_API_KEY="):
                    return line.split("=", 1)[1].strip()
    except OSError:
        pass
    return None


def image_block(url: str) -> dict | None:
    if url.startswith("ipfs://"):
        path = url[len("ipfs://"):]
        url = "https://ipfs.io/ipfs/" + (path[5:] if path.startswith("ipfs/") else path)
    if url.startswith("https://"):
        return {"type": "image", "source": {"type": "url", "url": url}}
    return None


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("candidatos", nargs="?", default="candidatos_alvo.json")
    ap.add_argument("--max", type=int, default=50)
    ap.add_argument("--model", default=os.environ.get("ANTHROPIC_MODEL",
                                                      "claude-sonnet-4-6"))
    ap.add_argument("--out", default="escrevibilidade_resultados.json")
    args = ap.parse_args()

    key = env_key()
    if not key:
        print("ANTHROPIC_API_KEY não encontrada (.env ou ambiente).")
        return 2
    client = anthropic.Anthropic(api_key=key)

    with open(args.candidatos, encoding="utf-8") as f:
        cands = json.load(f)[: args.max]
    if not cands:
        print("Sem candidatos — corre primeiro o medir_universo_alvos.py.")
        return 2

    results = []
    writable = 0
    for i, c in enumerate(cands, 1):
        content: list[dict] = [{
            "type": "text",
            "text": (f"Target name: {c['name']}\n"
                     f"Marketplace description (may be empty): "
                     f"{c.get('description') or '(none)'}\n"
                     "Artwork: attached image if present; otherwise judge from "
                     "name+description only and be stricter."),
        }]
        img = image_block(c.get("image") or "")
        if img:
            content.insert(0, img)
        verdict = {"writable": False, "reason": "call failed", "pieces": []}
        for attempt in (1, 2):
            try:
                msg = client.messages.create(
                    model=args.model, max_tokens=900,
                    system=DOCTRINE,
                    messages=[{"role": "user", "content": content}],
                )
                text = "".join(b.text for b in msg.content if b.type == "text").strip()
                start, end = text.find("{"), text.rfind("}")
                verdict = json.loads(text[start:end + 1])
                break
            except Exception as e:  # noqa: BLE001
                if img and attempt == 1:      # imagem irrecuperável: tenta sem ela
                    content = content[1:]
                    continue
                verdict = {"writable": False, "reason": f"error: {e}", "pieces": []}
        ok = bool(verdict.get("writable")) and len(verdict.get("pieces") or []) == 7
        writable += ok
        results.append({**c, "writable": ok,
                        "reason": str(verdict.get("reason", ""))[:200]})
        print(f"[{i}/{len(cands)}] {'✅' if ok else '—'} {c['name'][:44]!r}"
              f"  ({verdict.get('reason', '')[:60]})")
        time.sleep(0.4)

    rate = writable / len(cands)
    print("\n================= RESULTADO =================")
    print(f"escrevíveis: {writable}/{len(cands)}  ({rate:.0%})")
    print("universo EFECTIVO = estimativa do filtro × esta taxa.")
    with open(args.out, "w", encoding="utf-8") as f:
        json.dump(results, f, ensure_ascii=False, indent=1)
    print(f"detalhe por candidato → {args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
