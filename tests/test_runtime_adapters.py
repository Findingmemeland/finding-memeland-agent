import os
from datetime import datetime, timedelta, timezone

from finding_memeland.persona.source import DBPersonaSource, persona_findability_ready

_OLD = (datetime.now(timezone.utc) - timedelta(days=30)).isoformat()
_NEW = (datetime.now(timezone.utc) - timedelta(days=2)).isoformat()
from finding_memeland.runtime import (
    ManualPriceFeed,
    SystemClock,
    env_token_resolver,
    round_sig,
    write_temp_png,
)
from finding_memeland.social.publisher import XPublisher


# --- XPublisher ---
class _FakeX:
    def __init__(self):
        self.posts = []
        self.replies = []

    def post(self, text, *, long_post=False):
        self.posts.append((text, long_post))
        return "t1"

    def reply_dm(self, recipient_x_id, text):
        self.replies.append((recipient_x_id, text))


def test_publisher_posts_and_replies():
    x = _FakeX()
    pub = XPublisher(x)
    assert pub.post("gm", long_post=True) == "t1"
    pub.reply_dm("99", "nope")
    assert x.posts == [("gm", True)]
    assert x.replies == [("99", "nope")]


# --- ManualPriceFeed ---
def test_price_feed_converts_usd_to_whole_tokens():
    feed = ManualPriceFeed(usd_per_token=0.0001)
    assert feed.usd_to_fmml(500) == 5_000_000


def test_price_feed_rounds_to_clean_number():
    # 200 / 0.0000123 = 16,260,162.6 -> rounded to 3 sig figs -> 16,300,000
    feed = ManualPriceFeed(usd_per_token=0.0000123)
    out = feed.usd_to_fmml(200)
    assert out == 16_300_000
    assert out % 100_000 == 0  # clean, no ugly trailing digits


def test_round_sig_examples():
    assert round_sig(16_260_163) == 16_300_000
    assert round_sig(20_000_000) == 20_000_000
    assert round_sig(1234, 3) == 1230
    assert round_sig(0) == 0


def test_price_feed_raises_on_use_when_price_unset():
    feed = ManualPriceFeed(0)  # boots fine (token not launched yet)
    try:
        feed.usd_to_fmml(500)
    except RuntimeError:
        return
    raise AssertionError("expected RuntimeError when price unset")


# --- write_temp_png ---
def test_write_temp_png_roundtrip():
    path = write_temp_png(b"\x89PNG data")
    with open(path, "rb") as fh:
        assert fh.read() == b"\x89PNG data"
    assert path.endswith(".png")


# --- SystemClock ---
def test_system_clock_now_is_utc():
    assert SystemClock().now().tzinfo is not None


# --- env_token_resolver ---
def test_env_token_resolver_reads_env():
    os.environ["X_PERSONA_07_ACCESS_TOKEN"] = "tok7"
    os.environ["X_PERSONA_07_ACCESS_SECRET"] = "sec7"
    assert env_token_resolver("07") == ("tok7", "sec7")


def test_env_token_resolver_raises_when_missing():
    try:
        env_token_resolver("doesnotexist")
    except RuntimeError:
        return
    raise AssertionError("expected RuntimeError for missing tokens")


# --- DBPersonaSource ---
class _Repo:
    def __init__(self, row):
        self._row = row
        self.state_calls = []

    def next_ready_persona(self):
        return self._row

    def set_persona_state(self, pid, state, **fields):
        self.state_calls.append((pid, state, fields))


def _ready_row():
    return {"id": "p1", "handle": "@anon", "x_user_id": "123", "oauth_ref": "01",
            "account_created_at": _OLD, "phone_verified": True}


def test_persona_findability_ready_rule():
    # phone + old enough -> ready
    assert persona_findability_ready(_OLD, True, min_days=7)
    # no phone -> not ready
    assert not persona_findability_ready(_OLD, False, min_days=7)
    # too new -> not ready
    assert not persona_findability_ready(_NEW, True, min_days=7)
    # missing created date -> not ready
    assert not persona_findability_ready(None, True, min_days=7)


def test_persona_source_acquires_and_marks_in_play():
    repo = _Repo(_ready_row())
    src = DBPersonaSource(repo, token_resolver=lambda ref: (f"tok{ref}", f"sec{ref}"))
    persona = src.acquire_ready()
    assert persona.handle == "@anon" and persona.x_user_id == "123"
    assert persona.access_token == "tok01" and persona.access_secret == "sec01"
    assert ("p1", "in_play", {}) in repo.state_calls


def test_persona_source_rejects_unverified_or_too_new():
    for bad in ({**_ready_row(), "phone_verified": False},
                {**_ready_row(), "account_created_at": _NEW}):
        src = DBPersonaSource(_Repo(bad), token_resolver=lambda r: ("t", "s"))
        try:
            src.acquire_ready()
        except RuntimeError:
            continue
        raise AssertionError("expected RuntimeError for under-prepared persona")


def test_persona_source_raises_when_pipeline_empty():
    src = DBPersonaSource(_Repo(None), token_resolver=lambda r: ("t", "s"))
    try:
        src.acquire_ready()
    except RuntimeError:
        return
    raise AssertionError("expected RuntimeError when no ready persona")


def test_persona_source_retire_sets_delete_after():
    repo = _Repo({"id": "p1", "handle": "@a", "x_user_id": "1", "oauth_ref": "01"})
    DBPersonaSource(repo, token_resolver=lambda r: ("t", "s")).mark_retired("p1")
    pid, state, fields = repo.state_calls[-1]
    assert state == "retired" and "delete_after" in fields
