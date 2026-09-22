"""Um palpite julgado tem sempre resposta. O gozo é que é uma vez por perfil.

HUNT #12, 18/09. Por volta das 16:30 o fio estava no auge — onze contas a
tentar, palpites de minuto a minuto — e o oráculo estava calado para toda
a gente, incluindo para quem escrevia no formato certo. Não havia avaria:
`taunted` guarda um gozo por perfil por hunt (custo de LLM, spam, e o X
despromove respostas quase iguais), e a contagem de tentativas que
construímos no dia anterior estava pendurada nesse gozo. Cada jogador
ouvia "four left" uma vez na vida e depois nunca mais.

Do lado de quem joga, isso é indistinguível de ser ignorado.

A regra passa a ser: o gozo uma vez, o recibo sempre. Sem gozo, a resposta
é uma linha seca — sem LLM, e com um número que muda de cada vez, por isso
nem é quase-duplicado.

Estes testes correm o `_claim_loop` VERDADEIRO. A primeira versão que
escrevi tinha um ajudante que repetia a lógica do loop, e teria passado com
o código de produção intacto — o erro exacto que já cometi nos taunts.
"""
from __future__ import annotations

from datetime import timedelta

from test_claim_by_post import _post, _replies_to, _rig


def _wrongs(hunt, t0, author, n, first_id=7700):
    """n palpites errados e bem formados, do mesmo autor, minuto a minuto."""
    return [
        _post(first_id + i, author, f"guess CD{i}EF{i}GH", t0 + timedelta(minutes=i),
              hunt.reshare_post_id)
        for i in range(1, n + 1)
    ]


def _run(n_guesses: int, author: str = "42"):
    rig, orch, hunt, src = _rig()
    posts = _wrongs(hunt, hunt.live_at, author, n_guesses)
    src.schedule[1] = lambda: posts
    orch._max_rounds = 4
    try:
        orch._claim_loop(hunt)
    except RuntimeError:
        pass                      # sem vencedor — esperado
    return rig, posts


def test_every_wrong_guess_gets_an_answer():
    """O TESTE. Antes disto, do segundo palpite em diante era silêncio."""
    rig, posts = _run(3)
    respostas = [_replies_to(rig, p.tweet_id) for p in posts]
    assert all(respostas), f"palpites sem resposta: {respostas}"


def test_the_jeer_is_still_once_per_profile():
    """A correcção não pode virar spam."""
    rig, posts = _run(3)
    textos = [r[0] for p in posts for r in [_replies_to(rig, p.tweet_id)] if r]
    curtas = [t for t in textos if "left." in t and len(t.split()) <= 3]
    assert len(curtas) == 2, textos       # 2.º e 3.º palpites: só o recibo
    assert len(textos) == 3


def test_the_count_walks_down_across_the_thread():
    rig, posts = _run(3)
    textos = [_replies_to(rig, p.tweet_id)[0] for p in posts]
    assert "four left." in textos[0]      # 1.º: gozo + contagem
    assert textos[1] == "three left."
    assert textos[2] == "two left."


def test_the_receipt_costs_no_llm():
    """O gozo é gerado; o recibo é uma linha nossa. Com o TauntEngine sem
    cliente (o do rig), os dois caminhos funcionam — o que prova que o
    recibo não depende de modelo nenhum."""
    rig, posts = _run(4)
    assert _replies_to(rig, posts[3].tweet_id)[0] == "one left."


def test_the_fifth_gets_the_closing_line_and_the_sixth_silence():
    """O quinto esgota a conta e leva o fecho; o sexto nem é julgado."""
    from finding_memeland.orchestrator.state_machine import POST_REPLY_OUT_OF_TRIES
    rig, posts = _run(6)
    assert _replies_to(rig, posts[4].tweet_id) == [POST_REPLY_OUT_OF_TRIES]
    assert _replies_to(rig, posts[5].tweet_id) == []
    subs = [s for s in rig.repo.submissions if s["sender_x_id"] == "42"]
    assert [s["outcome"] for s in subs].count("bad_code") == 5


def test_two_players_keep_separate_counts():
    """A contagem é por perfil: o recibo de um não fala do outro."""
    rig, orch, hunt, src = _rig()
    t0 = hunt.live_at
    a = _wrongs(hunt, t0, "42", 2, first_id=7800)
    b = _wrongs(hunt, t0, "77", 2, first_id=7900)
    src.schedule[1] = lambda: a + b
    orch._max_rounds = 4
    try:
        orch._claim_loop(hunt)
    except RuntimeError:
        pass
    assert _replies_to(rig, a[1].tweet_id)[0] == "three left."
    assert _replies_to(rig, b[1].tweet_id)[0] == "three left."
