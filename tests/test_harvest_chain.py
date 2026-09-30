"""/harvest com opção de cadeia (30/09, Pedro).

"Quero encher a despensa com alvos de Base; de Ethereum já temos que
chegue." `/harvest 200 base` gasta a corrida — e a quota do gateway dedicado —
só em Base. Sem cadeia, corre todas, como antes. Uma cadeia que o jogo não
conhece é recusada com a lista das válidas: nunca ignorada, que seria correr
as duas quando se pediu uma.
"""
from __future__ import annotations

import pytest
from test_harvest_command import _Harv, _wiring

from finding_memeland.target.harvest import HARVEST_USAGE, parse_harvest_args

A = "0x" + "a1" * 20
B = "0x" + "b2" * 20

# --------------------------------------------------------------------------- #
# 1. O comando                                                                  #
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("arg, want", [
    ("", (200, None)),
    ("200", (200, None)),
    ("150", (150, None)),
    ("200 base", (200, "base")),
    ("base 200", (200, "base")),
    ("base", (200, "base")),
    ("  300   ETHEREUM ", (300, "ethereum")),
])
def test_blocks_and_chain_in_any_order(arg, want):
    assert parse_harvest_args(arg, default_blocks=200) == want


@pytest.mark.parametrize("arg", [
    "200 polygon",        # a chain the game does not know
    "200 base base",      # said twice
    "200 300",            # two block counts
    "0", "2001",          # out of range
    "-5", "abc",
])
def test_anything_else_is_refused_with_the_usage(arg):
    with pytest.raises(ValueError) as e:
        parse_harvest_args(arg, default_blocks=200)
    assert str(e.value) == HARVEST_USAGE
    assert "ethereum" in HARVEST_USAGE and "base" in HARVEST_USAGE


# --------------------------------------------------------------------------- #
# 2. A corrida                                                                  #
# --------------------------------------------------------------------------- #


def test_one_chain_runs_that_chain_only():
    eth, base = _Harv([f"ethereum:{A}:1"]), _Harv([f"base:{B}:7"])
    tw, store = _wiring({"ethereum": eth, "base": base}, {"ethereum", "base"})
    out = tw.harvest(200, only="base")
    assert base.calls == 1 and eth.calls == 0, "Ethereum was scanned"
    assert out.startswith("harvest (só base):"), out
    assert "ethereum" not in out
    assert store.larder.size() == 1


def test_no_chain_runs_them_all_as_before():
    eth, base = _Harv([f"ethereum:{A}:1"]), _Harv([f"base:{B}:7"])
    tw, store = _wiring({"ethereum": eth, "base": base}, {"ethereum", "base"})
    out = tw.harvest(200)
    assert eth.calls == 1 and base.calls == 1
    assert out.startswith("harvest:\n"), out
    assert store.larder.size() == 2


def test_a_chain_this_process_cannot_harvest_is_said_not_swapped():
    """Base sem RPC com chave: dizê-lo, e não varrer Ethereum no lugar dela."""
    eth = _Harv([f"ethereum:{A}:1"])
    tw, store = _wiring({"ethereum": eth}, {"ethereum"})
    out = tw.harvest(200, only="base")
    assert "base não configurada" in out and "nada foi varrido" in out
    assert eth.calls == 0 and store.larder.size() == 0


def test_a_chain_without_a_public_provider_is_still_skipped_with_the_reason():
    base = _Harv([f"base:{B}:7"])
    tw, _ = _wiring({"ethereum": _Harv(), "base": base}, {"ethereum"})
    out = tw.harvest(200, only="base")
    assert base.calls == 0 and "TARGET_PUBLIC_RPCS_BASE" in out
