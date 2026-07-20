"""Pack 2, item 3 — preflight refuses to launch while the clue_one explainer
is still the placeholder, so '<<EXPLAINER-PENDING>>' can never be posted to X.
The sequence is deliberate: deploy is safe at any time; /launch is what's
gated until the operator writes the real text in content/templates.py."""

from finding_memeland.content import templates
from finding_memeland.preflight import preflight_check


def test_preflight_refuses_while_placeholder(monkeypatch):
    monkeypatch.setattr(
        templates, "CLUE_ONE_EXPLAINER", templates._EXPLAINER_PLACEHOLDER_MARK
    )
    problems = preflight_check()  # no clients: only the content gate runs
    assert any("explainer" in p and "placeholder" in p for p in problems)


def test_preflight_passes_once_text_is_written(monkeypatch):
    monkeypatch.setattr(
        templates, "CLUE_ONE_EXPLAINER",
        "our AI hid a secret account on X. find it, DM the code, win the prize.",
    )
    assert preflight_check() == []
