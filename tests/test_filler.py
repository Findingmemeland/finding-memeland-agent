import json

from finding_memeland.content.filler import FillerEngine, _is_clean


class _Block:
    type = "text"

    def __init__(self, text):
        self.text = text


class _FakeAnthropic:
    def __init__(self, responses):
        self._responses = list(responses)
        self.calls = 0
        self.last_user_msg = ""
        self.messages = self

    def create(self, **kwargs):
        self.calls += 1
        self.last_user_msg = kwargs.get("messages", [{}])[0].get("content", "")
        return type("R", (), {"content": [_Block(self._responses.pop(0))]})()


def _payload(*options):
    return json.dumps({"options": list(options)})


def test_generates_clean_options():
    fake = _FakeAnthropic([_payload("the frog stirs. 🐸", "you hunt. i hide.", "memes breathe again")])
    opts = FillerEngine(fake, "m").generate_options()
    assert len(opts) == 3
    assert fake.calls == 1


def test_topic_reaches_the_prompt():
    fake = _FakeAnthropic([_payload("a", "b", "c")])
    FillerEngine(fake, "m").generate_options(topic="goza com o B20")
    assert "goza com o B20" in fake.last_user_msg


def test_banned_options_are_filtered_out():
    fake = _FakeAnthropic([_payload(
        "this will 100x trust me",          # banned: 100x
        "buy now or cry later",             # banned: buy now
        "the sharpest holder eats. 🐸",     # clean
    )])
    opts = FillerEngine(fake, "m").generate_options()
    assert opts == ["the sharpest holder eats. 🐸"]


def test_all_dirty_retries_then_raises():
    dirty = _payload("pump it", "to the moon")
    fake = _FakeAnthropic([dirty, dirty])
    try:
        FillerEngine(fake, "m").generate_options()
    except RuntimeError:
        assert fake.calls == 2
        return
    raise AssertionError("expected RuntimeError when nothing survives the filter")


def test_is_clean_rules():
    assert _is_clean("gm to everyone except the charts. 🐸")
    assert not _is_clean("x" * 300)                 # too long
    assert not _is_clean("#a #b #c three hashtags") # >2 hashtags
    assert not _is_clean("check https://scam.xyz")  # link
