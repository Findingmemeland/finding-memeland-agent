"""A âncora é verificada na API, nunca na interface do X.

HUNT #11, 17/09. A meio do hunt o X deixou de mostrar o post da Clue 1.
O operador concluiu que o tinha apagado — e era a conclusão razoável, dado
o que tinha à frente. Publicou-se uma âncora nova, editou-se `reshare_post_id`
à mão no Supabase, reiniciou-se o Railway. Vinte minutos, com gente à espera
de pistas. No fim, o post original nunca tinha sido apagado.

A regra: um post só está apagado quando a API o diz.

E a parte que custou os vinte minutos não foi não saber — foi não conseguir
distinguir "não está lá" de "não consegui perguntar". As duas levam a
decisões opostas. Por isso são três respostas, nunca duas.
"""
from __future__ import annotations

from finding_memeland.runtime import anchor_status_line


def test_a_live_anchor_says_so_and_says_where_it_looked():
    """O "(API...)" não é enfeite: é para quem lê saber que o veredicto não
    veio de uma página aberta no telemóvel."""
    line = anchor_status_line("2100601855583879484",
                              lambda pid: {"id": pid, "text": "clue 1"})
    assert "✓" in line
    assert "API" in line
    assert "2100601855583879484" in line


def test_a_deleted_anchor_is_named_as_broken():
    line = anchor_status_line("123", lambda pid: None)
    assert "✗" in line
    assert "NÃO EXISTE" in line


def test_a_failure_to_ask_is_never_reported_as_a_deletion():
    """O TESTE. Se a chamada rebentar e isto disser "apagada", reproduzimos
    o erro de 17/09 com mais passos: alguém vai publicar uma âncora nova
    por cima de uma que está viva, e o hunt fica com duas."""
    def boom(_pid):
        raise TimeoutError("x api slow")

    line = anchor_status_line("123", boom)
    assert "✗" not in line
    assert "NÃO EXISTE" not in line
    assert "não consegui verificar" in line
    assert "TimeoutError" in line          # a causa, para se poder decidir
    assert "NÃO concluas" in line


def test_no_anchor_recorded_is_its_own_case():
    """Nem "existe" nem "apagada": nunca houve. Um hunt assim não tem onde
    pendurar as pistas e o problema é anterior ao X."""
    for empty in (None, "", 0):
        line = anchor_status_line(empty, lambda pid: {"id": pid})
        assert "nenhuma registada" in line


def test_the_id_is_passed_as_a_string():
    """O Supabase devolve-o ora como texto ora como número conforme a
    coluna; a API quer texto. Um int silenciosamente formatado em notação
    científica seria uma consulta a um post que não existe — e o veredicto
    seria "apagada"."""
    seen: list = []

    def spy(pid):
        seen.append(pid)
        return {"id": pid}

    anchor_status_line(2100601855583879484, spy)
    assert seen == ["2100601855583879484"]
