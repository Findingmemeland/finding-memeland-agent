from datetime import datetime, UTC

from finding_memeland.dm.listener import XDMSource


class _FakeX:
    def __init__(self, events):
        self._events = events
        self.last_since = "unset"

    def read_dms(self, *, since_id=None):
        self.last_since = since_id
        return self._events


def test_maps_events_to_submissions():
    when = datetime(2026, 8, 1, tzinfo=UTC)
    fake = _FakeX([
        {"dm_id": "1", "sender_x_id": "9", "sender_handle": "anon",
         "text": "code ABCDEFGH wallet 0x" + "a" * 40, "created_at": when},
    ])
    src = XDMSource(fake)
    subs = src.poll(since_id="0")
    assert fake.last_since == "0"
    assert len(subs) == 1
    s = subs[0]
    assert s.dm_id == "1" and s.sender_x_id == "9" and s.sender_handle == "anon"
    assert "ABCDEFGH" in s.body and s.created_at == when


def test_empty_inbox_returns_empty_list():
    src = XDMSource(_FakeX([]))
    assert src.poll(since_id=None) == []
