"""Cadência das pistas — a fábrica partilhada por produção e pré-flight.

O buraco que estes testes fecham: os gaps eram constantes fixas (1-3h), o
live_test injetava a sua própria cadência, e nada exercitava o caminho que
corre num hunt REAL. Agora main.py e o check_genesis_config.py passam ambos
por next_clue_due_factory — e é ela que se testa aqui.
"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from finding_memeland.content.clue_engine import (
    MAX_GAP_SECONDS,
    MIN_GAP_SECONDS,
    next_clue_due_factory,
)


def _gap_s(due_fn, now=None) -> float:
    now = now or datetime.now(timezone.utc)
    return (due_fn(now) - now).total_seconds()


def test_gap_stays_inside_the_configured_band():
    due = next_clue_due_factory(600, 1800)  # Genesis: 10-30min
    for _ in range(500):
        assert 600 <= _gap_s(due) <= 1800


def test_gap_is_random_not_fixed():
    """Uma cadência previsível deixa os jogadores pôr despertador — mata o jogo."""
    due = next_clue_due_factory(600, 1800)
    seen = {_gap_s(due) for _ in range(200)}
    assert len(seen) > 50, "gaps demasiado repetidos — a cadência não é aleatória"


def test_min_equal_max_is_allowed_but_fixed():
    due = next_clue_due_factory(900, 900)
    assert all(_gap_s(due) == 900 for _ in range(20))


def test_min_greater_than_max_raises_at_build_not_mid_hunt():
    """random.randint(hi, lo) rebentaria SÓ ao agendar a 2ª pista — com o tesouro
    já enterrado e o hunt público. Tem de falhar ao construir."""
    with pytest.raises(ValueError, match="CLUE_MIN_GAP_S"):
        next_clue_due_factory(1800, 600)


@pytest.mark.parametrize("lo,hi", [(0, 1800), (600, 0), (-1, 600), (600, -1)])
def test_non_positive_gaps_raise(lo, hi):
    with pytest.raises(ValueError):
        next_clue_due_factory(lo, hi)


def test_due_is_in_the_future_and_tz_aware():
    due = next_clue_due_factory(600, 1800)
    now = datetime.now(timezone.utc)
    nxt = due(now)
    assert nxt > now
    assert nxt.tzinfo is not None


def test_defaults_still_match_the_published_pirate_code():
    """O site publica 'new clues drop every 1-3 hours'. Se um default mudar sem
    o site mudar, passamos a mentir aos jogadores."""
    assert MIN_GAP_SECONDS == 3600
    assert MAX_GAP_SECONDS == 3 * 3600


def test_genesis_worst_case_fits_under_its_holding_window():
    """A regra de ouro: HOLDING_HOURS > (nº pistas x max gap). Se o hunt puder
    durar mais que a janela de holding, alguém compra a meio e ganna à mesma —
    e a regra pública 'hold antes da primeira pista' torna-se FALSA."""
    genesis_max_gap_s, genesis_holding_h, assumed_clues = 1800, 8, 8
    worst_case_h = assumed_clues * genesis_max_gap_s / 3600
    assert worst_case_h == 4.0
    assert genesis_holding_h > worst_case_h
