"""Depois das hunts #24 e #25 (23/10) — as decisões do Pedro.

  1. CADÊNCIA DA REVELAÇÃO: depois de o puzzle acabar — o fim do puzzle
     DESTA hunt (4, 6 ou 8 pistas), nunca "pista 8" — as pistas saem com um
     intervalo aleatório mais curto, 4–12 min por omissão, logo a partir da
     1.ª pista de revelação. O puzzle fica exactamente como está.
  2. LINKS DE COLECÇÃO PELO NOME: opensea.io/collection/<slug> recebia "i
     can't read that link — my side, not yours" (visto na #23). Recebe a
     resposta de colecção que os outros links de colecção já recebem, e não
     gasta tentativa (nunca gastou: uma resposta de formato não é palpite).
     Só o OpenSea: os outros mercados ficam como estão.
"""
from __future__ import annotations

import inspect
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest
from test_format_replies import GOOD, _matcher, _row, _run, _thread

from finding_memeland.content.clue_engine import ClueRoundRefused, next_clue_due_factory
from finding_memeland.content.relic_clues import puzzle_clues_of
from finding_memeland.orchestrator.state_machine import Orchestrator
from finding_memeland.target.claim import collection_link
from finding_memeland.target.dryrun import TargetWorld, replies_to
from finding_memeland.target.templates import (
    POST_REPLY_COLLECTION_LINK,
    POST_REPLY_UNKNOWN_CHAIN,
)

PUZZLE_GAP = timedelta(minutes=20)
REVEAL_GAP = timedelta(minutes=8)


def world_with_bands() -> TargetWorld:
    """O mundo do dry-run com duas janelas fixas, para se ver qual saiu."""
    w = TargetWorld()
    w.orch._clue_due_fn = lambda now: now + PUZZLE_GAP
    w.orch._reveal_due_fn = lambda now: now + REVEAL_GAP
    return w


def fake_target_hunt(n: int):
    return SimpleNamespace(target=object(),
                           ctx=SimpleNamespace(clue_facet_plan=[("name_word_1", 0.9)] * n))


# --------------------------------------------------------------------------- #
# 1. A cadência                                                                 #
# --------------------------------------------------------------------------- #


def test_the_band_is_chosen_by_the_next_clue():
    w = world_with_bands()
    hunt = w.launch()
    n = puzzle_clues_of(hunt.ctx)
    now = w.rig.clock.now()
    for nxt in range(2, n + 4):
        assert w.orch._clue_due_for(hunt, nxt) - now == (
            REVEAL_GAP if nxt > n else PUZZLE_GAP), nxt


@pytest.mark.parametrize("n", [4, 6, 8])
def test_the_end_of_the_puzzle_is_this_hunts_never_clue_8(n):
    w = world_with_bands()
    now = w.rig.clock.now()
    assert w.orch._clue_due_for(fake_target_hunt(n), n) - now == PUZZLE_GAP
    assert w.orch._clue_due_for(fake_target_hunt(n), n + 1) - now == REVEAL_GAP


def test_the_first_reveal_clue_already_comes_on_the_short_band():
    """Pela pista real: cada pista sai quando é devida, e a espera que se
    marca a seguir é a da pista seguinte. A espera ANTES da última do puzzle
    é a de sempre; a que vem a seguir a ela — antes da 1.ª de revelação —
    já é a curta (a opção 1 do Pedro)."""
    w = world_with_bands()
    hunt = w.launch()
    n = puzzle_clues_of(hunt.ctx)
    idx, due, gaps = 1, w.rig.clock.now(), {}
    while idx < n + 2:
        new_idx, due = w.orch._maybe_post_clue(hunt, idx, due)
        assert new_idx == idx + 1, idx                    # the clue went out
        gaps[new_idx + 1] = due - w.rig.clock.now()
        w.rig.clock.sleep((due - w.rig.clock.now()).total_seconds())
        idx = new_idx
    assert [gaps[k] for k in range(3, n + 1)] == [PUZZLE_GAP] * (n - 2)
    assert gaps[n + 1] == gaps[n + 2] == gaps[n + 3] == REVEAL_GAP
    assert len(hunt.clues) == n + 2


def test_a_hold_in_the_reveal_waits_the_short_band(monkeypatch):
    import finding_memeland.target.integration as integ
    w = world_with_bands()
    hunt = w.launch()
    n = puzzle_clues_of(hunt.ctx)
    monkeypatch.setattr(integ, "pre_clue_live_check", lambda orch, h, k: integ.PRE_CLUE_SKIP)
    now = w.rig.clock.now()
    assert w.orch._maybe_post_clue(hunt, n, now) == (n, now + REVEAL_GAP)
    assert w.orch._maybe_post_clue(hunt, n - 1, now) == (n - 1, now + PUZZLE_GAP)


@pytest.mark.parametrize("held", [True, False])
def test_a_failed_reveal_clue_is_tried_again_on_the_short_band(monkeypatch, held):
    import finding_memeland.target.integration as integ
    w = world_with_bands()
    hunt = w.launch()
    n = puzzle_clues_of(hunt.ctx)

    def boom(*_a, **_k):
        raise RuntimeError("down")
    monkeypatch.setattr(w.orch._engine_for(hunt), "next_clue", boom)
    monkeypatch.setattr(integ, "clue_failed", lambda orch, h, e: held)
    now = w.rig.clock.now()
    assert w.orch._maybe_post_clue(hunt, n, now) == (n, now + REVEAL_GAP)
    assert w.orch._maybe_post_clue(hunt, n - 1, now) == (n - 1, now + PUZZLE_GAP)


def test_a_refused_round_keeps_its_quick_retries_then_waits_its_phases_band():
    w = world_with_bands()
    hunt = w.launch()
    n = puzzle_clues_of(hunt.ctx)
    now = w.rig.clock.now()
    for clue, band in ((n + 1, REVEAL_GAP), (n, PUZZLE_GAP)):
        refused = ClueRoundRefused("x", clue_index=clue, attempts=6, reasons=1)
        dues = [w.orch._after_refused_round(hunt, refused) for _ in range(3)]
        assert dues[:2] == [now, now], clue               # the two quick rounds stay
        assert dues[2] - now == band, clue


def test_every_wait_between_clues_goes_through_one_place():
    """Os dois ciclos ao (re)começar, depois de uma pista, de uma pausa e de
    uma ronda recusada: nenhum sorteia a espera por fora."""
    src = inspect.getsource(Orchestrator)
    assert src.count("self._clue_due_fn(") == 1
    assert src.count("self._reveal_due_fn(") == 1
    assert src.count("next_due = self._clue_due_for(hunt, clue_index + 1)") == 2


def test_a_hunt_that_is_not_a_target_keeps_one_band():
    w = world_with_bands()
    now = w.rig.clock.now()
    relic = SimpleNamespace(target=None, ctx=SimpleNamespace(clue_facet_plan=[("x", 0.9)] * 4))
    assert w.orch._clue_due_for(relic, 50) - now == PUZZLE_GAP


def test_without_a_reveal_band_everything_is_as_it_was():
    w = TargetWorld()
    w.orch._clue_due_fn = lambda now: now + PUZZLE_GAP
    hunt = w.launch()
    now = w.rig.clock.now()
    assert w.orch._reveal_due_fn is None
    assert {w.orch._clue_due_for(hunt, k) - now for k in range(2, 12)} == {PUZZLE_GAP}


def test_the_default_band_is_4_to_12_minutes():
    from finding_memeland.config import Settings
    fields = Settings.model_fields
    assert fields["clue_reveal_min_gap_s"].default == 4 * 60
    assert fields["clue_reveal_max_gap_s"].default == 12 * 60


def test_the_reveal_band_draws_fresh_between_its_ends():
    due = next_clue_due_factory(240, 720)
    now = datetime(2026, 10, 23, tzinfo=UTC)
    gaps = [(due(now) - now).total_seconds() for _ in range(400)]
    assert min(gaps) >= 240 and max(gaps) <= 720
    assert min(gaps) < 360 and max(gaps) > 600           # a band, not an alarm clock


@pytest.mark.parametrize("lo, hi", [(720, 240), (0, 720), (240, -1)])
def test_a_bad_reveal_band_refuses_when_the_bot_is_built(lo, hi):
    with pytest.raises(ValueError):
        next_clue_due_factory(lo, hi)


def test_main_builds_the_reveal_band_and_shows_it_in_status():
    from finding_memeland import main
    src = inspect.getsource(main.build_agent)
    assert ("reveal_due_fn=next_clue_due_factory(\n"
            "            s.clue_reveal_min_gap_s, s.clue_reveal_max_gap_s)") in src
    assert "reveal_min_gap_s=s.clue_reveal_min_gap_s" in src
    assert "reveal_max_gap_s=s.clue_reveal_max_gap_s" in src


def test_status_shows_both_bands_and_the_same_worst_case():
    from finding_memeland.runtime import cadence_status_line
    before = cadence_status_line(360, 1560, 24)
    now = cadence_status_line(360, 1560, 24, reveal_min_gap_s=240, reveal_max_gap_s=720)
    assert now.startswith("clues: 6-26min · revelação 4-12min → worst case ")
    assert now.split("→", 1)[1] == before.split("→", 1)[1]
    assert before.startswith("clues: 6-26min → worst case ")


def test_a_reveal_band_longer_than_the_puzzles_moves_the_bound_up_never_down():
    from finding_memeland.runtime import cadence_status_line
    longer = cadence_status_line(360, 1560, 24, reveal_min_gap_s=600, reveal_max_gap_s=3600)
    assert longer.split("→", 1)[1] == cadence_status_line(360, 3600, 24).split("→", 1)[1]


# --------------------------------------------------------------------------- #
# 2. Links de colecção pelo nome                                                #
# --------------------------------------------------------------------------- #

SLUGS = [
    "https://opensea.io/collection/some-art-drops",
    "https://www.opensea.io/collection/some-art-drops",
    "http://opensea.io/collection/some-art-drops/overview",
    "https://opensea.io/collection/some-art-drops?search[query]=x",
    "https://OpenSea.io/collection/Some-Art-Drops",
]


@pytest.mark.parametrize("url", SLUGS)
def test_an_opensea_collection_by_name_is_a_collection(url):
    assert collection_link(url)


@pytest.mark.parametrize("url", [
    f"https://opensea.io/item/base/{GOOD}/5",
    f"https://opensea.io/assets/base/{GOOD}/5",
    "https://opensea.io/collections",
    "https://opensea.io/collection/",
    "https://opensea.io/account",
    "https://notopensea.io/collection/x",
    "https://opensea.io.example.com/collection/x",
    # the other marketplaces stay as they were (Pedro, 23/10)
    "https://magiceden.io/collections/ethereum/some-slug",
    "https://foundation.app/collection/some-slug",
    "https://superrare.com/artwork/eth/some-slug",
])
def test_everything_else_is_as_it_was(url):
    assert not collection_link(url)


@pytest.mark.parametrize("url", SLUGS)
def test_the_post_gets_the_collection_reply_that_already_exists(url):
    m = _matcher(unknown_chain_reply=POST_REPLY_UNKNOWN_CHAIN,
                 larder_chains=frozenset({"ethereum", "base"}))
    for text in (url, f"found it {url}"):
        assert m.format_kind(text) == "collection_link", text
        assert m.format_hint(text) == POST_REPLY_COLLECTION_LINK, text


def test_in_the_thread_it_is_answered_logged_as_format_and_costs_no_guess():
    w = TargetWorld()
    hunt = w.launch()
    _thread(w, hunt, (8001, "61", SLUGS[0]), (8002, "62", f"look: {SLUGS[3]}"))
    _run(w, hunt)
    for tid in (8001, 8002):
        assert replies_to(w.rig, tid) == [POST_REPLY_COLLECTION_LINK]
        row = _row(w, tid)
        assert (row["outcome"], row["submitted_claim_code"]) == ("format", "collection_link")
    assert w.orch._rebuild_claim_state(hunt)[1] == {}        # nobody spent a guess
