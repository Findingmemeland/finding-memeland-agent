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


# --------------------------------------------------------------------------- #
# findability: quorum of marketplaces (2026-08-23 rework)                      #
# --------------------------------------------------------------------------- #


def test_quorum_needs_two_surfaces_to_agree():
    """BaseScan cannot be the name gate — it does not index NFT names at all
    (verified against a 17-hour-old control NFT), so as canonical it refused
    every launch. Two marketplaces agreeing answers the original worry about
    caching without using a surface that cannot answer at all."""
    from finding_memeland.persona.relic_findability import QuorumFindability
    found = FakeFindability("opensea", indexed={"goblin accountant"})
    also = FakeFindability("rarible", indexed={"goblin accountant"})
    missing = FakeFindability("rarible", indexed=set())

    assert QuorumFindability((found, also)).is_indexed_by_name("goblin accountant")
    assert not QuorumFindability((found, missing)).is_indexed_by_name("goblin accountant")


def test_quorum_counts_an_unreachable_surface_as_a_no():
    """The gate exists so we never launch on hope."""
    from finding_memeland.persona.relic_findability import QuorumFindability
    found = FakeFindability("opensea", indexed={"x"})
    down = FakeFindability("rarible", raises=True)
    quorum = QuorumFindability((found, down))
    ok, detail = quorum.results("x")
    assert not ok
    assert detail == {"opensea": True, "rarible": None}


def test_quorum_refuses_to_be_built_when_it_could_never_be_met():
    from finding_memeland.persona.relic_findability import QuorumFindability
    with pytest.raises(ValueError, match="quorum"):
        QuorumFindability((FakeFindability("opensea"),), required=2)


def test_opensea_adapter_hits_the_documented_search_endpoint():
    import json as _json

    from finding_memeland.persona.relic_findability import OpenSeaFindability
    seen = {}

    def _get(url, headers):
        seen["url"], seen["headers"] = url, headers
        return _json.dumps({"nfts": [{"contract": "0xABC", "name": "goblin accountant"}]})

    check = OpenSeaFindability(http_get=_get, api_key="k")
    assert check.is_indexed_by_name("goblin accountant", contract="0xabc")
    assert "/api/v2/search?query=" in seen["url"] and "chains=base" in seen["url"]
    assert seen["headers"]["X-API-KEY"] == "k"


def test_opensea_adapter_is_schema_agnostic_and_fail_closed():
    """A marketplace reshaping its JSON must not crash the pipeline, and every
    failure mode must read as 'not findable' rather than 'assume yes'."""
    import json as _json

    from finding_memeland.persona.relic_findability import OpenSeaFindability

    def reshaped(url, headers):
        return _json.dumps({"results": {"items": [{"meta": {"token": {"address": "0xABC"}}}]}})

    assert OpenSeaFindability(http_get=reshaped, api_key="k").is_indexed_by_name(
        "anything", contract="0xabc"
    )

    for broken in (lambda u, h: "not json", lambda u, h: _json.dumps({"nfts": []})):
        assert not OpenSeaFindability(http_get=broken, api_key="k").is_indexed_by_name("x")

    def boom(url, headers):
        raise RuntimeError("502")

    assert not OpenSeaFindability(http_get=boom, api_key="k").is_indexed_by_name("x")

    # No key configured: refuse without spending a request.
    calls = []
    no_key = OpenSeaFindability(http_get=lambda u, h: calls.append(1), api_key="")
    assert not no_key.is_indexed_by_name("x")
    assert not calls


# --------------------------------------------------------------------------- #
# create_relic — the missing step: invent an identity and store it blind        #
# --------------------------------------------------------------------------- #


class _Rev:
    """Visible, reversible transform — lets a test assert the stored blob is NOT
    the plaintext while still round-tripping. NullPoolCipher stores in the clear,
    so it cannot prove the blind property."""

    def encrypt(self, s):
        return "ENC::" + s[::-1]

    def decrypt(self, t):
        assert t.startswith("ENC::")
        return t[len("ENC::"):][::-1]


class _FakeGen:
    """Records what create_relic passed in, so the test can prove the
    anti-repetition inputs are read from the pool and not left to the caller."""

    def __init__(self, names):
        self._names = list(names)
        self.calls = []

    def generate(self, *, register=None, sequence=None, avoid_recent=None, avoid_words=None):
        self.calls.append({
            "register": register, "sequence": sequence,
            "avoid_recent": list(avoid_recent or []), "avoid_words": set(avoid_words or ()),
        })
        name = self._names.pop(0)

        class _G:
            def to_identity(_self):
                return new_identity(name=name, description="lore",
                                    image_prompt="p", solution_terms=["zzz"])
        return _G()


def test_create_relic_stores_encrypted_and_returns_a_usable_id():
    from finding_memeland.persona.relic_mint import create_relic

    repo = FakeRelicRepo()
    pool = RelicPool(repo, _Rev())
    gen = _FakeGen(["goblin accountant"])

    relic_id = create_relic(pool=pool, generator=gen)

    assert relic_id                                   # the DB never gets to invent it
    assert pool.reveal_identity(relic_id).name == "goblin accountant"
    assert "goblin accountant" not in repo.get_identity_ciphertext(relic_id)


def test_create_relic_feeds_the_generator_from_the_pool():
    """Rotation, themes and spent words are read HERE so a caller cannot forget
    them — that forgetting is what produced the monoculture and the repeated
    vocabulary in the 2026-08-23 samples."""
    from finding_memeland.persona.relic_mint import create_relic

    pool = RelicPool(FakeRelicRepo(), _Rev())
    gen = _FakeGen(["goblin accountant", "pickled Ptolemy", "leaky astronaut"])

    ids = [create_relic(pool=pool, generator=gen) for _ in range(3)]

    assert len(set(ids)) == 3
    assert [c["sequence"] for c in gen.calls] == [0, 1, 2]      # domain rotation
    assert gen.calls[0]["avoid_words"] == set()
    assert {"goblin", "accountant"} <= gen.calls[1]["avoid_words"]
    assert {"pickled", "ptolemy"} <= gen.calls[2]["avoid_words"]
    assert gen.calls[2]["avoid_recent"], "themes must be passed too"


def test_create_relic_is_independent_of_minting():
    """Creating is offline and cheap; minting costs gas and can fail. A relic
    exists in the pool before any chain call, so a failed mint never burns an
    identity."""
    from finding_memeland.persona.relic_mint import create_relic

    repo = FakeRelicRepo()
    pool = RelicPool(repo, _Rev())
    relic_id = create_relic(pool=pool, generator=_FakeGen(["clone brunch"]))

    stored = repo.get_relic(relic_id)
    assert stored is not None
    assert stored.contract is None and stored.token_id is None
    assert not stored.is_launchable()          # not minted, not committed
