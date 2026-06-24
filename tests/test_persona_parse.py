from finding_memeland.persona.generator import _extract_json, _to_persona

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
