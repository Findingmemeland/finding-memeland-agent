"""6/6 — the dry-run scenarios run green offline on every commit. Each
scenario's checks are its own assertions; a failing one names itself."""

from __future__ import annotations

import pytest

from finding_memeland.orchestrator.state_machine import HuntState
from finding_memeland.target.dryrun import (
    MANDATORY,
    SCENARIOS,
    TargetWorld,
    _GuardOutageEngine,
    run_scenarios,
)


@pytest.mark.parametrize("name", list(SCENARIOS))
def test_scenario_is_green(name):
    rep = run_scenarios([name])[0]
    assert rep.ok, f"{name}: {rep.failures()}"


def test_mandatory_scenarios_are_the_three_opus_named():
    assert set(MANDATORY) == {"429", "void", "flap"}
    assert all(m in SCENARIOS for m in MANDATORY)


def test_live_check_ok_does_not_release_the_search_guards_hold():
    """Found by the dry-run (09/09): the live check passing every cycle
    released the guard's hold every cycle — 60-second episodes, never an
    hour on the ledger, re-notify and ceilings unreachable for a 429.
    Release is by cause now: the live check releases only its own hold."""
    w = TargetWorld()
    w.ports.max_hold_s = 3600.0
    w.ports.clue_engine = _GuardOutageEngine(w.ports.clue_engine, w.rig.clock,
                                             blind_s=10 * 3600)
    hunt = w.launch()
    w.orch._hunt_timeout_h = None
    w.orch._clue_due_fn = lambda now: now
    w.orch._max_rounds = 130                                # 2h10 at 60s/cycle
    with pytest.raises(RuntimeError):
        w.orch._claim_loop(hunt)
    msgs = w.notices()
    assert sum("hold released" in m for m in msgs) == 0     # never released mid-outage
    assert sum("⏸ HOLD (" in m for m in msgs) == 1          # ONE episode, not 130
    assert any("HOLD past MAX" in m and "EPISODE ceiling" in m for m in msgs)
    assert not any("episode: episode" in m for m in msgs)     # Opus: duplicated word
    assert hunt.target_hold.current_hold_seconds(w.rig.clock.now().timestamp()) > 3600
    assert hunt.state is HuntState.LIVE


def test_a_clue_going_out_releases_any_hold():
    w = TargetWorld()
    w.ports.clue_engine = _GuardOutageEngine(w.ports.clue_engine, w.rig.clock,
                                             blind_s=5 * 60)
    hunt = w.launch()
    w.orch._hunt_timeout_h = None
    w.orch._clue_due_fn = lambda now: now
    w.orch._max_rounds = 12
    with pytest.raises(RuntimeError):
        w.orch._claim_loop(hunt)
    msgs = w.notices()
    assert sum("⏸ HOLD (" in m for m in msgs) == 1
    assert sum("hold released" in m for m in msgs) == 1
    assert sum(1 for p in w.posts() if "Clue" in p) >= 2
