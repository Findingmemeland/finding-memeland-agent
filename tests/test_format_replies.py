"""As respostas de formato, depois da hunt #17 (09/10) — três decisões do Pedro.

  10. QUASE-ENDEREÇOS. `0x` + 40 caracteres com um que não é hex (um "S" no
      lugar de um "5"), ou com um carácter a mais ou a menos, não era claim
      nem formato: ia ao juiz de humor e levava "a name is not a claim" — e
      com 39 ou 41 caracteres levava "falta-te cadeia, contrato E tokenId" a
      quem tinha mandado os três. Resposta própria, que diz qual é o
      carácter, sem gastar palpite.
  11. UMA RESPOSTA POR TIPO, com tecto de três por perfil por hunt. Era uma
      por perfil para todos os tipos: o segundo engano, diferente do
      primeiro, ficava sem resposta.
  12. OUTRAS CADEIAS. Uma cadeia que não lemos, um id de Tezos / Solana /
      Bitcoin, um link de um mercado dessas cadeias: uma resposta só — e
      que NÃO lista cadeias. A cadeia faz parte da resposta, e uma resposta
      pública do oráculo nunca a pode estreitar.

Nada aqui diz a um jogador que está errado: não se julga o que não se
conseguiu ler.
"""
from __future__ import annotations

import inspect
import re
from datetime import timedelta

import pytest

import finding_memeland.content.templates as content_templates
import finding_memeland.target.templates as target_templates
from finding_memeland.claims.matcher import TargetClaimMatcher, format_type_of
from finding_memeland.orchestrator.state_machine import HuntState, Orchestrator
from finding_memeland.target.claim import (
    CHAIN_ALIASES,
    OTHER_CHAIN_WORDS,
    ClaimJudge,
    NearAddress,
    near_address,
    non_evm_link,
    other_chain,
)
from finding_memeland.target.dryrun import (
    FakeClaimSource,
    post,
    replies_to,
)
from finding_memeland.target.dryrun import (
    TargetWorld as World,
)
from finding_memeland.target.integration import claim_matcher_for
from finding_memeland.target.templates import (
    CLAIM_FORMAT_EXAMPLE,
    POST_REPLY_COLLECTION_LINK,
    POST_REPLY_FORMAT,
    POST_REPLY_ONE_TOKEN,
    POST_REPLY_OTHER_CHAIN,
    POST_REPLY_UNRESOLVED_LINK,
    post_reply_bad_address,
)

GOOD = "0x" + "1a2b3c4d5e" * 4                         # an address
TYPO = "0x" + "1a2b3c4d5e" * 3 + "1a2b3c4dSe"          # an "S" where a "5" was
SHORT = GOOD[:-1]                                      # 39 after the 0x
LONG = GOOD + "f"                                      # 41
KT1 = "KT1RJ6PbjHpwc3M5rw5s2Nbmefwbuwbdxton"
MINT = "7xKXtg2CW87d97TXJSDpbD5jBkheTqA83TZRuJosgAsU"
INSCRIPTION = "6fb976ab49dcec017f1e201e84395983204ae1a7c2abf7ced0a85d692e442799i0"
COLLECTION = f"https://rarible.com/base/collections/{GOOD}"
SLUG_LINK = "https://foundation.app/@someone/a-piece"
TWO_LINKS = (f"https://opensea.io/assets/ethereum/{GOOD}/1 "
             f"https://opensea.io/assets/ethereum/{GOOD}/2")


def _matcher(resolve=None, **kw) -> TargetClaimMatcher:
    """The matcher as production wires it (integration.claim_matcher_for)."""
    args = dict(
        judge=ClaimJudge(target_id="ethereum:0x" + "ab" * 20 + ":1"),
        resolve_link=resolve, format_reply=POST_REPLY_FORMAT,
        unresolved_reply=POST_REPLY_UNRESOLVED_LINK,
        one_token_reply=POST_REPLY_ONE_TOKEN,
        collection_reply=POST_REPLY_COLLECTION_LINK,
        bad_address_reply=post_reply_bad_address,
        other_chain_reply=POST_REPLY_OTHER_CHAIN)
    args.update(kw)
    return TargetClaimMatcher(**args)


def _run(w: World, hunt, rounds: int = 3) -> None:
    w.orch._max_rounds = rounds
    with pytest.raises(RuntimeError):
        w.orch._claim_loop(hunt)


def _restart(w: World, hunt):
    seen = list(w.src._delivered)
    w.src = FakeClaimSource()
    w.src._delivered = seen
    w.orch._claim_source = w.src
    return w.orch._rebuild_hunt(w.rig.repo.hunts[hunt.id], HuntState.LIVE)


def _row(w: World, tid) -> dict:
    (row,) = [s for s in w.rig.repo.submissions if s.get("dm_id") == str(tid)]
    return row


def _thread(w: World, hunt, *posts) -> None:
    """Replies to Clue 1, a minute apart, delivered on the next poll."""
    t0 = hunt.live_at
    batch = [post(tid, author, text, t0 + timedelta(minutes=n + 1), hunt.reshare_post_id)
             for n, (tid, author, text) in enumerate(posts)]
    w.src.schedule[w.src.polls + 1] = lambda: batch


# --------------------------------------------------------------------------- #
# 10. Quase-endereços                                                           #
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("text, expected", [
    (f"ethereum:{TYPO}:123", NearAddress(("S",), 40)),        # the hunt #17 slip
    (f"{TYPO}:123", NearAddress(("S",), 40)),
    (TYPO, NearAddress(("S",), 40)),
    (f"it is {TYPO.upper().replace('0X', '0x')}, surely", NearAddress(("S",), 40)),
    (f"ethereum:{SHORT}:123", NearAddress((), 39)),
    (f"ethereum:{LONG}:123", NearAddress((), 41)),
    (GOOD[:-2], NearAddress((), 38)),
    (GOOD + "ab", NearAddress((), 42)),
    ("0x" + "g" * 20 + "Z" * 19 + "g", NearAddress(("g", "Z"), 40)),   # once each, in order
    (f"https://opensea.io/item/ethereum/{TYPO}/123", NearAddress(("S",), 40)),
])
def test_an_address_that_is_not_one_is_recognised(text, expected):
    assert near_address(text) == expected


@pytest.mark.parametrize("text", [
    f"ethereum:{GOOD}:123", GOOD, f"{GOOD}:7", GOOD.upper().replace("0X", "0x"),
    "0x" + "ab" * 32,                       # a transaction hash
    GOOD[:-3], GOOD + "abc",                # too far from 40 to be a slip
    "gm", "", "0x", "it costs 0x10",
    f"x{GOOD[1:]}S",                        # no 0x
])
def test_what_is_an_address_or_nothing_like_one_is_left_alone(text):
    assert near_address(text) is None


def test_the_two_replies_are_the_approved_ones_word_for_word():
    assert post_reply_bad_address(("S",), 40) == (
        "that address has a character that isn't hex — the 'S'. an address is "
        "0x + 40 of 0-9 and a-f. check it against the source and send it again. "
        "this one cost you nothing.")
    assert post_reply_bad_address((), 39) == (
        "that address is 39 characters after 0x — it takes 40. check it and "
        "send it again. this one cost you nothing.")


@pytest.mark.parametrize("bad, length, says, never", [
    (("S",), 40, ["the 'S'"], ["characters after"]),
    (("S", "z"), 40, ["'S', 'z'", "characters that aren't hex"], ["and more"]),
    (tuple("ghij"), 40, ["'g', 'h', 'i' and more"], ["'j'"]),
    ((), 41, ["is 41 characters after 0x", "it takes 40"], ["isn't hex"]),
    (("S",), 39, ["the 'S'", "39 characters after 0x"], []),
])
def test_the_reply_says_what_is_wrong_with_the_address_and_nothing_else(
        bad, length, says, never):
    text = post_reply_bad_address(bad, length)
    assert all(s in text for s in says) and not any(n in text for n in never)
    assert "cost you nothing" in text and len(text) <= 280
    # never a verdict, never the address back, never a chain
    assert not re.search(r"\b(wrong|incorrect|not it|nope)\b", text, re.I)
    assert "0x1a" not in text


@pytest.mark.parametrize("text", [
    f"ethereum:{TYPO}:123", f"{TYPO}:123", TYPO,
    f"ethereum:{SHORT}:123", f"ethereum:{LONG}:123", SHORT,
    f"https://opensea.io/item/ethereum/{TYPO}/123",      # in a link we cannot read
])
def test_the_matcher_answers_a_near_address_and_spends_nothing(text):
    m = _matcher()
    assert m.looks_like_claim(text) is False             # never a guess
    assert m.format_kind(text) == "bad_address"
    near = near_address(text)
    assert m.format_hint(text) == post_reply_bad_address(near.bad_chars, near.length)


def test_a_short_address_is_no_longer_told_it_lacks_a_token_id():
    """Medido antes: 39 ou 41 caracteres davam "a claim needs… chain,
    contract AND tokenId" a quem tinha mandado os três."""
    old = _matcher(bad_address_reply=None)
    new = _matcher()
    for text in (f"ethereum:{SHORT}:123", f"ethereum:{LONG}:123"):
        assert old.format_hint(text) == POST_REPLY_FORMAT
        assert new.format_hint(text) != POST_REPLY_FORMAT
        assert "characters after 0x" in new.format_hint(text)


def test_a_post_that_also_names_a_real_token_is_a_claim_like_any_other():
    m = _matcher()
    text = f"not {TYPO}, i meant ethereum:{GOOD}:5"
    assert m.looks_like_claim(text) is True and m.format_hint(text) is None


def test_without_the_reply_wired_a_typo_goes_where_it_went():
    old = _matcher(bad_address_reply=None)
    assert old.format_hint(f"ethereum:{TYPO}:123") is None   # the humour judge's


def test_in_the_thread_a_typo_gets_its_reply_a_row_and_no_guess():
    w = World()
    hunt = w.launch()
    _thread(w, hunt, (6001, "42", f"ethereum:{TYPO}:123"))
    _run(w, hunt)
    assert replies_to(w.rig, 6001) == [post_reply_bad_address(("S",), 40)]
    row = _row(w, 6001)
    assert row["outcome"] == "format" and row["submitted_claim_code"] == "bad_address"
    assert TYPO not in repr(row) and "1a2b" not in repr(row)
    state = w.orch._rebuild_claim_state(hunt)
    assert state[1] == {} and state[3] == set()          # no guess, no jeer spent


def test_production_wires_both_new_replies():
    w = World()
    hunt = w.launch()
    m = claim_matcher_for(w.orch, hunt)
    assert m.format_kind(TYPO) == "bad_address"
    assert m.format_hint(f"tezos:{KT1}:5") == POST_REPLY_OTHER_CHAIN


# --------------------------------------------------------------------------- #
# 11. Uma resposta por tipo, três por perfil                                    #
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("kind, key", [
    ("no_chain", "missing"), ("no_token_id", "missing"),     # the same words
    ("collection_link", "collection_link"), ("unreadable_link", "unreadable_link"),
    ("one_token", "one_token"), ("bad_address", "bad_address"),
    ("other_chain", "other_chain"), (None, "format"), ("", "format"),
])
def test_the_type_is_the_reply_the_player_reads(kind, key):
    assert format_type_of(kind) == key
    assert Orchestrator._format_key(kind) == "format:" + key


def test_two_kinds_share_a_type_only_when_they_share_the_words():
    m = _matcher()
    by_type: dict[str, set[str]] = {}
    for text in (f"{GOOD}:7", GOOD, COLLECTION, SLUG_LINK, TWO_LINKS, TYPO,
                 f"tezos:{KT1}:5"):
        by_type.setdefault(format_type_of(m.format_kind(text)), set()).add(
            # the bad-address text is built from the post: its type is its own
            "bad address" if m.format_kind(text) == "bad_address" else m.format_hint(text))
    assert len(by_type) == 6 and all(len(v) == 1 for v in by_type.values())


def test_a_second_slip_of_another_kind_is_answered():
    """O jogador a quem isto serve: colou a colecção, ouviu que faltava a
    peça, e a seguir mandou o contrato sem o tokenId. Ficava sem resposta."""
    w = World()
    hunt = w.launch()
    _thread(w, hunt, (6001, "42", COLLECTION), (6002, "42", GOOD))
    _run(w, hunt)
    assert replies_to(w.rig, 6001) == [POST_REPLY_COLLECTION_LINK]
    assert replies_to(w.rig, 6002) == [POST_REPLY_FORMAT]


def test_the_same_slip_twice_is_answered_once():
    w = World()
    hunt = w.launch()
    _thread(w, hunt, (6001, "42", f"{GOOD}:7"), (6002, "42", GOOD),
            (6003, "42", f"{GOOD}:9"))
    _run(w, hunt)
    assert replies_to(w.rig, 6001) == [POST_REPLY_FORMAT]
    assert replies_to(w.rig, 6002) == [] and replies_to(w.rig, 6003) == []
    assert [_row(w, t)["outcome"] for t in (6001, 6002, 6003)] == ["format"] * 3


def test_three_replies_to_one_profile_and_not_one_more():
    w = World()
    hunt = w.launch()
    _thread(w, hunt, (6001, "42", COLLECTION), (6002, "42", GOOD),
            (6003, "42", TYPO), (6004, "42", SLUG_LINK),
            (6005, "42", f"tezos:{KT1}:5"),
            (6006, "43", SLUG_LINK))                       # somebody else
    _run(w, hunt)
    answered = [len(replies_to(w.rig, t)) for t in range(6001, 6006)]
    assert answered == [1, 1, 1, 0, 0]
    assert [_row(w, t)["submitted_claim_code"] for t in (6004, 6005)] == [
        "unreadable_link", "other_chain"]                  # logged all the same
    assert replies_to(w.rig, 6006) == [POST_REPLY_UNRESOLVED_LINK]
    assert Orchestrator._MAX_FORMAT_REPLIES == 3


def test_what_was_answered_before_a_restart_is_not_answered_again():
    w = World()
    hunt = w.launch()
    _thread(w, hunt, (6001, "42", COLLECTION), (6002, "42", GOOD))
    _run(w, hunt)
    before = list(w.rig.publisher.post_replies)
    rebuilt = _restart(w, hunt)
    sys_sent = w.orch._rebuild_claim_state(rebuilt)[4]
    assert sys_sent["format:collection_link"] == {"42"}
    assert sys_sent["format:missing"] == {"42"}
    _thread(w, rebuilt, (6010, "42", COLLECTION), (6011, "42", f"{GOOD}:3"),
            (6012, "42", TYPO))                            # a third kind: answered
    _run(w, rebuilt)
    new = [r for r in w.rig.publisher.post_replies if r not in before]
    assert new == [("6012", post_reply_bad_address(("S",), 40))]


def test_the_ceiling_survives_a_restart():
    w = World()
    hunt = w.launch()
    _thread(w, hunt, (6001, "42", COLLECTION), (6002, "42", GOOD), (6003, "42", TYPO))
    _run(w, hunt)
    rebuilt = _restart(w, hunt)
    _thread(w, rebuilt, (6010, "42", SLUG_LINK), (6011, "42", f"tezos:{KT1}:5"))
    _run(w, rebuilt)
    assert replies_to(w.rig, 6010) == [] and replies_to(w.rig, 6011) == []


def test_rows_written_before_this_change_are_understood():
    """As linhas 'format' e 'malformed' que o commit anterior já escreve."""
    w = World()
    hunt = w.launch()
    for tid, author, label, outcome in (
            (7001, "50", "no_chain", "format"), (7002, "50", "no_token_id", "format"),
            (7003, "51", "collection_link", "format"), (7004, "52", "3 tokens", "malformed"),
            (7005, "53", None, "format")):
        w.rig.repo.log_submission(
            hunt_id=hunt.id, dm_id=str(tid), sender_x_id=author, wallet=None,
            sender_handle=f"u{author}", submitted_claim_code=label, outcome=outcome,
            x_created_at=hunt.live_at)
    sys_sent = w.orch._rebuild_claim_state(hunt)[4]
    assert sys_sent == {"format:missing": {"50"}, "format:collection_link": {"51"},
                        "format:one_token": {"52"}, "format:format": {"53"}}
    assert Orchestrator._format_replies_to(sys_sent, "50") == 1     # one type, not two


def test_a_multi_token_post_is_a_type_of_its_own():
    w = World()
    hunt = w.launch()
    _thread(w, hunt, (6001, "42", TWO_LINKS), (6002, "42", TWO_LINKS),
            (6003, "42", f"{GOOD}:7"))
    _run(w, hunt)
    assert replies_to(w.rig, 6001) == [POST_REPLY_ONE_TOKEN]
    assert replies_to(w.rig, 6002) == []
    assert replies_to(w.rig, 6003) == [POST_REPLY_FORMAT]


# --------------------------------------------------------------------------- #
# 12. Outras cadeias — uma resposta, e nenhuma lista                            #
# --------------------------------------------------------------------------- #

OTHER = [
    f"bsc:{GOOD}:12", f"avalanche:{GOOD}:12", f"BSC: {GOOD} : 12",   # a chain we do not read
    f"tezos:{KT1}:123456", f"{KT1} 123456", f"xtz:{KT1}",
    f"solana:{MINT}", f"sol: {MINT}", f"btc:{INSCRIPTION}", f"ordinals: {INSCRIPTION}",
    f"https://objkt.com/tokens/{KT1}/123456",
    f"https://magiceden.io/item-details/{MINT}",
    f"https://opensea.io/assets/solana/{MINT}",
    "https://tensor.trade/item/whatever",
]
NOT_OTHER = [
    f"ethereum:{GOOD}:12", f"base:{GOOD}:12", f"answer:{GOOD}:12", f"{GOOD}:12",
    MINT,                                    # a bare id says nothing about a chain
    "bitcoin: still king", "btc is the answer", "gm solana frens", "sol: no",
    SLUG_LINK, f"https://magiceden.io/item-details/ethereum/{GOOD}/5",
]


@pytest.mark.parametrize("text", OTHER)
def test_a_token_we_cannot_read_by_its_chain_is_recognised(text):
    assert other_chain(text) is True


@pytest.mark.parametrize("text", NOT_OTHER)
def test_what_is_ours_or_is_chatter_is_not(text):
    assert other_chain(text) is False


def test_the_reply_is_the_approved_text_word_for_word():
    assert POST_REPLY_OTHER_CHAIN == (
        "i can't read that one — my side, not yours, and it cost you nothing. "
        "reply with chain:contract:tokenId (like ethereum:0xabc…def:42) or the "
        "marketplace link.")
    assert CLAIM_FORMAT_EXAMPLE in POST_REPLY_OTHER_CHAIN


@pytest.mark.parametrize("text", OTHER)
def test_one_reply_for_all_three_cases_and_no_guess(text):
    m = _matcher()
    assert m.looks_like_claim(text) is False
    assert m.format_kind(text) == "other_chain"
    assert m.format_hint(text) == POST_REPLY_OTHER_CHAIN


def test_what_these_posts_used_to_hear():
    """Medido antes de mexer: uma cadeia que não lemos levava "falta-te
    cadeia, contrato E tokenId"; um link de Tezos levava "não consigo ler
    esse link"; um id de Tezos ia ao juiz de humor."""
    old = _matcher(other_chain_reply="")
    assert old.format_hint(f"bsc:{GOOD}:12") == POST_REPLY_FORMAT
    assert old.format_hint(f"https://objkt.com/tokens/{KT1}/1") == POST_REPLY_UNRESOLVED_LINK
    assert old.format_hint(f"tezos:{KT1}:1") is None


@pytest.mark.parametrize("text, kind", [
    (SLUG_LINK, "unreadable_link"),                    # a link of ours we could not read
    (COLLECTION, "collection_link"),
    (f"{GOOD}:12", "no_chain"), (f"answer:{GOOD}:12", "no_token_id"),
    (f"https://magiceden.io/item-details/ethereum/{GOOD}/5", None),   # a claim
    ("bitcoin: still king", None),                     # chatter
])
def test_the_other_rules_keep_what_was_theirs(text, kind):
    assert _matcher().format_kind(text) == kind


@pytest.mark.parametrize("url, yes", [
    ("https://objkt.com/tokens/x/1", True), ("https://www.objkt.com/x", True),
    ("https://teia.art/objkt/1", True), ("https://solscan.io/token/x", True),
    ("https://rarible.com/token/tezos/x:1", True),
    ("https://notobjkt.com/x", False), ("https://opensea.io/collection/x", False),
    ("https://example.com/blog/ethereum", False), ("", False),
])
def test_a_link_to_a_market_without_0x_contracts(url, yes):
    assert non_evm_link(url) is yes


def test_in_the_thread_it_gets_the_reply_a_row_and_no_guess():
    w = World()
    hunt = w.launch()
    _thread(w, hunt, (6001, "42", f"tezos:{KT1}:123456"), (6002, "43", f"bsc:{GOOD}:12"))
    _run(w, hunt)
    for tid in (6001, 6002):
        assert replies_to(w.rig, tid) == [POST_REPLY_OTHER_CHAIN]
        row = _row(w, tid)
        assert (row["outcome"], row["submitted_claim_code"]) == ("format", "other_chain")
        assert KT1 not in repr(row) and GOOD not in repr(row)
    assert w.orch._rebuild_claim_state(hunt)[1] == {}        # nobody spent a guess


# -- a regra: nenhuma resposta pública lista as cadeias que lemos ------------ #

READ_CHAINS = set(CHAIN_ALIASES) | set(CHAIN_ALIASES.values())


def _public_replies() -> dict[str, str]:
    """Every fixed text the oracle can send in reply to a player, by name."""
    from finding_memeland.orchestrator import state_machine
    out: dict[str, str] = {}
    for mod in (target_templates, content_templates, state_machine):
        for name, value in vars(mod).items():
            if name.startswith(("POST_REPLY_", "DM_REPLY_")) and isinstance(value, str):
                out.setdefault(name, value)
    for bad, length in ((("S",), 40), ((), 39), (("S", "z"), 41), (tuple("ghij"), 42)):
        out[f"bad_address {''.join(bad) or '-'} {length}"] = post_reply_bad_address(bad, length)
    return out


def test_there_are_replies_to_check():
    replies = _public_replies()
    assert len(replies) >= 20
    assert {"POST_REPLY_OTHER_CHAIN", "POST_REPLY_FORMAT", "POST_REPLY_INVALID_WALLET",
            "POST_REPLY_OUT_OF_TRIES", "DM_REPLY_LATE"} <= set(replies)


@pytest.mark.parametrize("name", sorted(_public_replies()))
def test_no_public_reply_lists_the_chains_we_read(name):
    """A cadeia faz parte da resposta (Pedro, 09/10). O exemplo publicado do
    formato é o único sítio onde uma resposta escreve o nome de uma cadeia —
    e escreve UMA, sempre a mesma, em todas as hunts.

    A única outra menção é a da CARTEIRA DO PRÉMIO: "a valid Base wallet". O
    prémio é sempre na Base, é público e está na Clue 1; não diz nada sobre
    onde está o tesouro. Fica permitida só aí, numa resposta que fale da
    carteira."""
    text = _public_replies()[name].replace(CLAIM_FORMAT_EXAMPLE, " ")
    words = set(re.findall(r"[a-z0-9]+", text.lower()))
    if "wallet" in words:
        words.discard("base")
    assert not (words & READ_CHAINS), (name, sorted(words & READ_CHAINS))
    assert not (words & OTHER_CHAIN_WORDS), (name, sorted(words & OTHER_CHAIN_WORDS))


def test_the_prize_wallet_is_the_only_other_place_a_chain_is_named():
    named = {name for name, text in _public_replies().items()
             if set(re.findall(r"[a-z0-9]+", text.replace(CLAIM_FORMAT_EXAMPLE, " ").lower()))
             & READ_CHAINS}
    assert named == {"POST_REPLY_INVALID_WALLET"}


def test_the_format_example_names_one_chain_and_never_changes_with_the_hunt():
    assert CLAIM_FORMAT_EXAMPLE == "ethereum:0xabc…def:42"
    assert "hunt" not in inspect.signature(post_reply_bad_address).parameters
