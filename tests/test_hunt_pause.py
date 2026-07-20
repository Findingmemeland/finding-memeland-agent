"""Fix 3 (post-mortem P3.7) — /silence persisted on the hunt row.

The old threading.Event died with the process: after a Railway restart,
resume_hunts picked the hunt back up with a fresh, UN-paused control — a hunt
paused on purpose (e.g. suspected exploit) silently resumed itself. The pause
now lives on hunts.paused: shared across instances, recovered by resume, never
inherited by the next hunt.
"""

from finding_memeland.orchestrator.simulation import FakeRepo, build_simulation
from finding_memeland.runtime import DBHuntPause


def _live_hunt(repo, hid=1):
    repo.hunts[hid] = {"id": hid, "state": "live"}
    return hid


def test_pause_writes_through_to_the_hunt_row():
    repo = FakeRepo()
    hid = _live_hunt(repo)
    control = DBHuntPause(repo)
    assert control.pause() == 1
    assert repo.hunts[hid]["paused"] is True
    assert control.paused() is True


def test_pause_survives_a_restart():
    repo = FakeRepo()
    _live_hunt(repo)
    DBHuntPause(repo).pause()
    # "Restart": a brand-new control object over the same DB must see the pause.
    assert DBHuntPause(repo).paused() is True


def test_resume_clears_the_row():
    repo = FakeRepo()
    hid = _live_hunt(repo)
    control = DBHuntPause(repo)
    control.pause()
    assert control.resume() == 1
    assert repo.hunts[hid]["paused"] is False
    assert control.paused() is False


def test_pause_is_not_inherited_by_the_next_hunt():
    repo = FakeRepo()
    hid = _live_hunt(repo)
    control = DBHuntPause(repo)
    control.pause()
    # Hunt ends; a new hunt starts as a fresh row.
    repo.hunts[hid]["state"] = "done"
    _live_hunt(repo, hid=2)
    assert control.paused() is False


def test_pause_with_no_active_hunt_touches_nothing():
    repo = FakeRepo()
    control = DBHuntPause(repo)
    assert control.pause() == 0
    assert control.paused() is False


def test_db_read_hiccup_keeps_last_known_value():
    """A transient DB failure must neither un-pause a paused hunt nor pause a
    healthy one."""
    repo = FakeRepo()
    _live_hunt(repo)
    control = DBHuntPause(repo)
    control.pause()
    real = repo.active_hunts
    repo.active_hunts = lambda: (_ for _ in ()).throw(ConnectionError("db down"))
    assert control.paused() is True  # last known, not False
    repo.active_hunts = real
    assert control.paused() is True


def test_resumed_hunt_respects_persisted_pause_then_finishes():
    """End-to-end: process 1 goes live and the operator pauses; process 2
    (post-restart) must idle in PAUSED until the row is cleared, then finish."""
    rig1 = build_simulation()
    hunt1 = rig1.orchestrator._prepare(200)
    rig1.orchestrator._go_live(hunt1)
    DBHuntPause(rig1.repo).pause()
    assert rig1.repo.hunts[hunt1.id]["paused"] is True

    class _OperatorResumesLater(DBHuntPause):
        """Persisted control that simulates the operator sending /resume after
        the restarted loop has idled for a few cycles."""

        def __init__(self, repo):
            super().__init__(repo)
            self.checks = 0

        def paused(self):
            self.checks += 1
            if self.checks == 4:
                self.resume()
            return super().paused()

    rig2 = build_simulation(repo=rig1.repo)
    control2 = _OperatorResumesLater(rig1.repo)
    rig2.orchestrator._control = control2
    rig2.orchestrator.resume_hunts()

    # The pause CAME FROM THE DB (fresh process, fresh control) and held.
    assert control2.checks >= 4
    assert any("PAUSED" in m for m in rig2.notifier.messages)
    assert any("RESUMED" in m for m in rig2.notifier.messages)
    # After the operator's resume, the hunt ran to completion and paid once.
    assert rig1.repo.hunts[hunt1.id]["state"] == "done"
    assert len(rig2.payout.sent) == 1
