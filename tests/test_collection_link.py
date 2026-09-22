"""Um link de colecção não é um link ilegível: sabemos o que lhe falta.

HUNT #12, 18/09. Dois jogadores colaram a página da COLECÇÃO em vez da peça:

    @Bode5t    rarible.com/base/collections/0x2960bb…5f05
    @0xThalyn  (o mesmo contrato, à secas)

O agente respondeu "i can't read that link — my side, not yours". Não é
mentira, mas é uma verdade inútil: o link traz o contrato, portanto nós
sabemos exactamente o que falta — o tokenId — e mandámos a pessoa adivinhar
o que já sabíamos. Nenhum dos dois desceu da colecção até ao token sozinho.

A resposta passa a dizer o que falta. Continua a não acusar ninguém de nada
e continua a não gastar tentativa — a regra do #11 mantém-se intacta.

E o caso oposto, que esta correcção NÃO pode estragar: um link que não traz
contrato nenhum (slug do SuperRare, página do Foundation) continua a ser
"não consigo ler", porque aí é mesmo o nosso limite.
"""
from __future__ import annotations

from finding_memeland.claims.matcher import TargetClaimMatcher
from finding_memeland.target.claim import collection_link, extract_target_refs
from finding_memeland.target.templates import (
    POST_REPLY_COLLECTION_LINK,
    POST_REPLY_FORMAT,
    POST_REPLY_ONE_TOKEN,
    POST_REPLY_UNRESOLVED_LINK,
)

C = "0x2960bb0a0b50bccf93cb1cf1ceec301be8095f05"


# --------------------------------------------------------------------------- #
# collection_link — contrato sim, token não                                    #
# --------------------------------------------------------------------------- #


def test_the_two_real_links_from_hunt_12_are_collections():
    assert collection_link(f"https://rarible.com/base/collections/{C}")
    assert collection_link(f"https://opensea.io/collection/{C}")


def test_a_token_link_is_not_a_collection():
    """O que já resolvia tem de continuar a resolver."""
    for u in (f"https://opensea.io/item/ethereum/{C}/11385",
              f"https://rarible.com/token/{C}:11385",
              f"https://x.example/nft?contract={C}&tokenId=7"):
        assert not collection_link(u), u


def test_a_link_without_any_contract_is_not_a_collection():
    """Slug do SuperRare, página do Foundation: aí não sabemos o que falta,
    e prometer precisão que não temos seria pior do que admitir o limite."""
    assert not collection_link("https://superrare.com/artwork/eth/some-slug")
    assert not collection_link("https://foundation.app/@artist/piece/12")
    assert not collection_link("https://t.co/aBcD1234")
    assert not collection_link("")


# --------------------------------------------------------------------------- #
# A resposta que sai                                                           #
# --------------------------------------------------------------------------- #


class _Judge:
    """Nada resolve — é o caso em que a resposta de formato entra."""

    def judge(self, text, resolve_link=None):
        return type("V", (), {"matched": False, "checked": 0,
                              "unresolved": True})()


def _matcher(**kw):
    return TargetClaimMatcher(
        judge=_Judge(), resolve_link=None,
        format_reply=POST_REPLY_FORMAT,
        unresolved_reply=POST_REPLY_UNRESOLVED_LINK,
        one_token_reply=POST_REPLY_ONE_TOKEN, **kw)


def test_a_collection_link_is_told_what_is_missing():
    m = _matcher(collection_reply=POST_REPLY_COLLECTION_LINK)
    hint = m.format_hint(f"https://rarible.com/base/collections/{C}")
    assert hint == POST_REPLY_COLLECTION_LINK
    assert "collection" in hint and "tokenId" in hint


def test_it_still_costs_nothing_and_never_says_wrong():
    """A regra do Pedro (17/09) não se toca: ser mais preciso não é ser
    mais duro."""
    assert "cost you nothing" in POST_REPLY_COLLECTION_LINK
    assert "wrong" not in POST_REPLY_COLLECTION_LINK.lower()
    m = _matcher(collection_reply=POST_REPLY_COLLECTION_LINK)
    assert m.skip_judge(f"https://rarible.com/base/collections/{C}") is False
    assert m.looks_like_claim(f"https://rarible.com/base/collections/{C}") is False


def test_an_unreadable_link_with_no_contract_keeps_the_old_answer():
    """O caso em que o limite é mesmo nosso."""
    m = _matcher(collection_reply=POST_REPLY_COLLECTION_LINK)
    assert m.format_hint("https://t.co/aBcD1234") == POST_REPLY_UNRESOLVED_LINK


def test_two_links_never_reach_this_reply_at_all():
    """Escrevi o código a guardar-se de um post com uma colecção E um link
    ilegível, e escrevi um teste a afirmar que aí a resposta seria genérica.
    Estava errado: dois links por resolver são MALFORMADOS pela regra do um
    token por resposta, que corre primeiro e é anterior a tudo isto.

    Ou seja, o `all(...)` no matcher nunca chega a ver mais do que um link.
    Fica como cinto — mas o teste regista o que acontece de facto, em vez de
    provar uma defesa que o código nunca exercita."""
    m = _matcher(collection_reply=POST_REPLY_COLLECTION_LINK)
    text = f"https://rarible.com/base/collections/{C} or https://t.co/aBcD1234"
    ext = extract_target_refs(text)
    assert len(ext.unresolved_links) == 2
    assert m.is_malformed(text) is True
    assert m.format_hint(text) == POST_REPLY_ONE_TOKEN


def test_without_the_new_reply_nothing_changes():
    """A correcção é aditiva: quem construir o matcher sem ela vê o
    comportamento antigo, intacto."""
    m = _matcher()
    assert m.format_hint(f"https://rarible.com/base/collections/{C}") == \
        POST_REPLY_UNRESOLVED_LINK
