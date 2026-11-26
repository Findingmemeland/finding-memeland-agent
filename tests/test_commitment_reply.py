"""O commitment, a regra do repost e a linha dos holders saem da abertura para
uma resposta do bot por baixo dela (Pedro, 25/11).

5 dos 10 posts que o X escondeu como SPAM_HIGH_RECALL em outubro eram
aberturas. A abertura perde o "Quote or repost this post to enter", a linha
dos holders e o "commitment v2: …", que passam para uma resposta do bot ao
próprio post, publicada logo a seguir. A resposta começa por dizer onde vão
os palpites: um palpite dado como resposta a ela continua "wrong door" — não
conta, não gasta tentativa, e o bot responde-lhe uma vez. O repost ou quote
continua obrigatório para ganhar (a regra não muda) e continua no post fixado.

A garantia mantém-se: o commitment é público antes de qualquer claim que
conte. `live_at`, a fronteira com que cada claim é comparado, só é marcado
depois de a resposta estar no ar; um claim anterior é 'early' e nunca ganha.
Se a resposta falhar, a hunt fica LIVE com os claims fechados, o ciclo
tenta-a outra vez a cada volta (e depois de um reinício, porque o live_at
vazio fica na linha da hunt), e um "duplicate" do X quer dizer que já lá
está.
"""
from __future__ import annotations

from datetime import timedelta

import pytest

from finding_memeland.orchestrator.state_machine import HuntState
from finding_memeland.target.dryrun import WALLET_A, TargetWorld, post, replies_to
from finding_memeland.target.templates import (
    POST_REPLY_EARLY_TARGET,
    POST_REPLY_WRONG_DOOR_TARGET,
    commitment_block,
    target_clue_one,
    target_commitment_reply,
    x_weighted_len,
)

COMMIT = "c0ffee" * 10 + "abcd"


def _pct(w, hunt):
    return w.orch._non_holder_pct if hunt.min_balance_fmml > 0 else None


def _break_replies(w, n, exc=None):
    """As próximas `n` respostas do bot falham; depois voltam a sair."""
    pub = w.rig.publisher
    real = pub.reply_post
    left = {"n": n}

    def reply(text, *, in_reply_to):
        if left["n"] > 0:
            left["n"] -= 1
            raise exc or RuntimeError("503 Service Unavailable")
        return real(text, in_reply_to=in_reply_to)
    pub.reply_post = reply
    return left


# --------------------------------------------------------------------------- #
# Os textos                                                                    #
# --------------------------------------------------------------------------- #


def test_the_reply_is_where_to_claim_the_reshare_gate_the_holder_line_and_the_commitment():
    r = target_commitment_reply(COMMIT)
    assert r == ("claims go as replies to the hunt post above, not here.\n"
                 "to be eligible: repost or quote the hunt post. hold FIND for the "
                 "full prize — non-holders win 10%.\n\n"
                 f"commitment v2: {COMMIT}")
    assert "$" not in r and "http" not in r and x_weighted_len(r) <= 280


def test_without_the_holding_floor_the_reply_has_no_holder_sentence():
    assert target_commitment_reply(COMMIT, None) == (
        "claims go as replies to the hunt post above, not here.\n"
        f"to be eligible: repost or quote the hunt post.\n\ncommitment v2: {COMMIT}")


def test_the_opening_keeps_how_to_claim_and_the_prize_and_nothing_of_the_reply():
    p = target_clue_one(43, "a clue", "200,000,000")
    assert "Quote or repost" not in p and "commitment" not in p and "$" not in p
    assert "non-holders" not in p and "hold FIND" not in p      # the reply's, since 25/11
    assert p.endswith("The first to find it wins 200,000,000 FIND.\n\n"
                      "Check pinned for rules.")


@pytest.mark.parametrize("stale", [
    ("c0ffee",),                     # the old positional commitment
    (10,),                           # the old positional holder share
])
def test_a_stale_positional_call_fails_loud(stale):
    """Uma chamada antiga com argumentos por posição punha o hash no lugar
    da percentagem ("non-holders win c0ffee…%"): tem de rebentar."""
    with pytest.raises(TypeError):
        target_clue_one(43, "a clue", "200,000,000", *stale)


def test_a_stale_call_with_the_holder_share_by_name_fails_loud():
    """A percentagem saiu da abertura a 25/11: quem ainda a passar não pode
    pensar que ela aparece lá."""
    with pytest.raises(TypeError):
        target_clue_one(43, "a clue", "200,000,000", non_holder_pct=10)


def test_the_wrong_door_reply_says_a_direct_reply_to_the_clue_1_post():
    """Pedro, 26/11: whoever answered OUR reply is already "in the replies"
    of the Clue 1 post — the text has to say a DIRECT reply."""
    assert POST_REPLY_WRONG_DOOR_TARGET == (
        "the claim goes as a direct reply to the Clue 1 post — drop the "
        "chain:contract:tokenId (or link) there and it counts. \U0001F438")


def test_the_reveal_and_the_void_point_at_the_reply():
    b = commitment_block(target_id="ethereum:0xabc:1", metadata_sha256="m", salt="s")
    assert "It matches the commitment in our reply under Clue 1." in b
    assert "It matches the commitment in Clue 1." not in b


# --------------------------------------------------------------------------- #
# A abertura da hunt                                                           #
# --------------------------------------------------------------------------- #


def test_go_live_posts_the_opening_then_the_reply_under_it_then_opens_the_claims():
    w = TargetWorld()
    pub, order = w.rig.publisher, []
    real_post, real_reply = pub.post, pub.reply_post

    def post_(text, **kw):
        order.append(("post", w.rig.clock.now()))
        return real_post(text, **kw)

    def reply_(text, *, in_reply_to):
        order.append(("reply", w.rig.clock.now()))
        return real_reply(text, in_reply_to=in_reply_to)
    pub.post, pub.reply_post = post_, reply_
    hunt = w.launch()
    assert [k for k, _ in order] == ["post", "reply"]
    assert replies_to(w.rig, hunt.reshare_post_id) == [
        target_commitment_reply(hunt.integrity_hash, _pct(w, hunt))]
    assert "commitment" not in w.posts()[0]
    # the claims open only once the reply is up
    assert hunt.state is HuntState.LIVE and hunt.live_at >= order[1][1]
    assert w.rig.repo.hunts[hunt.id]["live_at"] == hunt.live_at
    assert any("LIVE — clue 1 posted" in m and "commitment reply" in m for m in w.notices())


def test_a_claim_under_our_reply_is_a_wrong_door_answered_once():
    """Pedro, 25/11: a claim posted as a reply to OUR reply, not to the hunt
    post, never counts — 'wrong door', logged, no guess spent — and the bot
    answers it once, saying where the claim goes. The right answer there
    wins nothing; the same answer under the hunt post does."""
    w = TargetWorld()
    pub, first_reply_to = w.rig.publisher, {}
    real = pub.reply_post

    def reply(text, *, in_reply_to):
        tid = real(text, in_reply_to=in_reply_to)
        first_reply_to.setdefault(in_reply_to, tid)
        return tid
    pub.reply_post = reply
    hunt = w.launch()
    ours = first_reply_to[hunt.reshare_post_id]          # the commitment reply
    target, clock = hunt.target.target, w.rig.clock
    right = f"ethereum:{target.contract}:{target.token_id}"
    w.src.reshared.update({"41"})
    w.src.schedule[1] = lambda: [
        post(3001, "41", right, clock.now(), ours),
        post(3002, "41", right, clock.now() + timedelta(seconds=1), ours)]
    w.src.schedule[2] = lambda: [post(3003, "41", right, clock.now(), hunt.reshare_post_id)]
    w.src.schedule[5] = lambda: [
        post(3004, "41", WALLET_A, clock.now(),
             w.rig.repo.hunts[hunt.id].get("pending_ask_tweet_id"))]
    winner = w.orch._claim_loop(hunt)
    assert winner.submission.dm_id == "3003" and winner.wallet == WALLET_A
    outcome = {r["dm_id"]: r["outcome"] for r in w.rig.repo.submissions}
    assert outcome["3001"] == outcome["3002"] == "wrong_door"
    assert "wrong_door" not in w.orch._GUESS_OUTCOMES        # no guess spent
    assert replies_to(w.rig, "3001") == [POST_REPLY_WRONG_DOOR_TARGET]
    assert replies_to(w.rig, "3002") == []                   # once per profile


# --------------------------------------------------------------------------- #
# Se a resposta falhar                                                         #
# --------------------------------------------------------------------------- #


def test_a_reply_that_cannot_be_posted_leaves_the_hunt_live_with_the_claims_closed():
    w = TargetWorld()
    _break_replies(w, 3)
    t0 = w.rig.clock.now()
    hunt = w.launch()
    # three tries, 2 s and 4 s apart (launch() itself waits 600 s first)
    assert (w.rig.clock.now() - t0).total_seconds() == 600 + 2 + 4
    assert hunt.state is HuntState.LIVE and hunt.live_at is None
    assert w.rig.repo.hunts[hunt.id]["live_at"] is None        # a restart sees it too
    assert replies_to(w.rig, hunt.reshare_post_id) == []
    notes = " | ".join(w.notices())
    assert "commitment reply NOT posted (RuntimeError)" in notes
    assert "claims stay CLOSED" in notes and "503" not in notes   # the type, never the text


def test_a_claim_while_closed_never_counts_and_the_claims_open_once_the_reply_is_up():
    w = TargetWorld()
    _break_replies(w, 4)                    # go-live (3 tries) + the first cycle
    hunt = w.launch()
    target, clock = hunt.target.target, w.rig.clock
    right = f"ethereum:{target.contract}:{target.token_id}"
    w.src.reshared.update({"41", "42"})
    # cycle 1: the reply still fails — the right answer from 41 is early
    w.src.schedule[1] = lambda: [post(2001, "41", right, clock.now(), hunt.reshare_post_id)]
    # cycle 2: Phase 0c puts the reply up first; 42's right answer comes after it
    w.src.schedule[2] = lambda: [
        post(2002, "42", right, clock.now() + timedelta(seconds=1), hunt.reshare_post_id)]
    w.src.schedule[5] = lambda: [
        post(2003, "42", WALLET_A, clock.now(),
             w.rig.repo.hunts[hunt.id].get("pending_ask_tweet_id"))]
    winner = w.orch._claim_loop(hunt)
    assert winner.submission.sender_x_id == "42" and winner.wallet == WALLET_A
    early = [r for r in w.rig.repo.submissions if r["dm_id"] == "2001"]
    assert [r["outcome"] for r in early] == ["early"]
    assert replies_to(w.rig, "2001") == [POST_REPLY_EARLY_TARGET]
    assert replies_to(w.rig, hunt.reshare_post_id) == [
        target_commitment_reply(hunt.integrity_hash, _pct(w, hunt))]
    assert hunt.live_at is not None
    assert w.rig.repo.hunts[hunt.id]["live_at"] == hunt.live_at
    assert any("claims OPEN" in m for m in w.notices())


def test_x_answering_duplicate_means_the_reply_is_already_up():
    w = TargetWorld()
    _break_replies(w, 1, RuntimeError("403 Forbidden: 187 - Status is a duplicate."))
    hunt = w.launch()
    assert hunt.live_at is not None
    notes = " | ".join(w.notices())
    assert "commitment reply already up (X: duplicate)" in notes and "NOT posted" not in notes


def test_a_long_outage_is_reported_every_20_tries_not_every_cycle():
    w = TargetWorld()
    _break_replies(w, 3 + 45)
    hunt = w.launch()
    w.orch._max_rounds = 45
    with pytest.raises(RuntimeError, match="max rounds"):
        w.orch._claim_loop(hunt)
    still = [m for m in w.notices() if "still NOT posted" in m]
    assert len(still) == 2 and "(20 more tries)" in still[0] and "(40 more tries)" in still[1]
    assert hunt.live_at is None
