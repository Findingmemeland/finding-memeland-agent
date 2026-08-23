"""Tests for the relic ↔ orchestrator integration (package 5).

These run against the REAL `PreparedHunt`/`HuntState` and the REAL frozen
integrity protocol — only the orchestrator's I/O surface is faked. The properties
that matter: the commitment verifies through the untouched protocol, the operator
never sees the hidden name, persona hunts behave exactly as before, and a trophy
failure can never break a hunt whose prize is already paid.
"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from finding_memeland.chain.relic_trophy import FakeNFTTransfer
from finding_memeland.content.integrity import compute_integrity_hash, verify_integrity_hash
from finding_memeland.orchestrator.state_machine import HuntState, PreparedHunt
from finding_memeland.persona.relic import Relic, new_identity, relic_canonical_id
from finding_memeland.persona.relic_findability import FakeFindability, FindabilityRefused
from finding_memeland.persona.relic_integration import (
    deliver_trophy, engine_for, prepare_relic_hunt, relic_label, retire_relic,
    reveal_extra_line,
)
from finding_memeland.persona.relic_pool import FakeRelicRepo, NullPoolCipher, RelicPool


class _Clock:
    def now(self): return datetime(2026, 8, 22, 12, 0, tzinfo=timezone.utc)
    def sleep(self, s): pass


class _Repo:
    def __init__(self): self.created = []
    def create_hunt(self, **f): self.created.append(f); return 77


class _Orch:
    """The slice of Orchestrator the relic integration touches."""

    def __init__(self, pool, canonical):
        self._relic_pool = pool
        self._relic_findability = canonical
        self._relic_findability_secondary = ()
        self._clock = _Clock()
        self._repo = _Repo()
        self._holding_hours = 0
        self._clue_engine = "PERSONA_ENGINE"
        self._relic_clue_engine = "RELIC_ENGINE"
        self._trophy_port = FakeNFTTransfer()
        self.notes = []

    def _notify(self, t): self.notes.append(t)
    def _next_number(self): return 7
    def _prize_usd_of(self, p): return 123.0


class _Winner:
    wallet = "0xWINNER"
    class submission:  # noqa: D106
        sender_handle = "@nesfruitaa"


def _pool_with_relic(name="Maroon Ledger", indexed=True):
    pool = RelicPool(FakeRelicRepo(), NullPoolCipher())
    ident = new_identity(name=name, description="kept the books",
                         image_prompt="a brass ledger", solution_terms=["Cassandra"])
    pool.add(Relic(id="r1"), ident)
    cid = relic_canonical_id("base", "0xAAA", "1")
    pool.mark_minted("r1", chain="base", contract="0xAAA", token_id="1",
                     mint_wallet_ref="W1", image_uri="ipfs://x",
                     commitment=ident.commitment_for(cid),
                     minted_at=datetime(2026, 8, 1, tzinfo=timezone.utc))
    canon = FakeFindability("basescan", {name} if indexed else set())
    return pool, ident, _Orch(pool, canon)


def _persona_hunt():
    return PreparedHunt(id=1, persona=None, identity=None, ctx=None, claim_code="X",
                        salt="s", integrity_hash="h", prize_usd=0, prize_fmml=1,
                        min_balance_fmml=0, holding_hours=0)


# --------------------------------------------------------------------------- #
# prepare                                                                      #
# --------------------------------------------------------------------------- #


def test_prepare_returns_a_real_prepared_hunt():
    pool, _, orch = _pool_with_relic()
    hunt = prepare_relic_hunt(orch, 500_000_000, 10_000_000)
    assert isinstance(hunt, PreparedHunt)
    assert hunt.state == HuntState.PREPARING and hunt.number == 7
    assert hunt.predressed is True          # no prep window: already indexed


def test_frozen_integrity_protocol_reproduces_the_mint_commitment():
    """The design's keystone: canonical_id in x_user_id means the UNTOUCHED
    protocol produces and verifies the relic's commitment."""
    pool, ident, orch = _pool_with_relic()
    hunt = prepare_relic_hunt(orch, 1, 0)
    assert hunt.persona.x_user_id == "base:0xaaa:1"
    assert compute_integrity_hash(hunt.persona.x_user_id, hunt.claim_code,
                                  hunt.salt) == hunt.integrity_hash
    assert verify_integrity_hash(hunt.persona.x_user_id, hunt.claim_code,
                                 hunt.salt, hunt.integrity_hash)
    assert hunt.claim_code == ident.claim_code and hunt.salt == ident.salt


def test_operator_messages_never_leak_the_name_or_code():
    pool, ident, orch = _pool_with_relic()
    hunt = prepare_relic_hunt(orch, 1, 0)
    assert hunt.persona.handle == relic_label("r1")     # neutral label
    for note in orch.notes:
        assert "Maroon" not in note and "Ledger" not in note
        assert ident.claim_code not in note


def test_hunt_row_stores_no_plaintext_identity():
    """Blind mode in the DB: name/bio columns stay NULL for relic hunts."""
    pool, _, orch = _pool_with_relic()
    prepare_relic_hunt(orch, 1, 0, ladder_exempt=True)
    row = orch._repo.created[0]
    assert row["persona_display_name"] is None and row["persona_bio"] is None
    assert row["relic_id"] == "r1" and row["ladder_exempt"] is True


def test_pool_is_consumed_only_after_the_row_exists():
    pool, _, orch = _pool_with_relic()
    prepare_relic_hunt(orch, 1, 0)
    assert pool._repo.get_relic("r1").state.value == "in_play"


def test_launch_refused_when_relic_not_indexed():
    pool, _, orch = _pool_with_relic(indexed=False)
    with pytest.raises(FindabilityRefused):
        prepare_relic_hunt(orch, 1, 0)


# --------------------------------------------------------------------------- #
# routing / reveal / trophy / retire                                           #
# --------------------------------------------------------------------------- #


def test_clue_engine_routing_leaves_persona_hunts_untouched():
    pool, _, orch = _pool_with_relic()
    hunt = prepare_relic_hunt(orch, 1, 0)
    assert engine_for(orch, hunt) == "RELIC_ENGINE"
    assert engine_for(orch, _persona_hunt()) == "PERSONA_ENGINE"


def test_reveal_line_has_name_and_onchain_link_only_for_relics():
    pool, _, orch = _pool_with_relic()
    hunt = prepare_relic_hunt(orch, 1, 0)
    line = reveal_extra_line(hunt)
    assert "Maroon Ledger" in line and "basescan.org/token/0xAAA" in line
    assert reveal_extra_line(_persona_hunt()) == ""


def test_trophy_is_sent_to_the_winner():
    pool, _, orch = _pool_with_relic()
    hunt = prepare_relic_hunt(orch, 1, 0)
    deliver_trophy(orch, hunt, _Winner())
    assert orch._trophy_port.sent[0]["to"] == "0xWINNER"


def test_trophy_failure_never_breaks_the_flow():
    """The prize is already paid — a trophy problem must never raise."""
    pool, _, orch = _pool_with_relic()
    hunt = prepare_relic_hunt(orch, 1, 0)

    class _Boom:
        def owner_of(self, c, t): raise RuntimeError("rpc")
        def transfer(self, **k): raise RuntimeError("rpc dead")

    orch._trophy_port = _Boom()
    deliver_trophy(orch, hunt, _Winner())        # must not raise
    assert any("troféu" in n for n in orch.notes)


def test_trophy_is_a_noop_for_persona_hunts():
    pool, _, orch = _pool_with_relic()
    deliver_trophy(orch, _persona_hunt(), _Winner())
    assert orch._trophy_port.sent == []


def test_retire_marks_the_relic_revealed_and_destroys_nothing():
    pool, _, orch = _pool_with_relic()
    hunt = prepare_relic_hunt(orch, 1, 0)
    retire_relic(orch, hunt)
    assert pool._repo.get_relic("r1").state.value == "revealed"
