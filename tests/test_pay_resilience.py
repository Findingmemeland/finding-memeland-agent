"""Hunt #2 P1 — once the money is out, the hunt ALWAYS reaches the reveal.

Production failure: record_winner hit a NOT NULL (23502) AFTER send_prize and
the hunt died without the Winner Announcement (posted by hand). Every
bookkeeping step after the transfer is now best-effort with a loud notify.
"""

from finding_memeland.orchestrator.simulation import build_simulation
from finding_memeland.orchestrator.state_machine import HuntState


def test_record_winner_crash_does_not_kill_a_paid_hunt():
    rig = build_simulation()

    def boom(**fields):
        raise RuntimeError(
            'Error 23502: null value in column "submission_id" of relation '
            '"winners" violates not-null constraint'
        )

    rig.repo.record_winner = boom
    hunt = rig.orchestrator.run_hunt()
    assert hunt.state == HuntState.DONE          # hunt completed anyway
    assert len(rig.payout.sent) == 1             # paid exactly once
    blob = "\n".join(rig.publisher.posts)
    assert "We have a winner" in blob            # announcement WENT OUT
    assert any("record_winner failed" in m for m in rig.notifier.messages)


def test_set_payout_status_crash_does_not_kill_a_paid_hunt():
    rig = build_simulation()
    real = rig.repo.set_payout_status

    def boom(payout_id, status, **fields):
        if status == "sent":
            raise ConnectionError("db hiccup right after the transfer")
        return real(payout_id, status, **fields)

    rig.repo.set_payout_status = boom
    hunt = rig.orchestrator.run_hunt()
    assert hunt.state == HuntState.DONE
    assert len(rig.payout.sent) == 1
    assert any("could not be marked 'sent'" in m for m in rig.notifier.messages)


def test_record_winner_receives_the_submission_row_id():
    rig = build_simulation()
    hunt = rig.orchestrator.run_hunt()
    assert hunt.state == HuntState.DONE
    assert rig.repo.winners, "winner row must exist"
    row = rig.repo.winners[0]
    assert row.get("submission_id") is not None
    # And it points at the actual winning submission row.
    won = [s for s in rig.repo.submissions if s.get("outcome") == "won"]
    assert won and row["submission_id"] == won[0]["id"]


def test_failed_announcement_retries_then_screams_but_hunt_survives():
    rig = build_simulation()
    real_post = rig.publisher.post
    state = {"fails": 0}

    def flaky(text, **kw):
        if "winner" in text.lower() and state["fails"] < 5:
            state["fails"] += 1
            raise ConnectionError("X 500")
        return real_post(text, **kw)

    rig.orchestrator._publisher.post = flaky
    hunt = rig.orchestrator.run_hunt()
    assert hunt.state == HuntState.DONE
    assert len(rig.payout.sent) == 1
    assert any("POST THE REVEAL MANUALLY" in m for m in rig.notifier.messages)
