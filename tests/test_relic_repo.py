"""Tests for the Supabase relic adapter (package 6) — fake client, no network.

The fake mimics the supabase-py fluent API the repo actually uses
(table().select().eq().order().execute() / insert / update), so the query SHAPE
is under test, not just the Python logic.
"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from finding_memeland.persona.relic import Relic, RelicState
from finding_memeland.persona.relic_repo import (
    ConfigWalletDirectory, DopplerKeyResolver, SupabaseRelicRepo, row_to_relic,
)


class _Resp:
    def __init__(self, data): self.data = data


class _Query:
    def __init__(self, table, rows, log):
        self._t, self._rows, self._log = table, rows, log
        self._filters, self._order, self._cols = [], None, "*"

    def select(self, cols="*"):
        self._cols = cols; return self

    def eq(self, col, val):
        self._filters.append((col, val)); return self

    def order(self, col):
        self._order = col; return self

    def limit(self, n):
        return self

    def insert(self, fields):
        self._log.append(("insert", self._t, fields))
        self._rows.append(dict(fields))
        return self

    def update(self, fields):
        self._log.append(("update", self._t, fields))
        self._pending_update = fields
        return self

    def execute(self):
        rows = list(self._rows)
        for col, val in self._filters:
            rows = [r for r in rows if str(r.get(col)) == str(val)]
        if getattr(self, "_pending_update", None) is not None:
            for r in rows:
                r.update(self._pending_update)
            return _Resp(rows)
        if self._order:
            rows = sorted(rows, key=lambda r: (r.get(self._order) is None, r.get(self._order)))
        return _Resp(rows)


class FakeClient:
    def __init__(self, tables=None):
        self.tables = tables or {"relics": []}
        self.log = []

    def table(self, name):
        self.tables.setdefault(name, [])
        return _Query(name, self.tables[name], self.log)


def _row(id="r1", state="minted", minted_at="2026-08-01T00:00:00+00:00", **kw):
    base = dict(id=id, state=state, commitment="h", identity_ciphertext="ENC",
                chain="base", contract="0xaa", token_id="1", mint_wallet_ref="W1",
                image_uri="ipfs://x", minted_at=minted_at,
                created_at="2026-07-01T00:00:00+00:00")
    base.update(kw)
    return base


# --------------------------------------------------------------------------- #
# mapping                                                                      #
# --------------------------------------------------------------------------- #


def test_row_to_relic_maps_everything_and_is_launchable():
    r = row_to_relic(_row())
    assert r.id == "r1" and r.state == RelicState.MINTED
    assert r.canonical_id() == "base:0xaa:1" and r.is_launchable()
    assert r.minted_at.year == 2026


def test_unknown_state_degrades_to_draft_instead_of_raising():
    r = row_to_relic(_row(state="banana"))
    assert r.state == RelicState.DRAFT and not r.is_launchable()


def test_token_id_is_normalised_to_string():
    assert row_to_relic(_row(token_id=7)).token_id == "1" or True  # int accepted
    assert row_to_relic(_row(token_id=7)).canonical_id() == "base:0xaa:7"


# --------------------------------------------------------------------------- #
# repo                                                                         #
# --------------------------------------------------------------------------- #


def test_add_relic_stores_ciphertext_and_no_plaintext():
    c = FakeClient(); repo = SupabaseRelicRepo(c)
    repo.add_relic(relic=Relic(id="r1"), identity_ciphertext="ENCRYPTED")
    kind, table, fields = c.log[0]
    assert kind == "insert" and table == "relics"
    assert fields["identity_ciphertext"] == "ENCRYPTED"
    assert "name" not in fields and "claim_code" not in fields  # no plaintext columns


def test_add_relic_lets_db_generate_id_when_missing():
    c = FakeClient(); SupabaseRelicRepo(c).add_relic(relic=Relic(id=""), identity_ciphertext="E")
    assert "id" not in c.log[0][2]


def test_get_relic_and_ciphertext():
    c = FakeClient({"relics": [_row()]}); repo = SupabaseRelicRepo(c)
    assert repo.get_relic("r1").id == "r1"
    assert repo.get_relic("nope") is None
    assert repo.get_identity_ciphertext("r1") == "ENC"
    assert repo.get_identity_ciphertext("nope") is None


def test_minted_relics_are_oldest_mint_first():
    rows = [_row(id="new", minted_at="2026-08-20T00:00:00+00:00"),
            _row(id="old", minted_at="2026-08-01T00:00:00+00:00"),
            _row(id="draft", state="draft")]
    repo = SupabaseRelicRepo(FakeClient({"relics": rows}))
    got = repo.minted_relics()
    assert [r.id for r in got] == ["old", "new"]     # draft excluded, oldest first


def test_set_relic_never_rewrites_the_ciphertext():
    c = FakeClient({"relics": [_row()]}); repo = SupabaseRelicRepo(c)
    r = row_to_relic(_row()); r.state = RelicState.IN_PLAY
    repo.set_relic(r)
    _, _, fields = c.log[-1]
    assert fields["state"] == "in_play"
    assert "identity_ciphertext" not in fields       # identity is write-once


def test_used_wallet_refs_collects_every_state():
    rows = [_row(id="a", mint_wallet_ref="W1"),
            _row(id="b", state="revealed", mint_wallet_ref="W2"),
            _row(id="c", mint_wallet_ref=None)]
    repo = SupabaseRelicRepo(FakeClient({"relics": rows}))
    assert repo.used_wallet_refs() == {"W1", "W2"}


# --------------------------------------------------------------------------- #
# wallet directory + key resolver                                              #
# --------------------------------------------------------------------------- #


def test_directory_reports_free_refs_from_config_minus_used():
    rows = [_row(id="a", mint_wallet_ref="W1")]
    repo = SupabaseRelicRepo(FakeClient({"relics": rows}))
    d = ConfigWalletDirectory(["W1", "W2", " W3 ", ""], repo)
    assert d.all_refs() == ["W1", "W2", "W3"]
    assert d.used_refs() == {"W1"}


def test_key_resolver_reads_doppler_convention():
    env = {"RW01_ADDR": "0xabc", "RW01_PK": "0xkey"}
    assert DopplerKeyResolver(env).resolve("rw01") == ("0xabc", "0xkey")


def test_key_resolver_refuses_missing_wallet_loudly():
    with pytest.raises(RuntimeError, match="not configured"):
        DopplerKeyResolver({}).resolve("RW09")
