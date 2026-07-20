"""Fix 1 (post-mortem bonus A) — the double-hunt guard reads the DB, not memory.

After a restart, a resumed hunt is invisible to main.py's in-memory hunt_flag:
/status said "idle" and /launch would happily start a SECOND parallel hunt.
The DB is the only source of truth shared across restarts and the Railway
deploy-overlap window (two containers alive at once).
"""

from finding_memeland.runtime import active_hunt_guard, hunt_status_line


class _Repo:
    def __init__(self, rows=None, err=None):
        self._rows = rows or []
        self._err = err

    def active_hunts(self):
        if self._err:
            raise self._err
        return self._rows


def test_guard_allows_launch_when_db_has_no_active_hunt():
    assert active_hunt_guard(_Repo(rows=[])) is None


def test_guard_refuses_when_db_has_an_active_hunt():
    msg = active_hunt_guard(_Repo(rows=[{"id": 2, "state": "live"}]))
    assert msg is not None
    assert "#2" in msg and "live" in msg


def test_guard_refuses_a_resumed_hunt_invisible_to_the_local_flag():
    # The Genesis scenario: hunt live in the DB, hunt_flag long gone.
    msg = active_hunt_guard(_Repo(rows=[{"id": 7, "state": "resolving"}]))
    assert msg is not None and "one at a time" in msg


def test_guard_refuses_blind_launch_when_db_is_unreachable():
    msg = active_hunt_guard(_Repo(err=ConnectionError("supabase down")))
    assert msg is not None and "NOT launching" in msg


def test_status_reports_db_state_not_memory():
    line = hunt_status_line(_Repo(rows=[{"id": 2, "state": "live"}]), local_active=False)
    assert "#2" in line and "LIVE" in line


def test_status_shows_persisted_pause():
    line = hunt_status_line(
        _Repo(rows=[{"id": 2, "state": "live", "paused": True}]), local_active=True
    )
    assert "PAUSED" in line


def test_status_flags_local_thread_without_db_row():
    line = hunt_status_line(_Repo(rows=[]), local_active=True)
    assert "idle" in line and "investigate" in line


def test_status_flags_impossible_double_hunt():
    line = hunt_status_line(
        _Repo(rows=[{"id": 2, "state": "live"}, {"id": 3, "state": "live"}]),
        local_active=True,
    )
    assert "MORE active hunt" in line


def test_status_degrades_loudly_when_db_unreachable():
    line = hunt_status_line(_Repo(err=TimeoutError("db")), local_active=False)
    assert "DB check failed" in line
