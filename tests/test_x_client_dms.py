"""Contract tests for XClient.read_dms with a fake tweepy client.

Covers the self-DM echo fix: the v2 dm_events endpoint returns BOTH directions,
so our own canned replies must be dropped, or they come back on the next poll
as fake 'submissions'.
"""

from datetime import datetime, timezone
from types import SimpleNamespace

from finding_memeland.social.x_client import XClient

MAIN_ID = "999"


def _ev(ev_id, sender_id, text, minute, event_type="MessageCreate"):
    return SimpleNamespace(
        id=ev_id,
        sender_id=sender_id,
        text=text,
        created_at=datetime(2026, 7, 5, 12, minute, tzinfo=timezone.utc),
        event_type=event_type,
    )


_INCLUDES = {
    "users": [
        SimpleNamespace(id="111", username="player_one"),
        SimpleNamespace(id=int(MAIN_ID), username="FindingMemeland"),
    ]
}


class _FakeV2:
    def __init__(self, events):
        self._events = events

    def get_me(self, **_):
        return SimpleNamespace(data=SimpleNamespace(id=int(MAIN_ID)))

    def get_direct_message_events(self, **_):
        return SimpleNamespace(data=self._events, includes=_INCLUDES, meta={})


class _PagedFakeV2:
    """Two pages: newest events first, second page behind a pagination token."""

    def __init__(self, pages):
        self._pages = pages  # {None: [...], "tok1": [...]} etc.
        self.calls = []

    def get_me(self, **_):
        return SimpleNamespace(data=SimpleNamespace(id=int(MAIN_ID)))

    def get_direct_message_events(self, **kwargs):
        token = kwargs.get("pagination_token")
        self.calls.append(token)
        events, next_token = self._pages[token]
        return SimpleNamespace(
            data=events, includes=_INCLUDES,
            meta={"next_token": next_token} if next_token else {},
        )


def _client(events) -> XClient:
    xc = XClient(api_key="k", api_secret="s")
    xc._client = _FakeV2(events)  # inject fake v2 client
    return xc


def test_own_outbound_dms_are_filtered():
    events = [
        _ev(10, "111", "found it! code AB2CD3EF", 0),
        _ev(11, MAIN_ID, "that code is not correct", 1),  # our canned reply
        _ev(12, "111", "trying again AB2CD3EF", 2),
    ]
    out = _client(events).read_dms()
    assert [d["dm_id"] for d in out] == ["10", "12"]
    assert all(d["sender_x_id"] != MAIN_ID for d in out)


def test_since_id_filter_still_applies():
    events = [_ev(10, "111", "old", 0), _ev(12, "111", "new", 2)]
    out = _client(events).read_dms(since_id="10")
    assert [d["dm_id"] for d in out] == ["12"]


def test_non_message_events_skipped_and_sorted():
    events = [
        _ev(13, "111", "second", 3),
        _ev(12, "111", "first", 2),
        _ev(14, "111", "", 4, event_type="ParticipantsJoin"),
    ]
    out = _client(events).read_dms()
    assert [d["text"] for d in out] == ["first", "second"]


def test_pagination_fetches_older_pages_until_marker():
    # Viral spike: page 1 (newest) has ids 20-21, page 2 has 12-13. since_id=12
    # => must fetch BOTH pages and return 13, 20, 21 (13 was first to arrive!).
    pages = {
        None: ([_ev(21, "111", "late", 5), _ev(20, "111", "later", 4)], "tok1"),
        "tok1": ([_ev(13, "111", "early", 2), _ev(12, "111", "seen", 1)], "tok2"),
    }
    fake = _PagedFakeV2(pages)
    xc = XClient(api_key="k", api_secret="s")
    xc._client = fake
    out = xc.read_dms(since_id="12")
    assert [d["dm_id"] for d in out] == ["13", "20", "21"]  # ascending by time
    assert fake.calls == [None, "tok1"]  # stopped at the marker, no 3rd page


def test_pagination_stops_when_no_next_token():
    pages = {None: ([_ev(20, "111", "only", 4)], None)}
    fake = _PagedFakeV2(pages)
    xc = XClient(api_key="k", api_secret="s")
    xc._client = fake
    out = xc.read_dms()
    assert [d["dm_id"] for d in out] == ["20"]
    assert fake.calls == [None]


def test_empty_inbox_makes_no_get_me_call():
    class _Counting(_FakeV2):
        def __init__(self):
            super().__init__([])
            self.me_calls = 0

        def get_me(self, **_):
            self.me_calls += 1
            return super().get_me()

    fake = _Counting()
    xc = XClient(api_key="k", api_secret="s")
    xc._client = fake
    assert xc.read_dms() == []
    assert fake.me_calls == 0  # empty inbox stays $0
