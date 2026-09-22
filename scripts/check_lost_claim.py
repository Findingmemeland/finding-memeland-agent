#!/usr/bin/env python3
"""check_lost_claim.py — porque é que ESTE post nunca foi processado?

HUNT #12, 18/09. O @Bode5t publicou um palpite bem formado
(`ethereum:0xecd6…794b:5111`) e o oráculo nunca respondeu, enquanto
respondia a outros do mesmo autor minutos antes e depois. Spray e cadência
geral ficaram excluídos por observação.

Sobram três hipóteses, e só a API as distingue:

  A. o post NÃO está no fio da Clue 1 (conversation_id diferente)
     → está correctamente fora do jogo; a regra 3 manda responder à Clue 1

  B. está no fio, mas responde a OUTRO post e não à âncora
     → invisível no fio das menções; só a varredura o apanha, de N em N
       ciclos. Chega tarde, não se perde.

  C. está no fio E responde à âncora
     → aí é NOSSO, e é o caso grave: um claim que devia ter sido julgado e
       não foi. A versão má desta falha é um post VENCEDOR desaparecer da
       mesma maneira.

Nada é escrito, nada é publicado. Uma leitura.

    python scripts/check_lost_claim.py 2100973136330076568
    python scripts/check_lost_claim.py 2100973136330076568 --anchor <id_da_clue_1>
"""
from __future__ import annotations

import argparse
import sys

sys.path.insert(0, "src")

import tweepy  # noqa: E402

from finding_memeland.config import get_settings  # noqa: E402

FIELDS = ["author_id", "created_at", "conversation_id", "entities",
          "in_reply_to_user_id", "referenced_tweets", "text"]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("tweet_id")
    ap.add_argument("--anchor", default="",
                    help="id do post da Clue 1, se o souberes")
    ap.add_argument("--sweep", action="store_true",
                    help="correr a MESMA varredura do agente sobre o fio e "
                         "dizer se este post aparece lá")
    a = ap.parse_args()

    s = get_settings()
    client = tweepy.Client(
        bearer_token=s.x_bearer_token,
        consumer_key=s.x_api_key, consumer_secret=s.x_api_secret,
        access_token=s.x_main_access_token,
        access_token_secret=s.x_main_access_secret,
    )
    resp = client.get_tweet(a.tweet_id, tweet_fields=FIELDS, user_auth=True)
    if resp.data is None:
        print(f"✗ o post {a.tweet_id} NÃO existe para a API")
        print(f"  errors: {getattr(resp, 'errors', None)}")
        return 1

    t = resp.data
    conv = str(getattr(t, "conversation_id", "") or "")
    refs = getattr(t, "referenced_tweets", None) or []
    replied_to = next((str(r.id) for r in refs
                       if getattr(r, "type", "") == "replied_to"), "")

    print("=" * 64)
    print(f"post        : {t.id}")
    print(f"autor       : {getattr(t, 'author_id', '?')}")
    print(f"criado      : {getattr(t, 'created_at', '?')}")
    print(f"texto       : {t.text!r}")
    print(f"conversation: {conv or '(nenhuma)'}")
    print(f"responde a  : {replied_to or '(não é resposta — post solto)'}")
    ents = getattr(t, "entities", None) or {}
    print(f"menções     : {[m.get('username') for m in ents.get('mentions', [])]}")
    print(f"urls        : {[u.get('expanded_url') for u in ents.get('urls', [])]}")
    print("=" * 64)

    # A âncora não tem de ser dada: a raiz do fio é o conversation_id, e a
    # API sabe-o. Pedir o id a um humano a meio de um hunt era transferir
    # para ele trabalho que a máquina faz sozinha.
    anchor = a.anchor.strip()
    if not anchor and conv:
        root = client.get_tweet(conv, tweet_fields=FIELDS, user_auth=True)
        if root.data is not None:
            anchor = str(root.data.id)
            head = (root.data.text or "").splitlines()[0][:60]
            print(f"\nraiz do fio : {anchor}  ·  {head!r}")
            if "is live" not in (root.data.text or "").lower():
                print("⚠️  a raiz não parece um post de Clue 1 — lê o texto acima")
    if not anchor:
        print("\nNão consegui determinar a âncora. Corre outra vez com")
        print("--anchor <id do post da Clue 1>.")
        return 0
    if conv != anchor:
        print("\nVEREDICTO A: o post NÃO está no fio da Clue 1.")
        print("Está correctamente fora do jogo — a regra 3 manda responder")
        print("ao post da Clue 1. Nada partido do nosso lado.")
    elif replied_to != anchor:
        print("\nVEREDICTO B: está no fio, mas responde a outro post.")
        print("O fio das menções não o mostra; só a varredura o apanha, de")
        print("N em N ciclos. Chega atrasado, não se perde. Vale a pena ver")
        print("se a varredura acabou por o devolver — procura o id nos logs.")
    else:
        print("\nVEREDICTO C: responde à ÂNCORA e mesmo assim não foi julgado.")
        print("Isto é nosso e é o caso grave. Procura este id nos logs do")
        print("Railway: se aparecer em [claim-sweep] raw mas não em returned,")
        print("o filtro comeu-o; se nunca aparecer, não chegou a ser lido.")
        print("Um post vencedor pode desaparecer exactamente assim.")

    if a.sweep and anchor:
        _sweep(anchor, a.tweet_id, s)
    elif not a.sweep:
        print("\n(corre outra vez com --sweep para a medição decisiva)")
    return 0


def _sweep(anchor: str, wanted: str, s) -> None:
    """A medição que separa a culpa: corre a MESMA varredura do agente.

    Se o post aparecer aqui, o X deu-no-lo e nós deixámo-lo cair — é nosso,
    e é corrigível. Se NÃO aparecer, o índice de pesquisa do X não devolve
    esta resposta, e nenhuma cadência de varredura nossa a recupera: aí a
    rede de segurança tem de ser outra (ler as respostas directas ao post,
    não a pesquisa por conversa).

    Sem isto, escolher a correcção era adivinhar de que lado estava a falha.
    """
    from finding_memeland.social.x_client import XClient

    x = XClient(
        api_key=s.x_api_key, api_secret=s.x_api_secret,
        bearer_token=s.x_bearer_token,
        main_access_token=s.x_main_access_token,
        main_access_secret=s.x_main_access_secret,
    )
    print("\n" + "=" * 64)
    print("VARREDURA DO FIO — exactamente a que o agente corre")
    print("=" * 64)
    rows = x.search_conversation(anchor, since_id=None)
    ids = [str(r.get("tweet_id") or r.get("id") or "") for r in rows]
    print(f"a varredura devolveu {len(rows)} post(s)")
    if wanted in ids:
        print(f"\n✓ o post {wanted} ESTÁ na varredura.")
        print("  → o X deu-no-lo. A perda é NOSSA, entre a varredura e o")
        print("    juízo. Procurar no state_machine porque é que ele saiu")
        print("    do lote: `processed`, o filtro de autor, ou a ordenação.")
    else:
        print(f"\n✗ o post {wanted} NÃO aparece na varredura.")
        print("  → o índice de pesquisa do X não devolve esta resposta.")
        print("    Nenhuma cadência nossa a recupera. A rede de segurança")
        print("    tem de deixar de ser search_recent_tweets e passar a ser")
        print("    a leitura das respostas directas à âncora.")
        print("\n  ids devolvidos (para veres o que ele dá):")
        for i in ids[:15]:
            print("   ", i)


if __name__ == "__main__":
    raise SystemExit(main())
