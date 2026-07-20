"""Fix 4 (post-mortem P0 pack) — liveness watchdog for the hunt loop.

The Genesis P0 was undiagnosable because silence was invisible: a hung loop, a
dead thread and a healthy idle inbox produced identical (empty) logs. The loop
now beats every cycle; PollHeartbeat.check() returns the Telegram alert when a
live hunt stops beating.
"""

from datetime import datetime, timedelta, timezone

from finding_memeland.orchestrator.simulation import build_simulation
from finding_memeland.runtime import PollHeartbeat


class _Clock:
    def __init__(self):
        self.t = datetime(2026, 8, 1, 12, 0, tzinfo=timezone.utc)

    def now(self):
        return self.t

    def advance(self, s):
        self.t += timedelta(seconds=s)


def _hb(clock, stall=600, realert=900):
    return PollHeartbeat(stall_after_s=stall, realert_s=realert, now_fn=clock.now)


def test_no_alert_when_not_live():
    clock = _Clock()
    hb = _hb(clock)
    clock.advance(10_000)
    assert hb.check() is None


def test_no_alert_while_beating():
    clock = _Clock()
    hb = _hb(clock)
    hb.mark_live(True)
    for _ in range(20):
        clock.advance(75)  # normal poll cadence
        hb.beat()
        assert hb.check() is None


def test_alert_fires_when_live_loop_stalls():
    clock = _Clock()
    hb = _hb(clock)
    hb.mark_live(True)
    hb.beat()
    clock.advance(601)
    alert = hb.check()
    assert alert is not None
    assert "LIVE" in alert and "DM" in alert  # the scream says what to do


def test_alert_rate_limited_then_rescreams():
    clock = _Clock()
    hb = _hb(clock)
    hb.mark_live(True)
    hb.beat()
    clock.advance(601)
    assert hb.check() is not None
    clock.advance(60)
    assert hb.check() is None  # within realert window
    clock.advance(900)
    assert hb.check() is not None  # a real incident keeps screaming


def test_beat_resets_the_stall_clock():
    clock = _Clock()
    hb = _hb(clock)
    hb.mark_live(True)
    hb.beat()
    clock.advance(599)
    hb.beat()
    clock.advance(599)
    assert hb.check() is None


def test_no_alert_after_loop_exits():
    """mark_live(False) on exit (winner/void/crash): the watchdog must not keep
    screaming over a loop that already ended."""
    clock = _Clock()
    hb = _hb(clock)
    hb.mark_live(True)
    hb.beat()
    hb.mark_live(False)
    clock.advance(10_000)
    assert hb.check() is None


def test_orchestrator_beats_and_brackets_the_loop():
    class _Spy(PollHeartbeat):
        def __init__(self):
            super().__init__()
            self.beats = 0
            self.lives = []

        def beat(self):
            self.beats += 1
            super().beat()

        def mark_live(self, live):
            self.lives.append(live)
            super().mark_live(live)

    rig = build_simulation()
    spy = _Spy()
    rig.orchestrator._heartbeat = spy
    hunt = rig.orchestrator.run_hunt()
    assert hunt.state.value == "done"
    assert spy.beats >= 3            # one beat per loop cycle
    assert spy.lives == [True, False]  # bracketed exactly once


def test_loop_pre_hunt_skip_is_logged(capsys):
    """The one silent drop path in the loop (pre-hunt DMs) is now visible."""
    from datetime import timedelta as _td

    from finding_memeland.orchestrator.ports import Submission

    rig = build_simulation()
    orch = rig.orchestrator
    hunt = orch._prepare(200)
    orch._go_live(hunt)

    class _OldThenWinner:
        def __init__(self, repo, clock):
            self.repo, self.clock, self.calls = repo, clock, 0

        def poll(self, since):
            self.calls += 1
            if self.calls > 1:
                return []
            return [
                Submission(dm_id="900", sender_x_id="42", sender_handle="old",
                           body="hello from last month",
                           created_at=hunt.started_at - _td(days=3)),
                Submission(dm_id="901", sender_x_id="9001", sender_handle="win",
                           body=f"code {self.repo.latest_claim_code()} "
                                f"wallet 0x{'a' * 40}",
                           created_at=hunt.started_at + _td(minutes=5)),
            ]

    orch._dm_source = _OldThenWinner(rig.repo, rig.clock)
    winner = orch._clue_and_dm_loop(hunt)
    out = capsys.readouterr().out
    assert winner is not None
    assert "dm 900 skipped: pre-hunt" in out
    assert "processing 2 dm(s)" in out
