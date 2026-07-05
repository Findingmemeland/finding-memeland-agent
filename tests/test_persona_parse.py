import json

from finding_memeland.persona.generator import (
    PersonaGenerator,
    _extract_json,
    _to_persona,
)

GOOD = {
    "archetype": "historical figure dead at least 50 years",
    "display_name": "the cartographer of nowhere",
    "bio": "drawing maps to places that moved. mostly lost, occasionally found.",
    "backstory": "A 19th-century mapmaker known for charting territories that "
    "no longer exist. Clues lean on the paradox of mapping the unmappable.",
    "voice": "wry, terse, fond of geographic metaphors",
    "avatar_prompt": "weathered antique map fragment, sepia, candlelight",
    "banner_prompt": "a fog-bound coastline dissolving into blank parchment",
    "solution_terms": ["the cartographer of nowhere", "phantom island"],
    "findable_post": "the quiet hum of an unfinished atlas keeps me company tonight",
}


def test_extract_json_tolerates_prose_wrapping():
    raw = 'Here you go:\n{"a": 1, "b": "two"}\nHope that helps!'
    assert _extract_json(raw) == {"a": 1, "b": "two"}


def test_to_persona_happy_path():
    p = _to_persona(GOOD)
    assert p.display_name == "the cartographer of nowhere"
    assert "[" not in p.bio and "]" not in p.bio
    assert len(p.bio) <= 160 - 16


def test_to_persona_sanitizes_forbidden_chars():
    data = dict(GOOD, display_name="weird [name]", bio="vibes <only> here")
    p = _to_persona(data)
    assert "[" not in p.display_name and "]" not in p.display_name
    assert "<" not in p.bio and ">" not in p.bio


def test_to_persona_rejects_missing_keys():
    bad = {k: v for k, v in GOOD.items() if k != "bio"}
    try:
        _to_persona(bad)
    except ValueError:
        return
    raise AssertionError("expected ValueError for missing key")


class _FakeBlock:
    type = "text"

    def __init__(self, text):
        self.text = text


class _FakeAnthropicClient:
    """Returns each scripted payload in order; records how many calls were made."""

    def __init__(self, payloads):
        self._payloads = list(payloads)
        self.calls = 0
        self.messages = self

    def create(self, **_):
        self.calls += 1
        text = json.dumps(self._payloads.pop(0))
        return type("R", (), {"content": [_FakeBlock(text)]})()


def test_generate_retries_on_rejected_output():
    # 1st output leaks a solution term in findable_post (live-test crash of
    # 2026-07-05); the generator must regenerate instead of raising.
    leaky = dict(GOOD, findable_post="thoughts from a phantom island tonight")
    client = _FakeAnthropicClient([leaky, GOOD])
    gen = PersonaGenerator(client, model="m")
    p = gen.generate()
    assert client.calls == 2
    assert p.display_name == GOOD["display_name"]


def test_generate_gives_up_after_max_attempts():
    leaky = dict(GOOD, findable_post="thoughts from a phantom island tonight")
    client = _FakeAnthropicClient([leaky, leaky, leaky])
    gen = PersonaGenerator(client, model="m")
    try:
        gen.generate()
    except ValueError as e:
        assert "after 3 attempts" in str(e)
        assert client.calls == 3
        return
    raise AssertionError("expected ValueError after exhausting attempts")
