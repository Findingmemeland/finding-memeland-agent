"""Pack 2, item 1a (post-mortem P1a) — the anti-repetition circuit is closed.

Both halves existed since day one: generate(avoid_recent=...) and the stored
hunts.persona_identity. Nothing connected them — every hunt got an identical
prompt and the model converged on the same archetype (the Penelope repeat,
which falsifies the site's "brand-new identity for each hunt" promise).
"""

from finding_memeland.orchestrator.simulation import build_simulation
from finding_memeland.orchestrator.state_machine import _theme_line


class _SpyGenerator:
    def __init__(self, inner):
        self._inner = inner
        self.calls = []

    def generate(self, **kw):
        self.calls.append(kw)
        return self._inner.generate(**kw)


def _spy(rig):
    spy = _SpyGenerator(rig.orchestrator._persona_generator)
    rig.orchestrator._persona_generator = spy
    return spy


def test_first_hunt_passes_empty_avoid_list():
    rig = build_simulation()
    spy = _spy(rig)
    rig.orchestrator._prepare(200)
    assert spy.calls[0]["avoid_recent"] == []


def test_next_hunt_receives_previous_themes():
    rig = build_simulation()
    hunt1 = rig.orchestrator._prepare(200)
    rig.repo.set_hunt_state(hunt1.id, "done")

    spy = _spy(rig)
    rig.orchestrator._prepare(200)
    avoid = spy.calls[0]["avoid_recent"]
    assert avoid, "second hunt must receive the first hunt's theme"
    joined = " | ".join(avoid)
    # Name, archetype AND the literal answer terms — avoid the THEME, not just
    # the name (FakePersonaGenerator: 'burning daylight' / Amerigo Vespucci).
    assert "burning daylight" in joined
    assert "Amerigo" in joined and "Vespucci" in joined


def test_avoid_list_query_failure_degrades_loudly_not_fatally():
    rig = build_simulation()
    rig.repo.recent_persona_identities = lambda n=10: (_ for _ in ()).throw(
        ConnectionError("db down")
    )
    spy = _spy(rig)
    hunt = rig.orchestrator._prepare(200)  # must not raise
    assert hunt.id
    assert spy.calls[0]["avoid_recent"] == []
    assert any("avoid_recent" in m for m in rig.notifier.messages)


def test_theme_line_survives_json_string_and_missing_fields():
    assert _theme_line({
        "persona_display_name": "Penelope Unravels",
        "persona_identity": '{"archetype": "mythological figure", '
                            '"solution_terms": ["Penelope", "Odyssey"]}',
    }) == "Penelope Unravels / mythological figure / Penelope, Odyssey"
    assert _theme_line({"persona_display_name": "X", "persona_identity": None}) == "X"
    assert _theme_line({}) == ""


def test_full_hunt_still_completes_with_wiring_in_place():
    rig = build_simulation()
    hunt = rig.orchestrator.run_hunt()
    assert hunt.state.value == "done"
