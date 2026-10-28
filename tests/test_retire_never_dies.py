"""O fim de uma hunt nunca a mata (Pedro, 26/10).

O QUE ACONTECEU, na #24 e na #25, seguidas: a hunt estava paga e anunciada;
no último passo (RETIRING) um ReadTimeout subiu até ao fim do `main` —
"HUNT DIED". A hunt ficou 'retiring' na base, a bloquear o /prepare até SQL
à mão, e o post "Hunt #N closed…" não saiu. Lido no código: o passo lê as
submissões (Supabase, httpx), publica o post (X, tweepy + requests) e passa
a DONE — sem nada à volta. E, num reinício, uma hunt 'retiring' (ou
'pending_cleanup' fora de horas) publicava o "closed" TARDE.

As decisões do Pedro:
  · um timeout neste passo não mata a hunt nem a deixa presa: chega a DONE;
  · o post "closed": tenta durante poucos minutos e depois desiste, com uma
    linha calma no Telegram — nunca o publica horas depois.
"""
from __future__ import annotations

from datetime import timedelta

import httpx
import pytest
import requests

from finding_memeland.orchestrator.simulation import build_simulation
from finding_memeland.orchestrator.state_machine import HuntState, Orchestrator
from finding_memeland.target.dryrun import TargetWorld

GAVE_UP = "o post 'closed' não saiu"


def retiring_world():
    """Uma hunt de alvo paga e anunciada, à porta do RETIRING."""
    w = TargetWorld()
    hunt = w.launch()
    hunt.state = HuntState.PENDING_CLEANUP
    w.rig.repo.hunts[hunt.id]["state"] = "pending_cleanup"
    return w, hunt


def closed_posts(posts) -> list[str]:
    return [p for p in posts if " closed. " in p]


class Scripted:
    """Stands in for a call: raises the scripted errors in order, then calls
    through. Records WHEN each attempt started; each one can take time."""

    def __init__(self, real, errors, clock, cost_s: float = 0):
        self.real, self.errors, self.clock, self.cost_s = real, list(errors), clock, cost_s
        self.started = []

    def __call__(self, *a, **k):
        self.started.append(self.clock.now())
        if self.cost_s:
            self.clock.sleep(self.cost_s)
        if self.errors:
            raise self.errors.pop(0)
        return self.real(*a, **k)


TIMEOUTS = [
    ("post", requests.exceptions.ReadTimeout("read timed out")),   # X: tweepy + requests
    ("db", httpx.ReadTimeout("timed out")),                         # Supabase: httpx
]


# --------------------------------------------------------------------------- #
# 1. Ao vivo                                                                    #
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("where, exc", TIMEOUTS)
def test_a_timeout_never_kills_the_hunt_nor_leaves_it_stuck(where, exc):
    w, hunt = retiring_world()
    target = ((w.orch._publisher, "post") if where == "post"
              else (w.orch._repo, "submissions_for_hunt"))
    call = Scripted(getattr(*target), [exc] * 9, w.rig.clock)
    setattr(*target, call)
    t0 = w.rig.clock.now()
    w.orch._retire(hunt)                                   # never raises
    assert hunt.state is HuntState.DONE
    assert w.rig.repo.hunts[hunt.id]["state"] == "done"    # /prepare is free
    assert closed_posts(w.rig.publisher.posts) == []
    assert len(call.started) == 3
    assert all(t <= t0 + timedelta(minutes=5) for t in call.started)
    assert [m for m in w.rig.notifier.messages if GAVE_UP in m] == [
        f"hunt #{hunt.number}: o post 'closed' não saiu (ReadTimeout) — desisti, "
        "não volto a tentar."]
    assert not any("HUNT DIED" in m for m in w.rig.notifier.messages)


def test_the_post_goes_out_at_the_second_attempt():
    w, hunt = retiring_world()
    w.orch._publisher.post = Scripted(w.orch._publisher.post,
                                      [requests.exceptions.ReadTimeout("x")], w.rig.clock)
    w.orch._retire(hunt)
    [post] = closed_posts(w.rig.publisher.posts)
    assert post.startswith(f"Hunt #{hunt.number} closed. ")
    assert post.endswith(" submissions logged for public audit.")
    assert hunt.state is HuntState.DONE
    assert not any(GAVE_UP in m for m in w.rig.notifier.messages)


def test_no_attempt_starts_after_the_window():
    """Cada tentativa a demorar 200 s: a 1.ª às 0 s, a 2.ª aos 260 s; a 3.ª
    começaria aos 580 s, fora dos 5 min — não começa."""
    w, hunt = retiring_world()
    call = Scripted(w.orch._publisher.post, [requests.exceptions.ReadTimeout("x")] * 9,
                    w.rig.clock, cost_s=200)
    w.orch._publisher.post = call
    t0 = w.rig.clock.now()
    w.orch._retire(hunt)
    assert [(t - t0).total_seconds() for t in call.started] == [0, 260]
    assert hunt.state is HuntState.DONE
    # and it does not sit out a wait the window has no room for: DONE at
    # once, when the 2nd attempt fails (460 s), not after another 120 s
    assert (w.rig.clock.now() - t0).total_seconds() == 460


def test_a_duplicate_refusal_after_a_timeout_means_it_went_out():
    """O timeout pode chegar com o post já publicado; o X recusa o mesmo texto
    duas vezes. Isso é "saiu" — não se tenta mais e não se diz que falhou."""
    w, hunt = retiring_world()
    call = Scripted(w.orch._publisher.post, [
        requests.exceptions.ReadTimeout("x"),
        RuntimeError("403 Forbidden — You are not allowed to create a Tweet with "
                     "duplicate content.")], w.rig.clock)
    w.orch._publisher.post = call
    w.orch._retire(hunt)
    assert len(call.started) == 2
    assert not any(GAVE_UP in m for m in w.rig.notifier.messages)
    assert hunt.state is HuntState.DONE


def _db_failing(w, *, state: str, times: int):
    real = w.rig.repo.set_hunt_state
    left = {"n": times}

    def set_hunt_state(hunt_id, value, **fields):
        if value == state and left["n"]:
            left["n"] -= 1
            raise httpx.ReadTimeout("timed out")
        return real(hunt_id, value, **fields)
    w.orch._repo.set_hunt_state = set_hunt_state


def test_done_is_written_even_when_the_db_blinks():
    w, hunt = retiring_world()
    _db_failing(w, state="done", times=1)
    w.orch._retire(hunt)
    assert w.rig.repo.hunts[hunt.id]["state"] == "done"
    assert hunt.state is HuntState.DONE
    assert len(closed_posts(w.rig.publisher.posts)) == 1
    assert any(m.startswith(f"hunt #{hunt.number} done;") for m in w.rig.notifier.messages)


def test_a_db_that_stays_down_is_reported_not_raised():
    w, hunt = retiring_world()
    _db_failing(w, state="done", times=99)
    w.orch._retire(hunt)                                   # never raises
    assert w.rig.repo.hunts[hunt.id]["state"] == "retiring"
    assert any("não consegui marcar DONE na base (ReadTimeout)" in m
               and "no próximo arranque fecha sozinha, sem post" in m
               for m in w.rig.notifier.messages)
    assert not any(m.startswith(f"hunt #{hunt.number} done;") for m in w.rig.notifier.messages)


def test_the_retiring_write_failing_still_closes_the_hunt():
    w, hunt = retiring_world()
    _db_failing(w, state="retiring", times=1)
    w.orch._retire(hunt)
    assert w.rig.repo.hunts[hunt.id]["state"] == "done"
    assert len(closed_posts(w.rig.publisher.posts)) == 1
    assert any("'retiring' não ficou gravado (ReadTimeout)" in m for m in w.rig.notifier.messages)


def test_a_clean_retire_is_what_it_was():
    w, hunt = retiring_world()
    w.orch._retire(hunt)
    assert len(closed_posts(w.rig.publisher.posts)) == 1
    assert hunt.state is HuntState.DONE
    assert not any(GAVE_UP in m for m in w.rig.notifier.messages)


# --------------------------------------------------------------------------- #
# 2. Num reinício                                                               #
# --------------------------------------------------------------------------- #


def _restart_with(state: str, due_from_now: timedelta | None):
    rig1 = build_simulation()
    due = (rig1.clock.now() + due_from_now).isoformat() if due_from_now is not None else None
    hid = rig1.repo.create_hunt(
        persona_id="persona-1", claim_code="AB2CD3EF", integrity_salt="s",
        integrity_hash="h", prize_fmml=1000, min_balance_fmml=10, holding_hours=24,
        state=state, hunt_number=31, cleanup_due_at=due)
    rig2 = build_simulation(repo=rig1.repo)
    rig2.orchestrator.resume_hunts()
    return rig2, rig1.repo.hunts[hid]


@pytest.mark.parametrize("state", ["retiring", "pending_cleanup"])
@pytest.mark.parametrize("late", [timedelta(hours=-3), timedelta(minutes=-6)])
def test_a_late_restart_closes_the_hunt_without_the_post(state, late):
    rig, row = _restart_with(state, late)
    assert closed_posts(rig.publisher.posts) == []
    assert row["state"] == "done"
    assert any("hunt #31: o post 'closed' não saiu (fora da janela de 5 min) — "
               "desisti, não volto a tentar." == m for m in rig.notifier.messages)


def test_a_restart_without_a_due_time_never_posts():
    rig, row = _restart_with("retiring", None)
    assert closed_posts(rig.publisher.posts) == [] and row["state"] == "done"


def test_a_restart_inside_the_window_still_posts():
    rig, row = _restart_with("retiring", timedelta(minutes=-1))
    assert closed_posts(rig.publisher.posts) == [
        "Hunt #31 closed. 0 submissions logged for public audit."]
    assert row["state"] == "done"


def test_pending_cleanup_before_its_time_waits_then_posts():
    rig, row = _restart_with("pending_cleanup", timedelta(minutes=10))
    assert len(closed_posts(rig.publisher.posts)) == 1
    assert row["state"] == "done"


def test_a_persona_that_cannot_be_marked_retired_does_not_stop_the_close():
    """Hunts de persona: a marcação da persona também é uma escrita na base."""
    rig1 = build_simulation()
    hid = rig1.repo.create_hunt(
        persona_id="persona-1", claim_code="AB2CD3EF", integrity_salt="s",
        integrity_hash="h", prize_fmml=1000, min_balance_fmml=10, holding_hours=24,
        state="retiring", hunt_number=32,
        cleanup_due_at=rig1.clock.now().isoformat())
    rig2 = build_simulation(repo=rig1.repo)

    def down(_pid):
        raise httpx.ReadTimeout("timed out")
    rig2.orchestrator._persona_source.mark_retired = down
    rig2.orchestrator.resume_hunts()
    assert rig1.repo.hunts[hid]["state"] == "done"
    assert len(closed_posts(rig2.publisher.posts)) == 1
    assert any("not marked retired (ReadTimeout)" in m for m in rig2.notifier.messages)


def test_the_window_and_the_waits_are_the_approved_ones():
    """Poucos minutos (5), três tentativas a 1 e 2 min."""
    assert Orchestrator.CLOSED_POST_WINDOW_S == 300
    assert Orchestrator.CLOSED_POST_WAITS_S == (60, 120)
