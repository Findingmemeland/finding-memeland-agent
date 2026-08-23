"""Tests for relic_mint (package 2) — offline, fakes only. The on-chain adapters
(Web3Minter, BaseScanFindability) are exercised only through their pure parsing
paths / fakes; live behaviour is proven by the mainnet dry-run (Pedro)."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from finding_memeland.persona.relic import Relic, RelicState, new_identity, verify_relic_commitment
from finding_memeland.persona.relic_pool import RelicPool, FakeRelicRepo, NullPoolCipher
from finding_memeland.persona.relic_wallets import (
    WalletPool, FakeWalletDirectory, FakeKeyResolver,
)
from finding_memeland.persona.relic_mint import (
    mint_relic, FakeMinter, FakeImageGen, compose_onchain_description, json_escape,
    generate_symbol, generate_artist,
)
from finding_memeland.persona.relic_findability import (
    assert_findable_or_refuse, FindabilityRefused, FakeFindability, BaseScanFindability,
)
from finding_memeland.persona.relic_decoys import DecoyConfig, plan_decoys


# --------------------------------------------------------------------------- #
# wallets                                                                      #
# --------------------------------------------------------------------------- #


def test_pick_free_returns_unused_and_never_carries_the_key():
    wp = WalletPool(FakeWalletDirectory(["W1", "W2"], used={"W1"}), FakeKeyResolver())
    h = wp.pick_free()
    assert h.ref == "W2" and h.address == "0xADDR_W2"
    assert not hasattr(h, "private_key") and "key" not in vars(h)


def test_wallet_pool_exhaustion_raises_fund_more():
    wp = WalletPool(FakeWalletDirectory(["W1"], used={"W1"}), FakeKeyResolver())
    with pytest.raises(RuntimeError, match="exhausted"):
        wp.pick_free()


def test_signing_key_is_fetched_only_on_demand():
    wp = WalletPool(FakeWalletDirectory(["W1"]), FakeKeyResolver())
    addr, key = wp.signing_key("W1")
    assert addr == "0xADDR_W1" and key == "0xKEY_W1"


# --------------------------------------------------------------------------- #
# mint flow                                                                    #
# --------------------------------------------------------------------------- #


def _mint_one():
    repo = FakeRelicRepo(); pool = RelicPool(repo, NullPoolCipher())
    ident = new_identity(name="Maroon Ledger", description="kept the books",
                         image_prompt="a glowing ledger", solution_terms=["Cassandra"])
    pool.add(Relic(id="r1"), ident)
    wp = WalletPool(FakeWalletDirectory(["W1"]), FakeKeyResolver())
    minter = FakeMinter()
    res = mint_relic(relic_id="r1", pool=pool, wallets=wp, image_gen=FakeImageGen(), minter=minter)
    return repo, pool, ident, minter, res


def test_mint_puts_code_in_the_onchain_description():
    _, _, ident, minter, _ = _mint_one()
    assert f"code: {ident.claim_code}" in minter.minted[0]["description"]


def test_mint_contract_name_is_the_relic_name_not_a_shared_cluster():
    _, _, _, minter, _ = _mint_one()
    assert minter.minted[0]["name"] == "Maroon Ledger"


def test_mint_records_coords_commitment_and_verifies():
    repo, _, ident, _, res = _mint_one()
    r = repo.get_relic("r1")
    assert r.state == RelicState.MINTED and r.mint_wallet_ref == "W1" and r.contract == res.contract
    assert verify_relic_commitment(r.canonical_id(), ident.claim_code, ident.salt, r.commitment)


def test_mint_uses_varied_symbol_and_artist_no_shared_literal():
    import re
    _, _, _, minter, _ = _mint_one()
    rec = minter.minted[0]
    assert re.fullmatch(r"[A-Z]{3,5}", rec["symbol"]) and rec["symbol"] != "RELIC"
    assert rec["artist"] and rec["artist"].strip()
    # variation across relics: no single symbol/artist an observer could filter on
    syms = {generate_symbol() for _ in range(40)}
    arts = {generate_artist() for _ in range(40)}
    assert len(syms) > 1 and len(arts) > 1  # not a shared literal


def test_compose_and_json_escape():
    d = compose_onchain_description('she said "no"', "ABCD2345")
    assert "code: ABCD2345" in d
    esc = json_escape(d)
    assert '\\"no\\"' in esc  # quotes escaped for on-chain JSON safety


# --------------------------------------------------------------------------- #
# findability gate (fail-closed)                                               #
# --------------------------------------------------------------------------- #


def test_gate_refuses_when_canonical_not_indexed():
    canon = FakeFindability("basescan", indexed=set())
    with pytest.raises(FindabilityRefused, match="fail-closed"):
        assert_findable_or_refuse("Maroon Ledger", canonical=canon)


def test_gate_passes_when_canonical_indexed_and_records_secondary():
    canon = FakeFindability("basescan", indexed={"Maroon Ledger"})
    sec_ok = FakeFindability("opensea", indexed={"Maroon Ledger"})
    sec_down = FakeFindability("rarible", raises=True)
    rep = assert_findable_or_refuse("Maroon Ledger", canonical=canon, secondary=(sec_ok, sec_down))
    assert rep.canonical_ok and rep.secondary["opensea"] is True
    assert rep.secondary["rarible"] is None  # unreachable secondary tolerated, never blocks


def test_basescan_parser_no_match_and_contract_disambiguation():
    b = BaseScanFindability(http_get=lambda url: "Your search - did not match any records.")
    assert b.is_indexed_by_name("Maroon Ledger") is False
    b2 = BaseScanFindability(http_get=lambda url: "<a>Maroon Ledger</a> 0xAAA contract")
    assert b2.is_indexed_by_name("Maroon Ledger", contract="0xaaa") is True
    assert b2.is_indexed_by_name("Maroon Ledger", contract="0xbbb") is False  # namesake rejected


# --------------------------------------------------------------------------- #
# decoy scheduler                                                              #
# --------------------------------------------------------------------------- #


def _cfg(**kw):
    base = dict(target_pool_size=5, min_gap_s=100, max_gap_s=100, max_batch=1)
    base.update(kw)
    return DecoyConfig(**base)


def test_decoys_wait_without_a_free_wallet():
    d = plan_decoys(cfg=_cfg(), pool_size=0, free_wallets=0)
    assert d.mint_now == 0 and "no free wallet" in d.reason


def test_decoys_wait_at_target():
    d = plan_decoys(cfg=_cfg(), pool_size=5, free_wallets=10)
    assert d.mint_now == 0 and "target" in d.reason


def test_decoys_respect_min_cadence_since_last_mint():
    now = datetime(2026, 8, 22, tzinfo=timezone.utc)
    d = plan_decoys(cfg=_cfg(min_gap_s=3600), pool_size=0, free_wallets=10,
                    now=now, last_mint_at=now - timedelta(seconds=60))
    assert d.mint_now == 0 and "too soon" in d.reason


def test_decoys_mint_below_target_capped_by_wallets_and_batch():
    d = plan_decoys(cfg=_cfg(target_pool_size=10, max_batch=3), pool_size=2, free_wallets=1,
                    gap_fn=lambda c: 100)
    assert d.mint_now == 1  # min(max_batch=3, free_wallets=1, want=8)


def test_decoys_jitter_is_injectable():
    now = datetime(2026, 8, 22, tzinfo=timezone.utc)
    d = plan_decoys(cfg=_cfg(min_gap_s=10, max_gap_s=999), pool_size=5, free_wallets=1,
                    now=now, gap_fn=lambda c: 42)
    assert d.next_check_at == now + timedelta(seconds=42)
