"""Depois das hunts #31 e #32 — pedido do Pedro a 05/11, aprovado a 06/11.

Quatro pontos, três commits. Estes dois vão juntos:

1. CLEANUP DE 30 MIN. A janela entre o anúncio do vencedor e o fecho passa de
   1 h para 30 min (`CLEANUP_WINDOW_SECONDS`). Medido antes de mexer: a
   constante só é lida no `_reveal` (a hora de fecho gravada e a espera); a
   produção usa o valor por omissão (o main.py não o passa, nada no
   Doppler); os 5 min do "closed" contam a partir da hora gravada; a retoma
   no arranque lê a hora gravada, por isso uma hunt que já estava em cleanup
   fecha à hora que tinha. Durante a janela, /launch, /prepare, /fill e
   /harvest continuam recusados (`active_hunt_guard`).

4. TEXTO DO TIMEOUT DA CARTEIRA. "submission timed out. failed to send
   wallet. miss your window → next in line." passa a "window closed — no
   wallet in time. the prize moves to the next in line."
"""
from __future__ import annotations

import importlib.util
import inspect
from datetime import datetime, timedelta
from pathlib import Path

from test_claim_by_post import WALLET_B, _code, _post, _rig
from test_format_replies import _public_replies
from test_retire_never_dies import _restart_with, closed_posts

from finding_memeland.content.templates import POST_REPLY_TIMED_OUT
from finding_memeland.orchestrator.state_machine import (
    CLEANUP_WINDOW_SECONDS,
    HuntState,
    Orchestrator,
)
from finding_memeland.runtime import active_hunt_guard
from finding_memeland.target.dryrun import WALLET_A, TargetWorld, post

THIRTY_MIN = timedelta(minutes=30)


def _source(module: str) -> str:
    return Path(importlib.util.find_spec(module).origin).read_text()


def _won(world: TargetWorld):
    """A target hunt won and paid, right before the reveal."""
    hunt = world.launch()
    t0 = hunt.live_at
    target = hunt.target.target
    world.src.reshared.add("42")
    world.src.schedule[5] = lambda: [                # as in the dry-run's happy path
        post(1010, "42", f"{target.chain}:{target.contract}:{target.token_id}",
             t0 + timedelta(minutes=5), hunt.reshare_post_id)]
    world.src.schedule[8] = lambda: [
        post(1040, "42", WALLET_A, t0 + timedelta(minutes=8),
             world.rig.repo.hunts[hunt.id].get("pending_ask_tweet_id"))]
    winner = world.orch._claim_loop(hunt)
    return hunt, winner, world.orch._pay(hunt, winner)


# --------------------------------------------------------------------------- #
# 1. Cleanup de 30 min                                                          #
# --------------------------------------------------------------------------- #


def test_the_reveal_window_is_thirty_minutes():
    assert CLEANUP_WINDOW_SECONDS == 30 * 60
    default = inspect.signature(Orchestrator.__init__).parameters["cleanup_window_s"].default
    assert default == CLEANUP_WINDOW_SECONDS


def test_production_takes_the_default_nothing_to_set_in_doppler():
    """Medido: só o ensaio ao vivo (live_test) passa outra janela."""
    for module in ("finding_memeland.main", "finding_memeland.config"):
        assert "cleanup_window" not in _source(module), module
    assert "cleanup_window_s=" in _source("finding_memeland.live_test")


def test_a_win_stores_the_close_thirty_minutes_after_the_reveal_and_waits_for_it():
    w = TargetWorld()
    hunt, winner, receipt = _won(w)
    slept = []
    real_sleep = w.rig.clock.sleep
    w.rig.clock.sleep = lambda s: (slept.append(s), real_sleep(s))[1]
    w.orch._reveal(hunt, winner, receipt)
    row = w.rig.repo.hunts[hunt.id]
    assert row["state"] == "pending_cleanup"
    assert row["cleanup_due_at"] - row["resolved_at"] == THIRTY_MIN
    assert slept[-1] == 30 * 60                     # the wait after the announcement


def test_the_commands_are_refused_during_the_window_and_free_after_it():
    w = TargetWorld()
    hunt, winner, receipt = _won(w)
    revealed_at = w.rig.clock.now()
    w.orch._reveal(hunt, winner, receipt)
    assert "pending_cleanup" in active_hunt_guard(w.rig.repo)
    w.orch._retire(hunt)
    assert hunt.state is HuntState.DONE
    assert active_hunt_guard(w.rig.repo) is None
    closed_after = w.rig.clock.now() - revealed_at
    assert THIRTY_MIN <= closed_after < timedelta(minutes=40), closed_after
    assert len(closed_posts(w.posts())) == 1


def test_a_hunt_already_in_cleanup_closes_at_the_time_it_stored():
    """O caso da #32: em cleanup com a hora antiga (1 h) gravada. Num
    reinício fecha à hora gravada — a constante nova não a encurta."""
    rig, row = _restart_with("pending_cleanup", timedelta(minutes=50))
    due = datetime.fromisoformat(row["cleanup_due_at"])
    assert rig.clock.now() >= due
    assert len(closed_posts(rig.publisher.posts)) == 1
    assert row["state"] == "done"


def test_the_closed_post_window_still_counts_from_the_stored_time():
    assert Orchestrator.CLOSED_POST_WINDOW_S == 5 * 60
    rig, row = _restart_with("pending_cleanup", timedelta(minutes=-6))  # 6 min late
    assert closed_posts(rig.publisher.posts) == [] and row["state"] == "done"


# --------------------------------------------------------------------------- #
# 4. Texto do timeout da carteira                                               #
# --------------------------------------------------------------------------- #


def test_the_timeout_text_is_the_approved_one_word_for_word():
    """Pedro, 05/11."""
    assert POST_REPLY_TIMED_OUT == (
        "window closed — no wallet in time. the prize moves to the next in line.")
    assert "\n" not in POST_REPLY_TIMED_OUT
    assert len(POST_REPLY_TIMED_OUT) <= 280
    # the test of every public reply (rule of 09/10) walks this one too
    assert _public_replies()["POST_REPLY_TIMED_OUT"] == POST_REPLY_TIMED_OUT


def test_the_timeout_answers_our_wallet_ask_and_the_next_in_line_gets_the_prize():
    rig, orch, hunt, src = _rig()
    sent = []                                       # (in_reply_to, text, id of the reply)
    real_reply = rig.publisher.reply_post

    def reply_post(text, *, in_reply_to):
        tid = real_reply(text, in_reply_to=in_reply_to)
        sent.append((in_reply_to, text, tid))
        return tid
    rig.publisher.reply_post = reply_post
    t0 = hunt.live_at
    src.reshared |= {"42", "77"}
    src.schedule[1] = lambda: [
        _post(7010, "42", f"code {_code(rig)}", t0 + timedelta(minutes=1),
              hunt.reshare_post_id)]
    src.schedule[6] = lambda: [
        _post(7020, "77", f"mine {_code(rig)}", rig.clock.now(), hunt.reshare_post_id)]
    src.schedule[18] = lambda: [
        _post(7050, "77", WALLET_B, rig.clock.now(),
              rig.repo.hunts[hunt.id].get("pending_ask_tweet_id"))]
    winner = orch._claim_loop(hunt)
    assert winner.submission.sender_x_id == "77" and winner.wallet == WALLET_B
    ask_42 = [tid for to, t, tid in sent if to == "7010" and t.startswith("you found me")]
    ask_77 = [tid for to, t, tid in sent if to == "7020" and t.startswith("you found me")]
    assert len(ask_42) == 1 and len(ask_77) == 1          # 77 got its own ask
    timed = [(to, t) for to, t, _tid in sent if t == POST_REPLY_TIMED_OUT]
    assert timed == [(ask_42[0], "window closed — no wallet in time. "
                                 "the prize moves to the next in line.")]
