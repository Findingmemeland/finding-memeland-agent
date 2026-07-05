from finding_memeland.content.clue_engine import (
    ClueEngine,
    PersonaContext,
    _parse_clue,
)


class _Block:
    type = "text"

    def __init__(self, text):
        self.text = text


class _Resp:
    def __init__(self, text):
        self.content = [_Block(text)]


class _FakeAnthropic:
    """Returns queued responses in order; records calls + last user message."""

    def __init__(self, responses):
        self._responses = list(responses)
        self.calls = 0
        self.last_user_msg = ""
        self.messages = self

    def create(self, **kwargs):  # noqa: D401
        self.calls += 1
        self.last_user_msg = kwargs.get("messages", [{}])[0].get("content", "")
        return _Resp(self._responses.pop(0))


PERSONA = PersonaContext(
    display_name="Celestial Mechanic",
    handle="@kepler_77",
    bio="everything moves, everything pulls",
    avatar_description="stern 19th-c astronomer, ink-wash",
    voice="terse, imperious",
    backstory="Urbain Le Verrier, who found Neptune by math alone.",
    solution_terms=["Le Verrier", "Neptune"],
)


def test_parse_clue_extracts_clue_and_taunt():
    d = _parse_clue('{"clue": "found a world with a pen", "taunt": "c\'mon"}')
    assert d.text == "found a world with a pen"
    assert d.taunt == "c'mon"


def test_parse_clue_empty_taunt_becomes_none():
    d = _parse_clue('{"clue": "x", "taunt": ""}')
    assert d.taunt is None


def test_next_clue_returns_clean_clue():
    clean = '{"clue": "found a world without looking up", "taunt": "too slow"}'
    eng = ClueEngine(_FakeAnthropic([clean]), "model-x")
    d = eng.next_clue(PERSONA, clue_index=2, prior_clues=[])
    assert "world" in d.text


def test_next_clue_retries_when_solution_leaks():
    leaky = '{"clue": "it is obviously Neptune", "taunt": "lol"}'
    clean = '{"clue": "found a world without looking up", "taunt": "too slow"}'
    fake = _FakeAnthropic([leaky, clean])
    eng = ClueEngine(fake, "model-x")
    d = eng.next_clue(PERSONA, clue_index=3, prior_clues=[])
    assert fake.calls == 2          # retried past the leaky one
    assert "Neptune" not in d.text


def test_retry_prompt_carries_rejection_feedback():
    # Live-test crash of 2026-07-05: the model repeated the same forbidden word
    # 4x because it was never told WHY the attempt was rejected. The retry
    # prompt must now include the guardrail reasons + the rejected text.
    leaky = '{"clue": "it is obviously Neptune", "taunt": "lol"}'
    clean = '{"clue": "found a world without looking up", "taunt": "too slow"}'
    fake = _FakeAnthropic([leaky, clean])
    eng = ClueEngine(fake, "model-x")
    eng.next_clue(PERSONA, clue_index=3, prior_clues=[])
    assert "REJECTED" in fake.last_user_msg
    assert "Neptune" in fake.last_user_msg  # names the flagged word to avoid


def test_next_clue_gives_up_after_max_attempts():
    leaky = '{"clue": "Le Verrier did it", "taunt": "lol"}'
    fake = _FakeAnthropic([leaky, leaky, leaky])
    eng = ClueEngine(fake, "model-x")
    try:
        eng.next_clue(PERSONA, clue_index=2, prior_clues=[], max_attempts=3)
    except RuntimeError:
        return
    raise AssertionError("expected RuntimeError after exhausting attempts")
