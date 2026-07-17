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
    ASSUMED_MAX_CLUES,
    MAX_GAP_SECONDS,
    MIN_GAP_SECONDS,
    holding_window_covers_hunt,
    next_clue_due_factory,
    worst_case_hunt_hours,
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


def test_worst_case_maths():
    assert worst_case_hunt_hours(1800) == 4.0  # Genesis: 8 x 30min
    assert worst_case_hunt_hours(10800) == 24.0  # default: 8 x 3h
    assert worst_case_hunt_hours(1800, assumed_clues=4) == 2.0


def test_genesis_config_satisfies_the_golden_rule():
    """Genesis: janela de 8h vs pior caso de 4h."""
    assert holding_window_covers_hunt(holding_hours=8, max_gap_s=1800)


def test_the_config_we_almost_shipped_would_have_broken_the_rule():
    """Histórico real: 10-60min de gaps com janela de 6h dava 8h de pior caso.
    Alguém podia comprar a meio do hunt, esperar 6h, reclamar e GANHAR — contra
    a regra anunciada. É este o cenário que a regra de ouro tem de apanhar."""
    assert not holding_window_covers_hunt(holding_hours=6, max_gap_s=3600)


def test_KNOWN_GAP_defaults_do_not_satisfy_the_golden_rule():
    """⚠️ DÍVIDA CONHECIDA (Hunt #2+), não regressão.

    Os defaults publicados no site — pistas de 1-3h, hold de 24h — dão 8 x 3h =
    24h de pior caso contra uma janela de 24h. Empate não chega: quem comprasse
    logo a seguir à Clue 1 de um hunt longo teria 24h de holding no claim e
    passava, contra o "buying mid-hunt counts for nothing" que o site promete.

    Fica travado num teste para não se perder. Resolver antes do Hunt #2:
    subir HOLDING_HOURS (obriga a mudar o site) ou baixar CLUE_MAX_GAP_S.
    """
    assert not holding_window_covers_hunt(holding_hours=24, max_gap_s=MAX_GAP_SECONDS)


def test_equal_is_not_enough():
    """Janela == pior caso deixa o empate a favor do sniper. Tem de ser >."""
    assert not holding_window_covers_hunt(holding_hours=4, max_gap_s=1800)
    assert holding_window_covers_hunt(holding_hours=5, max_gap_s=1800)


def test_assumed_max_clues_is_the_documented_planning_number():
    assert ASSUMED_MAX_CLUES == 8
