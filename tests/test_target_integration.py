"""Target hunt end to end through the REAL orchestrator with fakes: prepare
(gate → seal → row), Clue 1 v2, the claim loop with the target matcher
(format hint not counted, wrong id jeered, right id wins), pay + verifiable
reveal, the live check (hold freezes the deadline; mutation in the puzzle
phase void-reveals and the next draw excludes the target), anti-spray
pause, crash-resume from the sealed row."""

from __future__ import annotations

import random
import re
from datetime import timedelta

import pytest

from finding_memeland.orchestrator.state_machine import HuntState
from finding_memeland.target.commitment import compute_commitment_v2
from finding_memeland.target.hunt import LiveCheck, SprayParams
from finding_memeland.target.sources import ChainUnavailable
from finding_memeland.target.templates import POST_REPLY_FORMAT

# The world (snapshot + live chain + rig) and the fakes live in
# target/dryrun.py — the 6/6 dry-run and these tests share ONE harness.
from finding_memeland.target.dryrun import (
    WALLET_A,
    TargetWorld as World,
    post,
    replies_to,
)


# --------------------------------------------------------------------------- #
# Prepare + Clue 1                                                             #
# --------------------------------------------------------------------------- #


def test_prepare_seals_the_row_and_clue_one_is_v2():
    w = World()
    hunt = w.launch()
    row = w.rig.repo.hunts[hunt.id]
    assert row["claim_code"] == "" and row["target_sealed"]
    assert row["integrity_hash"] == compute_commitment_v2(
        hunt.target.id(), hunt.target.target.metadata_sha256, hunt.target.salt)
    assert hunt.target.target.name not in row["target_sealed"]     # cifrado
    assert hunt.persona.x_user_id == ""                             # nunca o id
    clue1 = w.rig.publisher.posts[0]
    assert "commitment v2:" in clue1 and "the treasure can be anywhere onchain" in clue1
    assert "code in its" not in clue1
    # o operador vê estrutura, nunca o nome
    assert all("Whispering" not in m for m in w.rig.notifier.messages)
    assert hunt.state is HuntState.LIVE


def test_content_guard_refusal_redraws_with_the_id_excluded_and_is_bounded():
    """Opus 06/09: o content_ok vive na passagem de visão (a imagem já está
    em mãos, dentro de um lote que já existe). Chumbo ⇒ novo sorteio com o
    id em exclude; três chumbos seguidos ⇒ launch recusado, nunca moído."""
    from finding_memeland.target.clues import ContentRefused
    from finding_memeland.target.hunt import LaunchRefused
    w = World()
    seen: list[str] = []

    def describe(sealed):
        seen.append(sealed.id())
        if len(seen) == 1:
            raise ContentRefused(sealed.id(), "nsfw")
        return "a lighthouse on a black rock"
    w.ports.describe_image = describe
    hunt = w.launch()
    assert len(seen) == 2 and seen[0] != seen[1] and hunt.target.id() == seen[1]
    assert any("content guard refused" in m for m in w.rig.notifier.messages)
    assert all("Whispering" not in m for m in w.rig.notifier.messages)

    w2 = World()
    w2.ports.describe_image = lambda sealed: (_ for _ in ()).throw(
        ContentRefused(sealed.id()))
    with pytest.raises(LaunchRefused) as e:
        w2.orch._prepare(200)
    assert "content guard refused 3" in str(e.value)


def test_gateway_outage_at_void_time_is_printed_as_ours_never_as_a_burn():
    """P0 (auditoria 09/09): três coisas somavam-se — live_hash None para
    burn E para gateway em baixo, ipfs.io morto por omissão, e o template a
    imprimir "reverts"/"burned". Agora: a falha do gateway é tri-estado e o
    post di-lo; a causa publicada é a MEDIDA por RPC (CID diferente)."""
    from finding_memeland.target.sources import ChainUnavailable as CU
    w = World()

    def gateway_down(sealed):
        raise CU("pinata 503")
    w.ports.live_hash = gateway_down
    hunt = w.launch()
    t = hunt.target.target
    w.live[(t.chain, t.contract, t.token_id)] = ("ipfs://Qm" + "9" * 44 + "/metadata.json", "0xowner")
    w.orch._clue_due_fn = lambda now: now
    assert w.orch._claim_loop(hunt) is None
    post = next(p for p in w.rig.publisher.posts if "is void" in p)
    assert "tokenURI now points at different content" in post
    assert "our gateway could not fetch it" in post
    assert "reverts" not in post and "burned" not in post and "owner" not in post.split("Here is")[0]

    # ports without a live_hash at all (sims) are "unavailable", never "unresolvable"
    w2 = World()
    w2.ports.live_hash = None
    hunt2 = w2.launch()
    t2 = hunt2.target.target
    w2.live[(t2.chain, t2.contract, t2.token_id)] = None            # burn (ownerOf reverts)
    w2.orch._clue_due_fn = lambda now: now
    assert w2.orch._claim_loop(hunt2) is None
    post2 = next(p for p in w2.rig.publisher.posts if "is void" in p)
    assert "appears burned" in post2 and "our gateway could not fetch it" in post2
    assert "the chain no longer serves" not in post2


def test_void_post_publishes_sealed_and_live_token_uri():
    """A anulação por mutação publica o tokenURI selado E o que a cadeia
    respondeu — os dois, para o void ser tão verificável como a vitória."""
    w = World()
    hunt = w.launch()
    t = hunt.target.target
    new_uri = "ipfs://Qm" + "9" * 44 + "/metadata.json"
    w.live[(t.chain, t.contract, t.token_id)] = (new_uri, "0xowner")
    w.orch._clue_due_fn = lambda now: now
    assert w.orch._claim_loop(hunt) is None
    post = next(p for p in w.rig.publisher.posts if "is void" in p)
    assert t.token_uri in post and new_uri in post
    assert "deadbeef" in post                                # live hash, resolvido no void


# --------------------------------------------------------------------------- #
# Claim loop with the target matcher                                           #
# --------------------------------------------------------------------------- #


def test_format_hint_wrong_id_and_right_id_wins():
    w = World()
    hunt = w.launch()
    t0 = hunt.live_at
    target = hunt.target.target
    w.src.reshared.add("42")
    w.src.reshared.add("7")
    w.src.schedule[1] = lambda: [
        # paste sem cadeia: resposta de formato, NÃO conta como palpite
        post(1001, "42", f"{target.contract}:{target.token_id}",
             t0 + timedelta(minutes=1), hunt.reshare_post_id),
        # id errado: bad_code + jeer
        post(1002, "7", "ethereum:0x" + "9" * 40 + ":1",
             t0 + timedelta(minutes=2), hunt.reshare_post_id),
        # id certo (link OpenSea)
        post(1003, "42", f"https://opensea.io/assets/ethereum/{target.contract}/{target.token_id}",
             t0 + timedelta(minutes=3), hunt.reshare_post_id),
    ]
    w.src.schedule[5] = lambda: [
        post(1050, "42", f"here {WALLET_A}", t0 + timedelta(minutes=6),
             w.rig.repo.hunts[hunt.id].get("pending_ask_tweet_id")),
    ]
    winner = w.orch._claim_loop(hunt)
    assert winner is not None and winner.submission.sender_x_id == "42"
    assert winner.wallet == WALLET_A
    assert replies_to(w.rig, 1001) == [POST_REPLY_FORMAT]
    outcomes = {s["dm_id"]: s["outcome"] for s in w.rig.repo.submissions}
    assert "1001" not in outcomes                       # formato não é palpite
    assert outcomes["1002"] == "bad_code"
    assert outcomes["1003"] == "won"
    assert w.rig.repo.submissions[-1]["submitted_claim_code"] == target.id()


def test_full_hunt_pays_and_reveal_verifies():
    w = World()
    hunt = w.launch()
    t0 = hunt.live_at
    target = hunt.target.target
    w.src.reshared.add("42")
    w.src.schedule[1] = lambda: [
        post(1010, "42", f"ethereum:{target.contract}:{target.token_id}",
             t0 + timedelta(minutes=1), hunt.reshare_post_id)]
    w.src.schedule[4] = lambda: [
        post(1040, "42", WALLET_A, t0 + timedelta(minutes=4),
             w.rig.repo.hunts[hunt.id].get("pending_ask_tweet_id"))]
    winner = w.orch._claim_loop(hunt)
    receipt = w.orch._pay(hunt, winner)
    w.orch._reveal(hunt, winner, receipt)
    w.orch._retire(hunt)
    assert hunt.state is HuntState.DONE
    reveal = next(p for p in w.rig.publisher.posts if "We have a winner" in p)
    assert target.name_onchain in reveal
    chain = re.search(r"chain: (\S+)", reveal).group(1)
    contract = re.search(r"contract: (\S+)", reveal).group(1)
    token = re.search(r"tokenId: (\S+)", reveal).group(1)
    meta = re.search(r"  metadata_sha256: (\S+)", reveal).group(1)
    salt = re.search(r"salt: (\S+)", reveal).group(1)
    assert compute_commitment_v2(f"{chain}:{contract}:{token}", meta, salt) == hunt.integrity_hash
    assert "note:" not in reveal
    # Opus (dry-run 09/09): the reveal SHOWS the treasure — art as media,
    # item link in the text; the media never blocks the announcement
    assert f"see it: opensea.io/item/ethereum/{target.contract.lower()}/{target.token_id}" in reveal
    from finding_memeland.target.dryrun import FAKE_ARTWORK
    assert list(w.rig.publisher.media.values()) == [FAKE_ARTWORK]
    assert f"“{target.name_onchain}”, by {target.artist}" in reveal      # R9
    assert target.artist and target.artist in list(w.rig.publisher.media_alt.values())[0]
    assert w.rig.payout.sent and w.rig.payout.sent[0]["wallet"] == WALLET_A


# --------------------------------------------------------------------------- #
# Live check: hold freezes the deadline; mutation relaunches                   #
# --------------------------------------------------------------------------- #


def test_live_check_runs_in_the_sealed_batch_before_each_clue():
    w = World()
    hunt = w.launch()
    w.orch._hunt_timeout_h = None
    w.orch._max_rounds = 4
    w.orch._clue_due_fn = lambda now: now                 # a clue every cycle
    with pytest.raises(RuntimeError):                     # max rounds, no winner
        w.orch._claim_loop(hunt)
    clue_posts = [p for p in w.rig.publisher.posts if "Clue:" in p]
    assert len(clue_posts) >= 3
    batches = [set(w.fetches[i:i + 8]) for i in range(0, len(w.fetches), 8)]
    assert len(batches) >= 3 and all(len(b) == 8 for b in batches)
    assert len(set(map(frozenset, batches))) == 1        # o MESMO lote sempre


def test_transport_outage_holds_and_freezes_the_void_deadline():
    w = World()
    hunt = w.launch()
    w.orch._hunt_timeout_h = 1                            # prazo curto
    w.orch._clue_due_fn = lambda now: now
    w.orch._max_rounds = 90                               # 90 × 60s = 1h30
    w.down = True                                          # RPC em baixo todo o run
    with pytest.raises(RuntimeError):                      # max rounds — NÃO void
        w.orch._claim_loop(hunt)
    assert hunt.state is HuntState.LIVE
    assert not any("Clue:" in p for p in w.rig.publisher.posts)   # sem pistas em hold
    assert any("HOLD (live check unavailable)" in m for m in w.rig.notifier.messages)
    assert hunt.target_hold.held_seconds(w.rig.clock.now().timestamp()) > 3600


def test_reveal_without_usable_artwork_goes_out_with_the_link_and_tells_why():
    """Opus: video/SVG art degrades the reveal to text — correct, but the
    operator must be able to COUNT it (the reason is measured, never
    published); a hung/failed gateway is a type name, and the reveal never
    waits on either."""
    from finding_memeland.target.integration import ArtworkUnusable

    def run(fetch):
        w = World()
        w.ports.fetch_artwork = fetch
        hunt = w.launch()
        t0 = hunt.live_at
        target = hunt.target.target
        w.src.reshared.add("42")
        w.src.schedule[1] = lambda: [
            post(1010, "42", f"ethereum:{target.contract}:{target.token_id}",
                 t0 + timedelta(minutes=1), hunt.reshare_post_id)]
        w.src.schedule[4] = lambda: [
            post(1040, "42", WALLET_A, t0 + timedelta(minutes=4),
                 w.rig.repo.hunts[hunt.id].get("pending_ask_tweet_id"))]
        winner = w.orch._claim_loop(hunt)
        w.orch._reveal(hunt, winner, w.orch._pay(hunt, winner))
        reveal = next(p for p in w.rig.publisher.posts if "We have a winner" in p)
        assert "see it: opensea.io/item/" in reveal and w.rig.publisher.media == {}
        return [m for m in w.rig.notifier.messages if "reveal artwork" in m], target

    def unusable(sealed):
        raise ArtworkUnusable("not a still image (video/mp4)")
    notes, target = run(unusable)
    assert notes == ["reveal artwork not attached: not a still image (video/mp4) — "
                     "posting with the item link only"]

    def hung(sealed):
        raise TimeoutError("gateway")
    notes, target = run(hung)
    assert len(notes) == 1 and "TimeoutError" in notes[0]
    assert target.name_onchain not in notes[0] and target.contract not in notes[0]


def test_r9_credit_order_metadata_then_chain_then_link_only():
    """Opus: metadata artist → tokenCreator+ENS (verified) → 0x… → nothing;
    step 4 never blocks; one read per reveal, shared by text and alt-text;
    the operator hears which step the credit came from."""
    import dataclasses

    def run(artist, port):
        w = World()
        w.ports.creator_credit = port
        hunt = w.launch()
        hunt.target = dataclasses.replace(
            hunt.target, target=dataclasses.replace(hunt.target.target, artist=artist))
        t0 = hunt.live_at
        target = hunt.target.target
        w.src.reshared.add("42")
        w.src.schedule[1] = lambda: [
            post(1010, "42", f"ethereum:{target.contract}:{target.token_id}",
                 t0 + timedelta(minutes=1), hunt.reshare_post_id)]
        w.src.schedule[4] = lambda: [
            post(1040, "42", WALLET_A, t0 + timedelta(minutes=4),
                 w.rig.repo.hunts[hunt.id].get("pending_ask_tweet_id"))]
        winner = w.orch._claim_loop(hunt)
        w.orch._reveal(hunt, winner, w.orch._pay(hunt, winner))
        reveal = next(p for p in w.rig.publisher.posts if "We have a winner" in p)
        alt = list(w.rig.publisher.media_alt.values())[0]
        r9 = [m for m in w.rig.notifier.messages if "R9" in m]
        return reveal, alt, r9, target

    calls = []

    def chain(sealed):
        calls.append(sealed.id())
        return "sarah.eth"
    reveal, alt, r9, t = run("Meta Name", chain)
    assert ", by Meta Name" in reveal and "by Meta Name" in alt and calls == [] and r9 == []
    reveal, alt, r9, t = run("", chain)
    assert ", by sarah.eth" in reveal and "by sarah.eth" in alt
    assert calls == [t.id()] and r9 == []                       # ONE read, cached
    reveal, alt, r9, t = run("", lambda s: "0x1111…1111")
    assert ", by 0x1111…1111" in reveal and any("truncated address" in m for m in r9)
    reveal, alt, r9, t = run("", lambda s: "")
    assert ", by " not in reveal and "made by someone else" in reveal
    assert "see it: opensea.io/item/" in reveal and any("item link only" in m for m in r9)

    def boom(sealed):
        raise TimeoutError("rpc")
    reveal, alt, r9, t = run("", boom)
    assert ", by " not in reveal and any("credit lookup failed" in m and "TimeoutError" in m for m in r9)
    assert all(t.name_onchain not in m for m in r9)


def test_mutation_in_puzzle_phase_void_reveals_and_next_draw_excludes():
    w = World()
    hunt = w.launch()
    t = hunt.target.target
    w.live[(t.chain, t.contract, t.token_id)] = ("ipfs://Qm" + "9" * 44 + "/metadata.json", "0xowner")
    w.orch._clue_due_fn = lambda now: now
    assert w.orch._claim_loop(hunt) is None
    assert hunt.state is HuntState.DONE
    void = next(p for p in w.rig.publisher.posts if "is void" in p)
    assert "tokenURI now points at different content" in void and "starts shortly" in void
    assert re.search(r"salt: (\S+)", void).group(1) == hunt.target.salt
    assert w.rig.repo.hunts[hunt.id]["target_void_id"] == hunt.target.id()
    # relaunch: the voided target is excluded from the next draw
    hunt2 = w.orch._prepare(200)
    assert hunt2.target.id() != hunt.target.id()


def test_mutation_after_valid_claim_pays_with_a_note():
    w = World()
    hunt = w.launch()
    t0 = hunt.live_at
    t = hunt.target.target
    w.src.reshared.add("42")
    w.src.schedule[1] = lambda: [
        post(1010, "42", f"ethereum:{t.contract}:{t.token_id}",
             t0 + timedelta(minutes=1), hunt.reshare_post_id)]
    w.src.schedule[4] = lambda: [
        post(1040, "42", WALLET_A, t0 + timedelta(minutes=4),
             w.rig.repo.hunts[hunt.id].get("pending_ask_tweet_id"))]
    # the owner burns the token right before the claim arrives
    w.live[(t.chain, t.contract, t.token_id)] = None
    winner = w.orch._claim_loop(hunt)
    assert winner is not None and hunt.target_pay_noted
    receipt = w.orch._pay(hunt, winner)
    w.orch._reveal(hunt, winner, receipt)
    reveal = next(p for p in w.rig.publisher.posts if "We have a winner" in p)
    assert "changed AFTER the winning claim" in reveal and "prize paid in full" in reveal


def test_unclaimed_target_hunt_void_reveals_ingredients():
    w = World()
    hunt = w.launch()
    w.orch._hunt_timeout_h = 0.01
    w.rig.clock.sleep(3600)
    assert w.orch._claim_loop(hunt) is None
    void = next(p for p in w.rig.publisher.posts if "is void" in p)
    assert "nobody found it" in void and hunt.target.salt in void
    assert hunt.state is HuntState.DONE


# --------------------------------------------------------------------------- #
# Anti-spray pause                                                             #
# --------------------------------------------------------------------------- #


def test_spray_pauses_for_review_never_voids():
    w = World(live_params=SprayParams(min_total_guesses=3, min_distinct_ratio=0.9))
    hunt = w.launch()
    t0 = hunt.live_at
    w.src.schedule[1] = lambda: [
        post(2000 + i, str(100 + i), f"ethereum:0x{i:040x}:1",
             t0 + timedelta(minutes=1), hunt.reshare_post_id)
        for i in range(5)                                    # 5 contas, 5 alvos distintos
    ]
    w.orch._max_rounds = 3
    with pytest.raises(RuntimeError):
        w.orch._claim_loop(hunt)
    assert w.control.pause_calls == 1
    assert any("PAUSE for review" in m for m in w.rig.notifier.messages)
    assert hunt.state is HuntState.LIVE                      # nunca anulada
    assert not any("is void" in p for p in w.rig.publisher.posts)


# --------------------------------------------------------------------------- #
# Crash-resume                                                                 #
# --------------------------------------------------------------------------- #


def test_resume_rebuilds_the_target_from_the_sealed_row():
    w = World()
    hunt = w.launch()
    row = w.rig.repo.hunts[hunt.id]
    rebuilt = w.orch._rebuild_hunt(row, HuntState.LIVE)
    assert rebuilt.target == hunt.target
    assert rebuilt.ctx.display_name == hunt.target.target.name
    assert rebuilt.ctx.image_description == "a lighthouse on a black rock"
    assert rebuilt.persona.x_user_id == ""
    assert rebuilt.target_hold is not None
    # o detector recomeçou do zero — o operador ouve-o (limite conhecido, visível)
    assert any("anti-spray detector restarted from zero" in m
               for m in w.rig.notifier.messages)
    assert all("Whispering" not in m for m in w.rig.notifier.messages)


# --------------------------------------------------------------------------- #
# Opus P1-A/P1-B, hold persistence, search-guard hold, last-void exclusion    #
# --------------------------------------------------------------------------- #


def test_spray_counts_one_piece_once_whatever_the_link_shape():
    """P1-A: OpenSea link, Rarible link and explicit triple of the SAME piece
    are ONE distinct target — an honest crowd converging on a candidate must
    not look like a farm."""
    w = World(live_params=SprayParams(min_total_guesses=3, min_distinct_ratio=0.9))
    hunt = w.launch()
    t0 = hunt.live_at
    c = "0x" + "9" * 40
    w.src.schedule[1] = lambda: [
        post(3001, "101", f"https://opensea.io/assets/ethereum/{c}/1", t0 + timedelta(minutes=1), hunt.reshare_post_id),
        post(3002, "102", f"https://rarible.com/token/ethereum/{c}:1", t0 + timedelta(minutes=1), hunt.reshare_post_id),
        post(3003, "103", f"ethereum:{c}:1", t0 + timedelta(minutes=1), hunt.reshare_post_id),
        post(3004, "104", f"ETH:{c}:1", t0 + timedelta(minutes=1), hunt.reshare_post_id),
        # links por resolver / paste sem cadeia NÃO entram no detector
        post(3005, "105", "https://foundation.app/@x/piece/1", t0 + timedelta(minutes=1), hunt.reshare_post_id),
    ]
    w.orch._max_rounds = 3
    with pytest.raises(RuntimeError):
        w.orch._claim_loop(hunt)
    assert w.control.pause_calls == 0                      # 4 palpites, 1 distinto
    assert not any("PAUSE" in m for m in w.rig.notifier.messages)


def test_hold_renotifies_hourly_and_calls_the_operator_past_max():
    w = World()
    w.ports.max_hold_s = 3600.0                            # tecto a 1h
    hunt = w.launch()
    w.orch._hunt_timeout_h = None
    w.orch._clue_due_fn = lambda now: now
    w.orch._max_rounds = 150                               # 2h30 a 60s/ciclo
    w.down = True
    with pytest.raises(RuntimeError):
        w.orch._claim_loop(hunt)
    msgs = w.rig.notifier.messages
    assert sum("still on HOLD" in m or "HOLD past MAX" in m for m in msgs) >= 2
    assert any("HOLD past MAX" in m and "DECIDE" in m for m in msgs)
    assert hunt.state is HuntState.LIVE                    # nada decidido sozinho
    assert w.rig.repo.hunts[hunt.id]["target_hold_s"] > 3600


def test_hold_seconds_persist_and_seed_the_resume():
    w = World()
    hunt = w.launch()
    w.orch._hunt_timeout_h = None
    w.orch._clue_due_fn = lambda now: now
    w.orch._max_rounds = 5
    w.down = True
    with pytest.raises(RuntimeError):
        w.orch._claim_loop(hunt)
    row = w.rig.repo.hunts[hunt.id]
    assert row["target_hold_s"] > 0
    rebuilt = w.orch._rebuild_hunt(row, HuntState.LIVE)
    now = w.rig.clock.now().timestamp()
    assert rebuilt.target_hold.held_seconds(now) >= row["target_hold_s"]


def test_search_guard_outage_enters_the_same_hold():
    """(b): a Rarible outage makes the guard unverifiable on every clue —
    the ramp stops AND the deadline freezes; a bare exception did only the
    first, which is a void in slow motion."""
    from finding_memeland.target.clues import SearchGuardUnavailable

    class BlindGuardEngine:
        def next_clue(self, ctx, i, prior):
            if i == 1:
                from finding_memeland.content.clue_engine import ClueDraft
                return ClueDraft(text="first piece", taunt=None)
            raise SearchGuardUnavailable("canary blind")

    w = World()
    w.ports.clue_engine = BlindGuardEngine()
    hunt = w.launch()
    w.orch._hunt_timeout_h = 1
    w.orch._clue_due_fn = lambda now: now
    w.orch._max_rounds = 90                                # 1h30 > prazo de 1h
    with pytest.raises(RuntimeError):                      # max rounds, NÃO void
        w.orch._claim_loop(hunt)
    assert hunt.state is HuntState.LIVE
    assert any("search guard unverifiable" in m for m in w.rig.notifier.messages)
    assert hunt.target_hold.held_seconds(w.rig.clock.now().timestamp()) > 3600


def test_immediate_relaunch_excludes_the_void_even_without_repo_support():
    w = World()
    w.rig.repo.recent_target_voids = lambda: (_ for _ in ()).throw(AttributeError())
    hunt = w.launch()
    t = hunt.target.target
    w.live[(t.chain, t.contract, t.token_id)] = None       # burn
    w.orch._clue_due_fn = lambda now: now
    assert w.orch._claim_loop(hunt) is None
    hunt2 = w.orch._prepare(200)
    assert hunt2.target.id() != hunt.target.id()


# --------------------------------------------------------------------------- #
# Opus volta 2: one token per reply; accumulated hold ceiling                  #
# --------------------------------------------------------------------------- #


def test_shotgun_reply_gets_the_one_token_rule_and_never_wins():
    """P0: 50 links num post = 50 candidatos por um palpite. Malformado:
    resposta de formato, sem gastar palpite, sem match — mesmo com o alvo
    entre os links."""
    from finding_memeland.target.templates import POST_REPLY_ONE_TOKEN
    w = World(live_params=SprayParams(min_total_guesses=3, min_distinct_ratio=0.9))
    hunt = w.launch()
    t0 = hunt.live_at
    t = hunt.target.target
    w.src.reshared.add("42")
    links = [f"https://opensea.io/assets/ethereum/0x{i:040x}/1" for i in range(49)]
    links.append(f"https://opensea.io/assets/ethereum/{t.contract}/{t.token_id}")
    w.src.schedule[1] = lambda: [
        post(4001, "42", " ".join(links), t0 + timedelta(minutes=1), hunt.reshare_post_id)]
    w.orch._max_rounds = 3
    with pytest.raises(RuntimeError):                      # ninguém ganha
        w.orch._claim_loop(hunt)
    assert replies_to(w.rig, 4001) == [POST_REPLY_ONE_TOKEN]
    assert not any(s.get("outcome") in ("won", "pending", "bad_code")
                   for s in w.rig.repo.submissions)     # sem palpite gasto
    assert w.control.pause_calls == 0                      # e o detector não vê 50 alvos
    # P1-1 (auditoria 09/09): a forma de ataque com mais volume deixa rasto
    mal = [s for s in w.rig.repo.submissions if s.get("outcome") == "malformed"]
    assert len(mal) == 1 and mal[0]["sender_x_id"] == "42"
    assert mal[0]["submitted_claim_code"] == "50 tokens"
    assert t.contract not in repr(mal)                     # nunca o alvo no log


def test_shotgun_account_gets_one_reply_and_the_operator_hears_at_three():
    """P1-1: o orçamento do sys_sent é uma resposta de formato por perfil —
    uma conta que insiste não nos faz encher o fio; ao terceiro post
    malformado o operador é avisado uma vez, cada post fica registado."""
    from finding_memeland.target.templates import POST_REPLY_ONE_TOKEN
    w = World()
    hunt = w.launch()
    t0 = hunt.live_at
    w.src.reshared.add("42")
    links = [f"https://opensea.io/assets/ethereum/0x{i:040x}/1" for i in range(3)]
    w.src.schedule[1] = lambda: [
        post(4100 + k, "42", " ".join(links), t0 + timedelta(minutes=k + 1), hunt.reshare_post_id)
        for k in range(4)]
    w.orch._max_rounds = 3
    with pytest.raises(RuntimeError):
        w.orch._claim_loop(hunt)
    all_replies = [r for k in range(4) for r in replies_to(w.rig, 4100 + k)]
    assert all_replies == [POST_REPLY_ONE_TOKEN]                 # UMA por perfil
    mal = [s for s in w.rig.repo.submissions if s.get("outcome") == "malformed"]
    assert len(mal) == 4
    shots = [m for m in w.rig.notifier.messages if "shotgun posts from" in m]
    assert len(shots) == 1 and "@" in shots[0] and "no guess spent" in shots[0]
    assert "3 multi-token replies so far" in shots[0]           # a tally, said so


def test_oscillating_outage_trips_the_accumulated_hold_ceiling():
    """P1: 5h em baixo, 10 min em cima, 5h em baixo… nunca atinge o tecto
    do episódio (6h) — mas o prazo está congelado há um dia. O tecto
    ACUMULADO (12h) apanha a degradação."""
    w = World()
    w.ports.max_hold_s = 6 * 3600.0
    w.ports.max_total_hold_s = 12 * 3600.0
    hunt = w.launch()
    w.orch._hunt_timeout_h = None
    w.orch._clue_due_fn = lambda now: now
    cycle = {"n": 0}

    def flap(chain, contract, tid):                        # 300 ciclos down, 10 up
        cycle["n"] += 1
        if (cycle["n"] // 8) % 310 < 300:                   # 8 leituras por lote
            raise ChainUnavailable("flapping")
        return w.fetch_live(chain, contract, tid)

    w.ports.live_check = LiveCheck(read_live=flap, rng=random.Random(2))
    w.orch._max_rounds = 15 * 60                            # 15h a 60s/ciclo
    with pytest.raises(RuntimeError):
        w.orch._claim_loop(hunt)
    msgs = w.rig.notifier.messages
    assert any("ACCUMULATED" in m for m in msgs)
    assert any("hold released" in m for m in msgs)          # oscilou mesmo
    assert hunt.state is HuntState.LIVE


def test_clue_one_exhaustion_is_a_refusal_that_excludes_the_target():
    """5th --real-clues: six attempts on clue 1, all solver hits — in
    production that surfaced as '🚨 HUNT DIED'. Nothing was posted: it is a
    refusal. The target is excluded like a void; the hunt closes; the
    notices never name it."""
    from finding_memeland.target.integration import GoLiveRefused

    class _Exhausted:
        def next_clue(self, ctx, i, prior, **kw):
            raise RuntimeError("clue #1 failed guardrails after 6 attempts")
    w = World()
    w.ports.clue_engine = _Exhausted()
    with pytest.raises(GoLiveRefused) as e:
        w.launch()
    assert "unwritable" in str(e.value)
    hunt = next(iter(w.rig.repo.hunts.values()))
    assert hunt["target_void_id"] and hunt["target_void_cause"] == "unwritable"
    assert w.rig.publisher.posts == []                          # nothing public
    assert any("launch refused" in m and "excluded from the next draw" in m
               for m in w.rig.notifier.messages)
    assert all(hunt["target_void_id"].split(":")[1] not in m for m in w.rig.notifier.messages)
    assert w.orch._last_target_void_id == hunt["target_void_id"]


def test_guard_down_at_clue_one_refuses_without_excluding_the_target():
    from finding_memeland.target.clues import TruthJudgeUnavailable
    from finding_memeland.target.integration import GoLiveRefused

    class _Down:
        def next_clue(self, ctx, i, prior, **kw):
            raise TruthJudgeUnavailable("judge down")
    w = World()
    w.ports.clue_engine = _Down()
    with pytest.raises(GoLiveRefused) as e:
        w.launch()
    assert "guard unavailable" in str(e.value)
    hunt = next(iter(w.rig.repo.hunts.values()))
    assert not hunt.get("target_void_id")
    assert any("stays prepared" in m for m in w.rig.notifier.messages)
    assert w.rig.publisher.posts == []

