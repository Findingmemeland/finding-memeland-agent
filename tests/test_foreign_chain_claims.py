"""Um claim numa cadeia que a despensa nunca produz é um palpite errado.

14/10, depois da #20. Um jogador mandou "solana:5uf5…:1" e ouviu "i can't read
that one — my side, not yours, and it cost you nothing" — como se o claim
pudesse estar certo e fôssemos nós a não o ler. Não podia: o tesouro nunca
está em Solana. Decisões do Pedro:

  a) um claim numa cadeia que o jogo nunca usa — Solana, Tezos, Bitcoin /
     Ordinals, e também as cadeias 0x que conhecemos e o jogo nunca lê (bsc,
     avalanche, linea…) — é um palpite errado normal: a resposta de errado,
     e conta como tentativa. Sem dizer que não suportamos essa cadeia: o jogo
     diz "anywhere onchain";
  b) um nome de cadeia que não reconhecemos (pode ser uma das nossas escrita
     de outra forma) continua a ter resposta de formato, sem gastar tentativa;
  c) o exemplo do formato deixa de ter cadeia: "<chain>:0xabc…def:42".

E, na mensagem dele: "A lista das cadeias 'nunca usadas' deve sair das cadeias
que a despensa pode produzir, não de uma lista fixa, para que uma cadeia nova
nunca dê 'errado' a um claim certo." Por isso "nunca usada" = uma cadeia que
o jogo CONHECE e que NÃO está entre as que a despensa pode produzir — e,
enquanto essas não se souberem, nada se dá por errado.
"""
from __future__ import annotations

import pytest
from test_format_replies import (
    GOOD,
    INSCRIPTION,
    KT1,
    MINT,
    OTHER,
    _matcher,
    _row,
    _run,
    _thread,
)

from finding_memeland.claims.matcher import TargetClaimMatcher
from finding_memeland.target.claim import (
    OTHER_CHAIN_CANON,
    OTHER_CHAIN_WORDS,
    chain_name,
    foreign_chain,
    unknown_chain_word,
)
from finding_memeland.target.dryrun import TargetWorld as World
from finding_memeland.target.dryrun import replies_to
from finding_memeland.target.integration import claim_matcher_for
from finding_memeland.target.templates import (
    POST_REPLY_OTHER_CHAIN,
    POST_REPLY_UNKNOWN_CHAIN,
)

OURS = frozenset({"ethereum", "base"})

# each post of test_format_replies.OTHER, with the chain it names
NAMED = [
    (f"bsc:{GOOD}:12", "bsc"), (f"avalanche:{GOOD}:12", "avalanche"),
    (f"BSC: {GOOD} : 12", "bsc"),
    (f"tezos:{KT1}:123456", "tezos"), (f"{KT1} 123456", "tezos"), (f"xtz:{KT1}", "tezos"),
    (f"solana:{MINT}", "solana"), (f"sol: {MINT}", "solana"),
    (f"btc:{INSCRIPTION}", "bitcoin"), (f"ordinals: {INSCRIPTION}", "bitcoin"),
    (f"https://objkt.com/tokens/{KT1}/123456", "tezos"),
    (f"https://magiceden.io/item-details/{MINT}", "solana"),
    (f"https://opensea.io/assets/solana/{MINT}", "solana"),
    ("https://tensor.trade/item/whatever", "solana"),
]


def matcher(larder=OURS, **kw) -> TargetClaimMatcher:
    return _matcher(unknown_chain_reply=POST_REPLY_UNKNOWN_CHAIN,
                    larder_chains=frozenset(larder), **kw)


# --------------------------------------------------------------------------- #
# 1. Que cadeia o post nomeia                                                   #
# --------------------------------------------------------------------------- #


def test_every_post_of_the_old_rule_is_named_by_its_chain():
    assert sorted(t for t, _c in NAMED) == sorted(OTHER)          # the same posts


@pytest.mark.parametrize("text, chain", NAMED)
def test_the_post_names_one_chain_by_one_name(text, chain):
    assert foreign_chain(text) == chain


@pytest.mark.parametrize("text", [
    f"ethereum:{GOOD}:12", f"base:{GOOD}:12", f"{GOOD}:12", MINT,
    "bitcoin: still king", "btc is the answer", "gm solana frens", "sol: no",
    f"arb:{GOOD}:12", f"https://magiceden.io/item-details/ethereum/{GOOD}/5",
])
def test_ours_chatter_and_unknown_names_name_no_foreign_chain(text):
    assert foreign_chain(text) is None


def test_one_name_per_chain():
    assert chain_name("ETH") == "ethereum" and chain_name("mainnet") == "ethereum"
    assert chain_name("bnb") == chain_name("binance") == chain_name("bsc") == "bsc"
    assert chain_name("xtz") == "tezos" and chain_name("ordinals") == "bitcoin"
    assert chain_name("apechain") == "apechain"             # its own name
    assert set(OTHER_CHAIN_CANON) <= OTHER_CHAIN_WORDS        # aliases of known words only


@pytest.mark.parametrize("text, unknown", [
    (f"arb:{GOOD}:12", True), (f"op:{GOOD}:1", True), (f"basechain:{GOOD}:7", True),
    (f"answer:{GOOD}:12", True),
    (f"ethereum:{GOOD}:12", False), (f"Base: {GOOD} : 3", False),
    (f"bsc:{GOOD}:12", False),                                # known, not ours
    (f"{GOOD}:12", False), ("arb is the answer", False),
])
def test_an_unknown_chain_name_is_told_apart(text, unknown):
    assert unknown_chain_word(text) is unknown


# --------------------------------------------------------------------------- #
# 2. Uma cadeia que a despensa nunca produz: um palpite                         #
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("text, chain", NAMED)
def test_a_claim_where_the_treasure_can_never_be_is_a_guess_and_wrong(text, chain):
    m = matcher()
    assert m.looks_like_claim(text) is True                  # it opens the guess path
    assert m.matches(text) is False
    assert m.submitted_label(text) == f"outra-cadeia:{chain}"
    assert m.spray_key(text) is None                         # never a detector key
    assert m.format_hint(text) is None                       # not a format slip


def test_a_wrong_guess_is_never_resolved_on_the_network():
    """O tesouro nunca lá está: não há nada para ler, e um link do objkt
    não vai ao resolvedor de mercados."""
    asked: list[str] = []

    def resolve(url):
        asked.append(url)
        return None

    m = matcher(resolve=resolve)
    assert m.matches(f"https://objkt.com/tokens/{KT1}/1") is False and asked == []


def test_our_own_chains_are_judged_as_they_always_were():
    m = matcher()
    right = "ethereum:0x" + "ab" * 20 + ":1"                 # the sealed id of `_matcher`
    assert m.looks_like_claim(right) and m.matches(right)
    wrong = f"base:{GOOD}:12"
    assert m.looks_like_claim(wrong) and not m.matches(wrong)
    assert m.submitted_label(wrong) == f"base:{GOOD.lower()}:12"


def test_a_chain_the_larder_can_produce_is_never_told_wrong():
    """A regra do Pedro: a lista sai das cadeias da despensa. Uma cadeia
    nova na despensa, que o leitor de claims ainda não lê, fica com a
    resposta de "não consigo ler" — nossa, nunca dele."""
    m = matcher(larder=OURS | {"bsc", "tezos"})
    for text in (f"bsc:{GOOD}:12", f"tezos:{KT1}:5", f"https://objkt.com/tokens/{KT1}/1"):
        assert m.looks_like_claim(text) is False
        assert m.format_hint(text) == POST_REPLY_OTHER_CHAIN
    assert m.looks_like_claim(f"solana:{MINT}") is True      # still never produced


def test_the_larder_s_chains_are_compared_by_their_one_name():
    m = matcher(larder=OURS | {"binance"})                   # bsc, by another name
    assert m.looks_like_claim(f"bnb:{GOOD}:1") is False
    assert m.format_hint(f"bnb:{GOOD}:1") == POST_REPLY_OTHER_CHAIN


def test_while_the_larder_s_chains_are_not_known_nothing_is_called_wrong():
    m = matcher(larder=frozenset())
    for text, _chain in NAMED:
        assert m.looks_like_claim(text) is False
        assert m.format_hint(text) == POST_REPLY_OTHER_CHAIN


def test_chatter_naming_a_chain_is_still_chatter():
    m = matcher()
    for text in ("bitcoin: still king", "btc is the answer", "gm solana frens", "sol: no"):
        assert m.looks_like_claim(text) is False and m.format_hint(text) is None


# --------------------------------------------------------------------------- #
# 3. Um nome de cadeia desconhecido: formato, sem gastar                        #
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("text", [f"arb:{GOOD}:12", f"op:{GOOD}:1", f"answer:{GOOD}:12"])
def test_an_unknown_chain_name_gets_its_own_format_reply(text):
    m = matcher()
    assert m.looks_like_claim(text) is False
    assert m.format_kind(text) == "unknown_chain"
    assert m.format_hint(text) == POST_REPLY_UNKNOWN_CHAIN


def test_without_the_reply_wired_it_goes_where_it_went():
    old = _matcher()
    assert old.format_kind(f"arb:{GOOD}:12") == "no_token_id"


# --------------------------------------------------------------------------- #
# 4. Na hunt: uma tentativa gasta, a resposta de errado, nada de spray          #
# --------------------------------------------------------------------------- #


def test_in_the_thread_it_is_a_wrong_guess_that_counts():
    w = World()
    hunt = w.launch()
    _thread(w, hunt, (6001, "42", f"tezos:{KT1}:123456"), (6002, "43", f"bsc:{GOOD}:12"),
            (6003, "44", f"solana:{MINT}"))
    _run(w, hunt)
    for tid, chain in ((6001, "tezos"), (6002, "bsc"), (6003, "solana")):
        row = _row(w, tid)
        assert (row["outcome"], row["submitted_claim_code"]) == ("bad_code",
                                                                 f"outra-cadeia:{chain}")
        assert KT1 not in repr(row) and GOOD not in repr(row) and MINT not in repr(row)
        (reply,) = replies_to(w.rig, tid)
        assert reply.endswith("four left.")                  # an attempt was spent
        assert POST_REPLY_OTHER_CHAIN not in reply
        for word in ("tezos", "bsc", "solana", "support", "can't read"):
            assert word not in reply.lower(), word
    state = w.orch._rebuild_claim_state(hunt)
    assert state[1] == {"42": 1, "43": 1, "44": 1}            # one guess each, after a restart too


def test_a_wrong_guess_on_another_chain_never_feeds_the_spray_detector():
    from finding_memeland.target.integration import rebuild_spray
    w = World()
    hunt = w.launch()
    _thread(w, hunt, *[(6100 + n, str(90 + n), f"solana:{MINT}") for n in range(8)])
    _run(w, hunt)
    log, _state = rebuild_spray(w.orch, hunt)
    assert log == []
    assert [_row(w, 6100 + n)["outcome"] for n in range(8)] == ["bad_code"] * 8


def test_the_guess_cap_counts_them_like_any_other_guess():
    w = World()
    hunt = w.launch()
    cap = w.orch._claim_guess_cap                             # noqa: SLF001
    _thread(w, hunt, *[(6200 + n, "42", f"tezos:{KT1}:{n + 1}") for n in range(cap + 1)])
    _run(w, hunt)
    outcomes = [_row(w, 6200 + n)["outcome"] for n in range(cap + 1)]
    assert outcomes == ["bad_code"] * cap + ["spam_capped"]


def test_production_and_the_dry_run_derive_the_chains_and_add_the_target_s():
    w = World()
    hunt = w.launch()
    m = claim_matcher_for(w.orch, hunt)
    target_chain = hunt.target.id().split(":", 1)[0]
    assert m._larder_chains == {"ethereum", "base", target_chain}   # noqa: SLF001
    # the sealed target's chain is one of them even if the ports forgot it
    w.orch._target.larder_chains = frozenset({"base"})               # noqa: SLF001
    assert claim_matcher_for(w.orch, hunt)._larder_chains == {"base", target_chain}  # noqa: SLF001
    w.orch._target.larder_chains = frozenset()                       # noqa: SLF001
    assert claim_matcher_for(w.orch, hunt)._larder_chains == frozenset()   # noqa: SLF001


def test_the_wiring_derives_the_set_from_what_the_larder_draws_and_harvests():
    from test_creator_harvest import _built

    from finding_memeland.target.wiring import _chains_we_read
    w = _built(target_arweave_gateways="https://ar-um.example/,https://ar-dois.example/")
    expected = (_chains_we_read() | w.deposit_chains | frozenset(w.harvesters)
                | frozenset(h.chain for h in w.creator_harvesters.values()))
    assert w.ports.larder_chains == expected
    assert {"ethereum", "base"} <= w.ports.larder_chains
    assert not (w.ports.larder_chains & {"solana", "tezos", "bitcoin", "bsc"})
