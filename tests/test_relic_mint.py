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
    generate_symbol, generate_artist, generate_attributes,
    generate_provenance_hash,
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
    """O código TEM de ir na descrição on-chain — é o que o vencedor lê.
    Já não se afirma o prefixo: "code: " era uma constante partilhada por todo o
    pool e um scraper de metadata apanhava-o inteiro (auditoria P0-1)."""
    _, _, ident, minter, _ = _mint_one()
    assert ident.claim_code in minter.minted[0]["description"]


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
    assert rec["attributes"] and rec["attributes"].strip()
    # variation across relics: no single symbol/artist an observer could filter on
    syms = {generate_symbol() for _ in range(40)}
    arts = {generate_artist() for _ in range(40)}
    assert len(syms) > 1 and len(arts) > 1  # not a shared literal


# --------------------------------------------------------------------------- #
# Anti-fingerprint (auditoria 2026-08-26, P0-1). As três assinaturas partilhadas
# que permitiam listar o pool inteiro com uma query. Cada teste falha se alguma
# delas voltar a ser constante.
# --------------------------------------------------------------------------- #


def test_provenance_hash_is_unique_per_relic():
    """Sem isto o runtime bytecode é idêntico em todas as relics — e um match
    exacto de código num indexador devolve o pool completo."""
    seeds = {generate_provenance_hash() for _ in range(50)}
    assert len(seeds) == 50
    for s in seeds:
        assert s.startswith("0x") and len(s) == 66


def test_code_prefix_is_not_a_shared_literal():
    """'\n\ncode: ' era uma constante em todas as descrições on-chain: um
    scraper de metadata apanhava o pool sem tocar no bytecode."""
    ds = {compose_onchain_description("lore", "ABCD2345") for _ in range(60)}
    assert len(ds) > 1
    assert all("ABCD2345" in d and d.startswith("lore") for d in ds)


def test_attributes_shape_varies_and_stays_valid_json():
    import json as _json
    shapes = set()
    for _ in range(60):
        attrs = _json.loads(generate_attributes())
        assert 1 <= len(attrs) <= 3
        assert all(set(a) == {"trait_type", "value"} and a["value"] for a in attrs)
        shapes.add((len(attrs), tuple(sorted(a["trait_type"] for a in attrs))))
    assert len(shapes) > 1  # nem a contagem nem os nomes dos traits são fixos


def test_compose_and_json_escape():
    d = compose_onchain_description('she said "no"', "ABCD2345")
    assert "ABCD2345" in d
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


# --------------------------------------------------------------------------- #
# P1-3 · a carteira é reservada ANTES de assinar                               #
# --------------------------------------------------------------------------- #


class _ExplodingMinter(FakeMinter):
    """Minta on-chain e morre antes de o resultado ser gravado — o cenário exacto
    do P1-3 (timeout do recibo, restart do Railway, erro de RPC)."""

    def deploy_and_mint(self, **kw):
        super().deploy_and_mint(**kw)
        raise RuntimeError("processo morreu depois de transmitir a transacção")


def test_wallet_is_reserved_before_signing_so_a_crash_never_frees_it():
    repo = FakeRelicRepo(); pool = RelicPool(repo, NullPoolCipher())
    ident = new_identity(name="Maroon Ledger", description="kept the books",
                         image_prompt="p", solution_terms=["x"])
    pool.add(Relic(id="r1"), ident)
    wallets = WalletPool(FakeWalletDirectory(["W1", "W2"]), FakeKeyResolver())

    with pytest.raises(RuntimeError):
        mint_relic(relic_id="r1", pool=pool, wallets=wallets,
                   image_gen=FakeImageGen(), minter=_ExplodingMinter())

    # A carteira ficou gasta apesar de o mint nunca ter sido gravado. Sem a
    # reserva, o mint seguinte reutilizava-a e ligava duas relics on-chain.
    assert repo.get_relic("r1").mint_wallet_ref == "W1"


def test_mint_retry_reuses_the_reserved_wallet_instead_of_burning_another():
    """A reserva do P1-3 era desfeita pelo próprio retry: pedia carteira nova e
    sobrescrevia o mint_wallet_ref, devolvendo a primeira ao conjunto livre."""
    repo = FakeRelicRepo(); pool = RelicPool(repo, NullPoolCipher())
    ident = new_identity(name="Maroon Ledger", description="d", image_prompt="p",
                         solution_terms=["x"])
    pool.add(Relic(id="r1"), ident)
    wallets = WalletPool(FakeWalletDirectory(["W1", "W2"]), FakeKeyResolver())

    with pytest.raises(RuntimeError):
        mint_relic(relic_id="r1", pool=pool, wallets=wallets,
                   image_gen=FakeImageGen(), minter=_ExplodingMinter())
    assert repo.get_relic("r1").mint_wallet_ref == "W1"

    # segunda tentativa: a MESMA carteira, e a W2 fica intacta
    minter = FakeMinter()
    mint_relic(relic_id="r1", pool=pool, wallets=wallets,
               image_gen=FakeImageGen(), minter=minter)
    assert minter.minted[0]["wallet_ref"] == "W1"
    assert repo.get_relic("r1").mint_wallet_ref == "W1"


# --------------------------------------------------------------------------- #
# Manifold-proxy minter (probe 2026-08-26, Probe_Manifold_Proxy.md)             #
# --------------------------------------------------------------------------- #


def test_manifold_artifact_is_the_real_proxy_runtime():
    """The artifact carries the constructor-only deployer AND the 298 bytes of a
    real Manifold ERC721Creator proxy read from Base, plus its implementation."""
    from finding_memeland.persona.relic_mint import load_manifold_artifact

    art = load_manifold_artifact()
    ctor = next(e for e in art["abi"] if e["type"] == "constructor")
    assert [i["type"] for i in ctor["inputs"]] == ["address", "string", "string", "bytes"]
    runtime = bytes.fromhex(art["manifold_runtime"][2:])
    assert len(runtime) == 298                       # the Manifold proxy, verbatim
    assert runtime[:4] == bytes.fromhex("60806040")   # solc prologue
    assert runtime[-2:] == bytes.fromhex("0033")      # CBOR trailer terminator
    assert art["manifold_implementation"].lower() == "0x95d452fc85869a7834189f41ec6bb0915f943aa3"
    assert art["bytecode"].startswith("0x")


class _RecordingPinner:
    def __init__(self):
        self.pinned: list[tuple[bytes, str]] = []

    def pin(self, data: bytes, *, name: str = "relic.png") -> str:
        self.pinned.append((data, name))
        return f"ipfs://bafyMETA{len(self.pinned)}"


class _FakeManifoldMinter:
    """ManifoldMinter with `_send` replaced: records the three-step call and
    hands back the coordinates the chain would."""

    def __init__(self, **kw):
        from finding_memeland.persona.relic_mint import ManifoldMinter

        self.sent: list[dict] = []
        outer = self

        class _M(ManifoldMinter):
            def _send(self, **kw2):
                outer.sent.append(kw2)
                return "0xPROXY000000000000000000000000000000000001", 1, "0xminttx"

        self.minter = _M(**kw)


def _manifold_minter():
    from finding_memeland.persona.relic_mint import load_manifold_artifact

    pinner = _RecordingPinner()
    wrap = _FakeManifoldMinter(
        web3=None, wallets=WalletPool(FakeWalletDirectory(["RW01"]), FakeKeyResolver()),
        pinner=pinner, artifact=load_manifold_artifact(),
    )
    return wrap, pinner


def test_manifold_minter_pins_the_metadata_and_mints_with_its_uri():
    import json

    wrap, pinner = _manifold_minter()
    res = wrap.minter.deploy_and_mint(
        name="Maroon Ledger", symbol="MLDG",
        description='kept the "books"\n\ncode: ABCDEFGH', image_uri="ipfs://bafyIMG",
        attributes='[{"trait_type":"maker","value":"Vex Rue"}]',
        provenance_hash="0x" + "ab" * 32, wallet_ref="RW01",
    )
    # The metadata JSON went to the pinner, with the four fields, properly escaped.
    data, name = pinner.pinned[0]
    meta = json.loads(data)
    assert name == "metadata.json"
    assert meta["name"] == "Maroon Ledger"
    assert meta["description"] == 'kept the "books"\n\ncode: ABCDEFGH'
    assert meta["image"] == "ipfs://bafyIMG"
    assert meta["attributes"] == [{"trait_type": "maker", "value": "Vex Rue"}]
    # The chain call got the pinned URI and the relic wallet, never the key.
    sent = wrap.sent[0]
    assert sent["token_uri"] == "ipfs://bafyMETA1"
    assert sent["deployer_address"] == "0xADDR_RW01"
    assert sent["name"] == "Maroon Ledger" and sent["symbol"] == "MLDG"
    assert res.contract.startswith("0xPROXY") and res.token_id == "1"
    assert res.image_uri == "ipfs://bafyIMG"


def test_manifold_minter_points_at_the_crowd_implementation_by_default():
    """The camouflage is the implementation ADDRESS thousands of Manifold proxies
    hold in their EIP-1967 slot. A different address — even our own copy of
    their code — would make the pool a class of one again, so an override is
    refused unless it is explicit."""
    from finding_memeland.persona.relic_mint import ManifoldMinter, load_manifold_artifact

    art = load_manifold_artifact()
    default = ManifoldMinter(web3=None, wallets=None, pinner=None, artifact=art)
    assert default.implementation.lower() == art["manifold_implementation"].lower()
    # Same address in any casing is not an override.
    same = ManifoldMinter(web3=None, wallets=None, pinner=None, artifact=art,
                          implementation=art["manifold_implementation"].upper().replace("0X", "0x"))
    assert same.implementation.lower() == art["manifold_implementation"].lower()
    with pytest.raises(RuntimeError, match="differs from the artifact"):
        ManifoldMinter(web3=None, wallets=None, pinner=None, artifact=art,
                       implementation="0x" + "11" * 20)
    over = ManifoldMinter(web3=None, wallets=None, pinner=None, artifact=art,
                          implementation="0x" + "11" * 20, allow_implementation_override=True)
    assert over.implementation == "0x" + "11" * 20


def test_mint_relic_through_manifold_binds_the_commitment_to_the_proxy():
    """End to end through mint_relic: the commitment is computed over
    base:<proxy>:<tokenId> and verifies — same frozen protocol, new contract."""
    from finding_memeland.persona.relic import relic_canonical_id, verify_relic_commitment
    from finding_memeland.persona.relic_mint import mint_relic

    pool = RelicPool(FakeRelicRepo(), NullPoolCipher())
    ident = new_identity(name="Maroon Ledger", description="kept the books",
                         image_prompt="a brass ledger", solution_terms=["Cassandra"])
    pool.add(Relic(id="r1"), ident)
    wrap, pinner = _manifold_minter()
    res = mint_relic(relic_id="r1", pool=pool, wallets=wrap.minter._wallets,
                     image_gen=FakeImageGen(), minter=wrap.minter)
    stored = pool._repo.get_relic("r1")
    cid = relic_canonical_id("base", res.contract, res.token_id)
    assert stored.contract == res.contract and stored.token_id == "1"
    assert verify_relic_commitment(cid, ident.claim_code, ident.salt, stored.commitment)
    # The claim code rides in the pinned description, not on-chain code.
    assert ident.claim_code in pinner.pinned[0][0].decode()
