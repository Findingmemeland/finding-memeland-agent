"""`Ethereum: 0xabc…def:42` é um claim. O espaço é de quem escreve, não nosso.

HUNT #12, 18/09, medido ao vivo. O formato publicado é
`chain:contract:tokenId`. A forma como uma pessoa o escreve é
`Ethereum: 0x…:42` — com o espaço que toda a gente põe a seguir a dois
pontos. Quatro contas diferentes fizeram-no na mesma hunt:

    @kayceeonyia   Ethereum: 0x506411…E922D:2
    @cryptojohnbull Ethereum: 0x0e37…Ba228:1
    @cryptojohnbull Polygon: 0xd3987…984d: 24     (espaço dos dois lados)
    @VKing_14      ethereum: 0x506411…E922D:2

A todos, o agente respondeu que faltava "chain, contract AND tokenId" —
os três que eles tinham acabado de enviar. Dizer a alguém que lhe falta o
que ele deu é a mesma falha que "nunca dizer que está errado quando pode
estar certo" proíbe, só que virada do avesso. E a versão cara dela não é
irritar um jogador: é não reconhecer a resposta vencedora.

O limite: quebras de linha continuam a não ser toleradas. Um `\\n` entre a
cadeia e o contrato é alguém a citar duas coisas separadas, não um claim
mal escrito, e alargar até aí seria inventar intenção.
"""
from __future__ import annotations

from finding_memeland.target.claim import claim_shaped, extract_target_refs

ETH = "0x0e37Ac32dEE41020fc177cF116a55c4b0e8Ba228"
POLY = "0xd3987dfdfee3b4f198ce8be7753ae329a099984d"


def _id(text: str) -> str | None:
    refs = extract_target_refs(text).refs
    return refs[0].id() if refs else None


# --------------------------------------------------------------------------- #
# Os posts reais do hunt #12                                                   #
# --------------------------------------------------------------------------- #


def test_the_four_real_posts_that_were_refused_are_now_read():
    assert _id(f"Ethereum: {ETH}:1") == f"ethereum:{ETH.lower()}:1"
    assert _id("ethereum: 0x506411CdEDcF6c4A02ED25B0c4e0C765f70E922D:2") == \
        "ethereum:0x506411cdedcf6c4a02ed25b0c4e0c765f70e922d:2"
    assert _id(f"Polygon: {POLY}: 24") == f"polygon:{POLY.lower()}:24"
    assert _id(f"@findingmemeland Ethereum: {ETH}:1") is not None


def test_the_published_format_still_works_exactly_as_before():
    """A correcção alarga, não substitui."""
    assert _id(f"ethereum:{ETH}:1") == f"ethereum:{ETH.lower()}:1"
    assert _id(f"Ethereum:{ETH}:1") == f"ethereum:{ETH.lower()}:1"


def test_a_tab_counts_as_a_space():
    assert _id(f"ethereum:\t{ETH}:\t1") is not None


# --------------------------------------------------------------------------- #
# O que NÃO pode passar a ser lido                                             #
# --------------------------------------------------------------------------- #


def test_a_newline_is_not_a_claim():
    """Duas linhas são duas coisas. Ler através de um \\n seria inventar a
    intenção de quem escreveu — e a seguir inventá-la-íamos num post que
    cita dois tokens."""
    assert _id(f"Ethereum:\n{ETH}:1") is None
    assert _id(f"Ethereum: {ETH}\n:1") is None


def test_a_bare_contract_is_still_not_a_claim():
    """Continua a merecer o formato, não um veredicto."""
    assert _id(f"Base: {ETH}") is None


def test_chatter_that_happens_to_contain_an_address_is_still_chatter():
    assert claim_shaped(f"gm chat {ETH}") is False


def test_a_marketplace_link_is_untouched():
    c = "0xb932a70a57673d89f4acffbe830e8ed7f75fb9e0"
    assert _id(f"https://opensea.io/item/ethereum/{c}/11385") == \
        f"ethereum:{c}:11385"


def test_two_tokens_in_one_post_are_both_seen_so_it_can_be_refused():
    """A regra do um-token-por-resposta depende de os vermos aos dois. Se o
    espaço escondesse o segundo, um post malformado passava a válido — a
    correcção não pode abrir essa porta."""
    text = f"Ethereum: {ETH}:1 or maybe Polygon: {POLY}: 24"
    assert len(extract_target_refs(text).refs) == 2
