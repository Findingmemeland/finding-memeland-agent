"""Hunt #2 P2 — prepare/go-live split with the T-24h prep window.

/launch dresses the persona and opens the window (persona posts its own anchor
posts; X gets time to index); Clue 1 fires at golive_due_at. The operator can
/abort_prep or /delay_golive — both persisted on the hunt row (DB doctrine).
The gate: prep-window submissions are rejected + logged as 'early', NEVER
silently dropped, NEVER able to win, and NEVER feed the assembler.
"""

from datetime import timedelta

from finding_memeland.orchestrator.ports import Submission
from finding_memeland.orchestrator.simulation import (
    FakePersonaPostEngine,
    build_simulation,
)
from finding_memeland.orchestrator.state_machine import HuntState


def _prep_rig(window_h=0.1, win_after_polls=3):
    rig = build_simulation(win_after_polls=win_after_polls)
    orch = rig.orchestrator
    orch._prep_window_h = window_h          # 6 min window; FakeClock ticks fast
    orch._persona_post_engine = FakePersonaPostEngine()
    return rig, orch


def test_prep_window_publishes_posts_then_goes_live():
    rig, orch = _prep_rig()
    hunt = orch.run_hunt()
    assert hunt.state == HuntState.DONE
    # Prep posts were generated, scheduled, published AS the persona, marked.
    assert len(rig.dresser.persona_posts) == 3
    rows = rig.repo.persona_posts_for_hunt(hunt.id)
    assert len(rows) == 3 and all(r.get("posted_at") for r in rows)
    # Clue 1 went out only after the window (hunt still completed and paid).
    assert len(rig.payout.sent) == 1
    assert any("PREPPED" in m for m in rig.notifier.messages)


def test_anchor_posts_reach_the_clue_context():
    rig, orch = _prep_rig()
    hunt = orch._prepare(200)
    assert hunt.ctx.anchor_posts, "clues must be able to point at real posts"
    assert len(hunt.ctx.anchor_posts) == 3


def test_abort_prep_voids_without_firing_clue_one():
    rig, orch = _prep_rig(window_h=10)  # long window; abort will end it
    hunt = orch._prepare(200)

    # Operator sends /abort_prep after 2 ticks: the flag is a DB row field.
    real_get = rig.repo.get_hunt
    ticks = {"n": 0}

    def get_hunt(hid):
        ticks["n"] += 1
        row = real_get(hid)
        if ticks["n"] >= 2:
            row["abort_prep"] = True
        return row

    rig.repo.get_hunt = get_hunt
    proceeded = orch._prep_window(hunt)
    assert proceeded is False
    assert rig.repo.hunts[hunt.id]["state"] == "done"
    assert rig.dresser.retired                      # persona undressed
    assert hunt.persona.id in rig.persona_source.retired
    assert not any("is live" in p.lower() for p in rig.publisher.posts)  # no Clue 1
    assert any("ABORTED" in m for m in rig.notifier.messages)


def test_delay_golive_is_respected_from_the_db():
    rig, orch = _prep_rig(window_h=0.05)  # 3 min
    hunt = orch._prepare(200)

    # After the first tick, the operator pushes go-live by 6 more minutes.
    real_get = rig.repo.get_hunt
    state = {"delayed": False, "ticks": 0}

    def get_hunt(hid):
        state["ticks"] += 1
        row = real_get(hid)
        if state["ticks"] == 2 and not state["delayed"]:
            state["delayed"] = True
            due = row.get("golive_due_at")
            rig.repo.update_hunt(hid, golive_due_at=due + timedelta(minutes=6))
            row = real_get(hid)
        return row

    rig.repo.get_hunt = get_hunt
    t0 = rig.clock.now()
    assert orch._prep_window(hunt) is True
    waited = (rig.clock.now() - t0).total_seconds()
    assert waited >= 6 * 60, "go-live must respect the DELAYED due time from the DB"


def test_early_submission_is_logged_rejected_and_replied_never_wins():
    rig, orch = _prep_rig(window_h=0.1)
    hunt = orch._prepare(200)
    prep_start = hunt.started_at
    proceeded = orch._prep_window(hunt)
    assert proceeded
    orch._go_live(hunt)

    code = rig.repo.latest_claim_code()
    early_at = prep_start + timedelta(seconds=60)      # inside the prep window
    assert early_at < hunt.live_at

    class Source:
        def __init__(self):
            self.n = 0

        def poll(self, since):
            self.n += 1
            if self.n == 1:
                # A perfect submission — but sent BEFORE Clue 1 existed.
                return [Submission(dm_id="900", sender_x_id="4242",
                                   sender_handle="early_bird",
                                   body=f"code {code} wallet 0x{'c' * 40}",
                                   created_at=early_at)]
            if self.n == 3:
                return [Submission(dm_id="901", sender_x_id="9001",
                                   sender_handle="fair_winner",
                                   body=f"code {code} wallet 0x{'a' * 40}",
                                   created_at=hunt.live_at + timedelta(minutes=1))]
            return []

    orch._dm_source = Source()
    winner = orch._clue_and_dm_loop(hunt)
    # The early bird did NOT win — the post-T0 player did.
    assert winner.submission.sender_x_id == "9001"
    rows = [s for s in rig.repo.submissions if s["sender_x_id"] == "4242"]
    assert rows and rows[0]["outcome"] == "early"      # logged, never lost
    replies = [t for rid, t in rig.publisher.dm_replies if rid == "4242"]
    assert replies and "hasn't started" in replies[0]  # explicitly told


def test_windows_are_watertight_early_code_cannot_complete_after_t0():
    """A code sent in the prep window must NOT pair with a wallet sent after
    T0 — each window is sealed; the game starts at Clue 1 for everyone."""
    rig, orch = _prep_rig(window_h=0.1)
    hunt = orch._prepare(200)
    prep_start = hunt.started_at
    assert orch._prep_window(hunt)
    orch._go_live(hunt)
    code = rig.repo.latest_claim_code()

    class Source:
        def __init__(self):
            self.n = 0

        def poll(self, since):
            self.n += 1
            if self.n == 1:  # code alone, sent DURING prep
                return [Submission(dm_id="900", sender_x_id="4242",
                                   sender_handle="early_bird",
                                   body=f"psst: {code}",
                                   created_at=prep_start + timedelta(seconds=90))]
            if self.n == 3:  # wallet alone, sent AFTER go-live
                return [Submission(dm_id="901", sender_x_id="4242",
                                   sender_handle="early_bird",
                                   body=f"my wallet 0x{'c' * 40}",
                                   created_at=hunt.live_at + timedelta(minutes=1))]
            return []

    orch._dm_source = Source()
    orch._max_rounds = 6
    try:
        orch._clue_and_dm_loop(hunt)
    except RuntimeError:
        pass  # no winner — exactly the point
    outcomes = {s["dm_id"]: s["outcome"] for s in rig.repo.submissions}
    assert outcomes.get("900") == "early"
    assert outcomes.get("901") == "partial"            # wallet alone, code owed
    assert not rig.payout.sent                          # nobody won


def test_resume_prepped_hunt_continues_to_golive_and_completes():
    rig1, orch1 = _prep_rig(window_h=0.1)
    hunt1 = orch1._prepare(200)
    # Crash right after entering the window: state 'prepped' in the DB.
    orch1._transition(hunt1, HuntState.PREPPED,
                      golive_due_at=rig1.clock.now() + timedelta(minutes=6))

    rig2 = build_simulation(repo=rig1.repo)
    orch2 = rig2.orchestrator
    orch2._prep_window_h = 0.1
    orch2._persona_post_engine = FakePersonaPostEngine()
    resumed = orch2.resume_hunts()
    assert resumed == 1
    assert rig1.repo.hunts[hunt1.id]["state"] == "done"
    assert len(rig2.payout.sent) == 1
    assert any("PREP window" in m for m in rig2.notifier.messages)


def test_without_prep_window_flow_is_unchanged():
    """prep_window_h=None (simulation/live-test default): direct go-live, no
    prepped state, no persona posts — the legacy path stays intact."""
    rig = build_simulation()
    hunt = rig.orchestrator.run_hunt()
    assert hunt.state == HuntState.DONE
    assert rig.dresser.persona_posts == []
    assert not any(r.get("state") == "prepped" for r in rig.repo.hunts.values())
