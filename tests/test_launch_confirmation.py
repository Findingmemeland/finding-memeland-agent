"""Fase 3 — a confirmação sim/não do /launch.

Desde a Fase 2 o launch é instantâneo (Clue 1 em segundos, sem take-backs);
este gate é a proteção que substitui a antiga janela de arrependimento.
Regras: só um 'sim' explícito dispara; 'não' cancela; o pedido expira; um
'sim' tardio NUNCA lança um hunt.
"""

from finding_memeland.telegram.confirmation import LaunchConfirmation


class FakeTime:
    def __init__(self):
        self.t = 1000.0

    def now(self):
        return self.t

    def advance(self, s):
        self.t += s


def _gate(expiry_s=120):
    ft = FakeTime()
    return LaunchConfirmation(expiry_s=expiry_s, now_fn=ft.now), ft


def test_sim_confirms_with_the_staged_prize_and_handle():
    gate, _ = _gate()
    gate.stage(500_000_000, "@ExpressoTitgo")
    res = gate.resolve("sim")
    assert res.outcome == "confirm"
    assert res.prize_fmml == 500_000_000
    assert res.expected_handle == "@ExpressoTitgo"


def test_nao_cancels_with_and_without_accent():
    for answer in ("não", "nao", "NÃO", "  Nao "):
        gate, _ = _gate()
        gate.stage(1, "@X")
        assert gate.resolve(answer).outcome == "cancel"


def test_sim_is_case_and_whitespace_insensitive():
    gate, _ = _gate()
    gate.stage(1, "@X")
    assert gate.resolve("  SIM ").outcome == "confirm"


def test_confirm_consumes_the_request():
    gate, _ = _gate()
    gate.stage(1, "@X")
    assert gate.resolve("sim").outcome == "confirm"
    assert gate.resolve("sim").outcome == "none"      # nothing staged anymore


def test_cancel_consumes_the_request():
    gate, _ = _gate()
    gate.stage(1, "@X")
    assert gate.resolve("não").outcome == "cancel"
    assert gate.resolve("sim").outcome == "none"


def test_noise_keeps_the_request_alive():
    gate, _ = _gate()
    gate.stage(1, "@X")
    assert gate.resolve("what?").outcome == "noise"
    assert gate.resolve("sim").outcome == "confirm"   # still confirmable


def test_free_text_with_nothing_staged_is_ignored():
    gate, _ = _gate()
    assert gate.resolve("bom dia").outcome == "none"
    assert gate.resolve("sim").outcome == "none"      # a stray 'sim' never fires


def test_late_sim_is_expired_and_never_confirms():
    gate, ft = _gate(expiry_s=120)
    gate.stage(500_000_000, "@X")
    ft.advance(121)
    res = gate.resolve("sim")
    assert res.outcome == "expired"
    assert res.prize_fmml is None                     # nothing to launch
    assert gate.resolve("sim").outcome == "none"      # consumed


def test_expiry_applies_to_any_answer():
    gate, ft = _gate(expiry_s=120)
    gate.stage(1, "@X")
    ft.advance(300)
    assert gate.resolve("não").outcome == "expired"


def test_restage_replaces_the_previous_request():
    gate, _ = _gate()
    gate.stage(100, "@Old")
    gate.stage(200, "@New")
    res = gate.resolve("sim")
    assert res.prize_fmml == 200 and res.expected_handle == "@New"


def test_clear_discards_the_request():
    gate, _ = _gate()
    gate.stage(1, "@X")
    gate.clear()
    assert gate.resolve("sim").outcome == "none"
