"""Depois da hunt #27 — pedido do Pedro a 29/10, D1 e D2 decididas a 30/10.

LINKS DE COIN DO ZORA: zora.co/coin/<cadeia>:0x… é um ERC-20 — um endereço sem
tokenId — e recebia a resposta de colecção ("that's the collection, not the
token — one piece short"). Uma coin não tem peças. Passa a ter resposta
própria, sem gastar tentativa.

  D1 — o texto não diz "zora": é também o nome de uma cadeia que lemos, e
       nenhuma resposta pública escreve uma (regra de 09/10). O jogador sabe
       de onde tirou o link.
  D2 — só o Zora, por agora. Dexscreener, clanker e uniswap também são coins
       e ficam como estavam (resposta de colecção); o basescan /token/ serve
       coins E colecções de NFT, e o link sozinho não diz qual é.

Medido antes de mexer (links inventados, sem rede): todas as formas de link de
coin do Zora recebiam a resposta de colecção, nenhuma gastava tentativa e o
resolvedor de links nem era chamado.
"""
from __future__ import annotations

import re

import pytest
from test_format_replies import (
    COLLECTION,
    GOOD,
    READ_CHAINS,
    TYPO,
    _matcher,
    _public_replies,
    _restart,
    _row,
    _run,
    _thread,
)

from finding_memeland.claims.matcher import format_type_of
from finding_memeland.orchestrator.state_machine import Orchestrator
from finding_memeland.target.adapters import MarketplaceLinkResolver
from finding_memeland.target.claim import OTHER_CHAIN_WORDS, coin_link
from finding_memeland.target.dryrun import TargetWorld, replies_to
from finding_memeland.target.integration import claim_matcher_for
from finding_memeland.target.templates import (
    CLAIM_FORMAT_EXAMPLE,
    POST_REPLY_COIN_LINK,
    POST_REPLY_COLLECTION_LINK,
    POST_REPLY_ONE_TOKEN,
    POST_REPLY_UNKNOWN_CHAIN,
    POST_REPLY_UNRESOLVED_LINK,
)

OTHER = "0x" + "9f8e7d6c5b" * 4                       # a second address (a referrer)

COINS = [
    f"https://zora.co/coin/base:{GOOD}",
    f"https://zora.co/coin/base:{GOOD}?referrer={OTHER}",     # how a share link comes
    f"https://zora.co/coin/zora:{GOOD}",
    f"https://www.zora.co/coin/base:{GOOD}",
    f"https://zora.co/coin/base:{GOOD}/",
    f"https://Zora.co/Coin/BASE:{GOOD}",
    f"http://zora.co/coin/base:{GOOD}",
]

# D2 (Pedro, 30/10): coins on other sites stay as they were
OTHER_COIN_SITES = [
    f"https://dexscreener.com/base/{GOOD}",
    f"https://clanker.world/clanker/{GOOD}",
    f"https://app.uniswap.org/explore/tokens/base/{GOOD}",
    f"https://basescan.org/token/{GOOD}",            # coins AND nft collections
]


def _wired(resolve=None, **kw):
    """The matcher as production builds it (integration.claim_matcher_for)."""
    args = dict(coin_reply=POST_REPLY_COIN_LINK,
                unknown_chain_reply=POST_REPLY_UNKNOWN_CHAIN,
                larder_chains=frozenset({"ethereum", "base"}))
    args.update(kw)
    return _matcher(resolve, **args)


# --------------------------------------------------------------------------- #
# What a coin link is                                                          #
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("url", COINS)
def test_a_zora_coin_page_is_a_coin(url):
    assert coin_link(url)


@pytest.mark.parametrize("url", [
    f"https://zora.co/collect/base:{GOOD}",          # a collection: a piece IS one short
    f"https://zora.co/collect/base:{GOOD}/5",        # a token
    "https://zora.co/@someone",                      # a profile
    "https://zora.co/coin/",                         # no coin named
    f"https://notzora.co/coin/base:{GOOD}",
    f"https://zora.co.example.com/coin/base:{GOOD}",
    f"https://example.com/coin/base:{GOOD}",
    *OTHER_COIN_SITES,
    "",
])
def test_everything_else_is_not(url):
    assert not coin_link(url)


# --------------------------------------------------------------------------- #
# The reply                                                                    #
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("url", COINS)
def test_a_coin_gets_its_own_reply_and_is_no_guess(url):
    m = _wired()
    for text in (url, f"found it {url} \U0001F438"):
        assert m.looks_like_claim(text) is False, text
        assert m.format_kind(text) == "coin_link", text
        assert m.format_hint(text) == POST_REPLY_COIN_LINK, text


def test_the_link_resolver_is_never_asked_about_a_coin():
    asked = []
    resolver = MarketplaceLinkResolver(
        chain_probe=lambda addr, tid: asked.append(tid), page_resolvers={})
    m = _wired(resolver)
    for url in COINS:
        assert m.format_kind(url) == "coin_link"
    assert asked == []


@pytest.mark.parametrize("url", COINS)
def test_what_these_links_used_to_hear(url):
    """Medido antes de mexer, e o que fica sem a resposta ligada."""
    m = _matcher(unknown_chain_reply=POST_REPLY_UNKNOWN_CHAIN,
                 larder_chains=frozenset({"ethereum", "base"}))
    assert m.format_kind(url) == "collection_link"
    assert m.format_hint(url) == POST_REPLY_COLLECTION_LINK


@pytest.mark.parametrize("text, kind", [
    (f"https://zora.co/collect/base:{GOOD}", "collection_link"),
    ("https://zora.co/@someone", "unreadable_link"),
    *((url, "collection_link") for url in OTHER_COIN_SITES),           # D2
    (COLLECTION, "collection_link"),
])
def test_every_other_link_keeps_its_reply(text, kind):
    assert _wired().format_kind(text) == kind


@pytest.mark.parametrize("url", [
    f"https://zora.co/collect/base:{GOOD}/5",
    f"https://zora.co/coin/base:{GOOD}/5",           # not a Zora shape: read as a token
])
def test_a_link_that_reads_as_a_token_is_never_touched(url):
    """A regra só olha para links que não se conseguem ler: o que já se lê
    como chain:contract:tokenId é julgado, como antes."""
    m = _wired()
    assert m.looks_like_claim(url) is True
    assert m.format_kind(url) is None


def test_the_rules_before_it_keep_their_place():
    m = _wired()
    # one token per reply, as before: a coin is still "a link" in the post
    assert m.format_hint(f"{COINS[0]} {COINS[2]}") == POST_REPLY_ONE_TOKEN
    assert m.format_hint(f"base:{OTHER}:5 {COINS[0]}") == POST_REPLY_ONE_TOKEN
    # an address that is not one comes first: until it is fixed nothing reads
    assert m.format_kind(f"https://zora.co/coin/base:{TYPO}") == "bad_address"


def test_the_reply_is_the_approved_text_word_for_word():
    """Pedro, 30/10 (D1: sem "zora")."""
    assert POST_REPLY_COIN_LINK == (
        "that's a coin, not an nft — the treasure is an nft. paste the piece's "
        "link, or reply with chain:contract:tokenId (like <chain>:0xabc…def:42). "
        "this one cost you nothing.")
    assert CLAIM_FORMAT_EXAMPLE in POST_REPLY_COIN_LINK
    assert len(POST_REPLY_COIN_LINK) <= 280


def test_the_reply_names_no_chain_and_the_chain_rule_checks_it():
    words = set(re.findall(r"[a-z0-9]+", POST_REPLY_COIN_LINK.replace(
        CLAIM_FORMAT_EXAMPLE, " ").lower()))
    assert "zora" not in words
    assert not (words & READ_CHAINS) and not (words & OTHER_CHAIN_WORDS)
    # the test of every public reply (rule of 09/10) walks this one too
    assert _public_replies()["POST_REPLY_COIN_LINK"] == POST_REPLY_COIN_LINK


def test_it_is_a_type_of_its_own():
    assert format_type_of("coin_link") == "coin_link"
    assert Orchestrator._format_key("coin_link") == "format:coin_link"
    assert format_type_of("coin_link") != format_type_of("collection_link")
    assert POST_REPLY_COIN_LINK not in (POST_REPLY_COLLECTION_LINK,
                                        POST_REPLY_UNRESOLVED_LINK)


def test_production_wires_the_coin_reply():
    w = TargetWorld()
    hunt = w.launch()
    m = claim_matcher_for(w.orch, hunt)
    assert m.format_hint(COINS[1]) == POST_REPLY_COIN_LINK
    assert m.format_hint(COLLECTION) == POST_REPLY_COLLECTION_LINK


# --------------------------------------------------------------------------- #
# In the thread                                                                #
# --------------------------------------------------------------------------- #


def test_in_the_thread_a_coin_is_answered_logged_as_format_and_costs_no_guess():
    w = TargetWorld()
    hunt = w.launch()
    _thread(w, hunt, (9001, "71", COINS[0]), (9002, "72", f"look: {COINS[1]}"))
    _run(w, hunt)
    for tid in (9001, 9002):
        assert replies_to(w.rig, tid) == [POST_REPLY_COIN_LINK]
        row = _row(w, tid)
        assert (row["outcome"], row["submitted_claim_code"]) == ("format", "coin_link")
        assert GOOD not in repr(row)
    assert w.orch._rebuild_claim_state(hunt)[1] == {}        # nobody spent a guess


def test_a_collection_and_then_a_coin_are_both_answered():
    """Um tipo novo: quem já ouviu a resposta de colecção ainda ouve esta."""
    w = TargetWorld()
    hunt = w.launch()
    _thread(w, hunt, (9001, "71", COLLECTION), (9002, "71", COINS[0]))
    _run(w, hunt)
    assert replies_to(w.rig, 9001) == [POST_REPLY_COLLECTION_LINK]
    assert replies_to(w.rig, 9002) == [POST_REPLY_COIN_LINK]


def test_the_same_coin_twice_is_answered_once():
    """Uma resposta por tipo por perfil, como as outras (a #27: o jogador
    repetiu o link e a repetição ficou sem resposta — de propósito)."""
    w = TargetWorld()
    hunt = w.launch()
    _thread(w, hunt, (9001, "71", COINS[0]), (9002, "71", COINS[1]))
    _run(w, hunt)
    assert replies_to(w.rig, 9001) == [POST_REPLY_COIN_LINK]
    assert replies_to(w.rig, 9002) == []
    assert [_row(w, t)["submitted_claim_code"] for t in (9001, 9002)] == ["coin_link"] * 2


def test_the_ceiling_of_three_holds_with_the_new_type():
    w = TargetWorld()
    hunt = w.launch()
    _thread(w, hunt, (9001, "71", COLLECTION), (9002, "71", GOOD),
            (9003, "71", TYPO), (9004, "71", COINS[0]))
    _run(w, hunt)
    assert [len(replies_to(w.rig, t)) for t in (9001, 9002, 9003, 9004)] == [1, 1, 1, 0]
    assert _row(w, 9004)["submitted_claim_code"] == "coin_link"     # logged all the same


def test_after_a_restart_a_coin_is_not_answered_again():
    w = TargetWorld()
    hunt = w.launch()
    _thread(w, hunt, (9001, "71", COINS[0]))
    _run(w, hunt)
    rebuilt = _restart(w, hunt)
    assert w.orch._rebuild_claim_state(rebuilt)[4]["format:coin_link"] == {"71"}
    _thread(w, rebuilt, (9010, "71", COINS[2]), (9011, "72", COINS[2]))
    _run(w, rebuilt)
    assert replies_to(w.rig, 9010) == []
    assert replies_to(w.rig, 9011) == [POST_REPLY_COIN_LINK]          # somebody else
