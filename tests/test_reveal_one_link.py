"""O reveal leva UM link clicável: a página do item. Mais nenhum.

HUNT #11, 17/09. O reveal saiu sem a arte — "não renderizou o NFT" (Pedro).
O X constrói o cartão a partir de um URL do post, e o post tinha dois: o
`tokenURI at launch`, dentro do bloco de verificação, e a página do item
lá mais abaixo. Um tokenURI aponta para um JSON de metadata, que não tem
cartão nenhum para construir.

O tokenURI NÃO faz parte do commitment — este é SHA-256(target_id +
metadata_sha256 + salt) e recalcula-se sem ele. Era um extra, e um extra
não vale o cartão: sai do post, e quem o quiser lê-o da fonte com
tokenURI(tokenId). Trocamos uma linha copiada de nós por uma leitura
on-chain — mais verificável, não menos.

O tx vai como hash puro, sem domínio, e por isso nunca disputou nada.
"""
from __future__ import annotations

import re

from finding_memeland.target.templates import (
    TargetWinnerData,
    commitment_block,
    item_link_for,
    target_winner_announcement,
)

C = "0xb932a70a57673d89f4acffbe830e8ed7f75fb9e0"
TARGET = f"ethereum:{C}:11385"

# O que o X liga: um URL com esquema, OU um domínio nu seguido de barra.
# O segundo é o que me apanhou a mim primeiro — partir o "https://" não
# chega, porque "gateway.exemplo.com/ipfs/Qm…" continua a virar link.
_LINKY = re.compile(
    r"https?://\S+"
    r"|(?<![\w.@])(?:[a-z0-9][a-z0-9-]*\.)+[a-z]{2,}/\S*",
    re.IGNORECASE)


def _links(text: str) -> list[str]:
    return _LINKY.findall(text)


def _data(**kw) -> TargetWinnerData:
    base = dict(
        hunt_n=11, winner_handle="@lovekillsduty", time_to_win="2h14m",
        prize_amount="100,000,000", tx_link="0x" + "ab" * 32,
        target_id=TARGET, target_name_onchain="Aeth Hopper",
        metadata_sha256="de" * 32, salt="ff" * 16,
        token_uri="ipfs://QmAethHopperMetadataCid",
        item_link=item_link_for(TARGET), artist="Metageist",
    )
    base.update(kw)
    return TargetWinnerData(**base)


def test_the_item_page_is_the_only_link_when_the_tokenuri_is_https():
    """O caso do hunt #11."""
    post = target_winner_announcement(
        _data(token_uri="https://gateway.pinata.cloud/ipfs/QmAeth/11385"))
    found = _links(post)
    assert len(found) == 1, found
    assert found[0].startswith("opensea.io/item/")


def test_an_ipfs_tokenuri_still_prints_in_full():
    """O ipfs:// não é ligado pelo X, por isso não custa nada e fica —
    perder informação que não estorva seria pagar duas vezes."""
    post = target_winner_announcement(_data())
    assert "ipfs://QmAethHopperMetadataCid" in post
    assert len(_links(post)) == 1


def test_an_https_tokenuri_says_where_to_read_it_instead_of_vanishing():
    """Sair do post não é desaparecer sem explicação: quem verifica tem de
    saber que a linha existiu e como a obter."""
    block = commitment_block(
        target_id=TARGET, metadata_sha256="de" * 32, salt="ff" * 16,
        token_uri="https://gateway.pinata.cloud/ipfs/QmAeth/11385")
    assert "tokenURI(tokenId)" in block
    assert "not part of the commitment" in block
    assert not _links(block)


def test_the_commitment_still_recomputes_without_the_tokenuri():
    """O que nunca pode sair: os três ingredientes e a fórmula."""
    block = commitment_block(target_id=TARGET, metadata_sha256="de" * 32,
                             salt="ff" * 16,
                             token_uri="https://gw.example.com/x.json")
    assert C in block
    assert "11385" in block
    assert "de" * 32 in block
    assert "ff" * 16 in block
    assert "SHA-256" in block


def test_the_mutation_note_does_not_smuggle_a_second_link():
    """O caminho raro que ninguém olha até ao dia em que acontece."""
    post = target_winner_announcement(_data(
        mutated_after_claim=True, live_hash_status="resolved",
        live_metadata_sha256="ab" * 32,
        live_token_uri="https://gateway.pinata.cloud/ipfs/QmOutro"))
    found = _links(post)
    assert len(found) == 1, found
    assert found[0].startswith("opensea.io/item/")
    assert "tokenURI(tokenId)" in post


def test_the_tx_goes_as_a_bare_hash_and_never_as_a_domain():
    post = target_winner_announcement(_data())
    assert "0x" + "ab" * 32 in post
    assert "basescan" not in post
