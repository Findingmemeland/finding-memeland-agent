"""build_target composes the production graph from Settings + fakes —
offline. Pins: fail-closed on missing config, the blob keys, the gate over
the stored snapshot, /scan and /snapshot as separate commands, the
confirmation fingerprint."""

from __future__ import annotations

import json

import pytest
from cryptography.fernet import Fernet

from finding_memeland.config import Settings
from finding_memeland.target.wiring import BLOB_REGISTRY, BLOB_SNAPSHOT, build_target

C = "0x3b3ee1931dc30c1957379fac9aba94d1c48a5405"


class FakeRepo:
    def __init__(self):
        self.blobs: dict[str, str] = {}

    def get_blob(self, key):
        return self.blobs.get(key)

    def put_blob(self, key, payload):
        self.blobs[key] = payload


def settings(**over) -> Settings:
    base = dict(
        _env_file=None,
        target_launch=True, target_pool_key=Fernet.generate_key().decode(),
        eth_rpc_url="https://alchemy.example/v2/key", base_rpc_url="https://base.example",
        target_epoch_id="e1", target_canary_block=12_965_000, target_canary_mints=3,
        target_writability_rates="foundation:0.5,tail2021:0.35",
        target_uniqueness_rates="foundation:0.66,tail2021:0.9",
        target_public_rpcs_ethereum="https://pub0,https://pub1",
        target_public_rpcs_base="https://pubb0",
        target_ipfs_gateways="https://gw0/ipfs/,https://gw1/ipfs/",
        target_ipfs_gateway="https://gateway.pinata.cloud/ipfs/",
        rarible_api_key="rk", anthropic_api_key="ak",
    )
    base.update(over)
    return Settings(**base)


def rpc_ok(url, body, headers):
    """A node that sees code everywhere (R2 canaries pass) and has no
    tokens (every eth_call answers empty) — the refresh builds an EMPTY
    snapshot, honestly."""
    req = json.loads(body)
    if req["method"] == "eth_getLogs":
        return json.dumps({"jsonrpc": "2.0", "id": 1, "result": []})
    if req["method"] == "eth_getCode":
        return json.dumps({"jsonrpc": "2.0", "id": 1, "result": "0x6001"})
    return json.dumps({"jsonrpc": "2.0", "id": 1, "result": "0x"})


def build(s=None, repo=None):
    return build_target(s or settings(), anthropic=object(), repo=repo or FakeRepo(),
                        http_get=lambda u, h: "{}", http_post=rpc_ok,
                        http_get_bytes=lambda u, h: b"")


def test_missing_config_refuses_with_names_only():
    s = settings(target_uniqueness_rates="", target_canary_mints=0)
    with pytest.raises(RuntimeError) as e:
        build(s)
    msg = str(e.value)
    assert "target_uniqueness_rates" in msg and "canary" in msg
    assert "target_pool_key" not in msg
    # the same list feeds assert_ready_for_hunt
    with pytest.raises(RuntimeError) as e2:
        settings(target_uniqueness_rates="", fmml_token_address="t",
                 hot_wallet_private_key="k", integrity_salt="s",
                 payout_cap_fmml=1).assert_ready_for_hunt()
    assert "target_uniqueness_rates" in str(e2.value)


def test_canary_needs_both_block_and_count():
    s = settings(target_canary_block=0)
    assert any("canary" in m for m in s.target_missing())
    s = settings(target_canary_mints=0)
    assert any("canary" in m for m in s.target_missing())
    assert not any("canary" in m for m in settings().target_missing())


def test_wiring_builds_ports_with_every_production_piece():
    w = build()
    p = w.ports
    assert p.epoch.epoch_id == "e1" and p.epoch.max_snapshot_age_days == 14
    assert p.live_check is not None and p.resolve_link is not None
    assert p.spray is not None and p.max_total_hold_s == 12 * 3600
    assert w.gate_now() is None                       # no snapshot yet
    assert w.snapshot_fingerprint() == ""
    # providers: index-aligned (ethereum[i], base[i], gateway[i])
    assert p.live_hash is not None                    # resolved once, at the void
    gen = p.live_check._generic                       # noqa: SLF001
    names = [pr.name for pr in gen._providers]        # noqa: SLF001
    assert names == ["provider0", "provider1"]
    assert gen._providers[1].rpc_urls == {"ethereum": "https://pub1"}   # noqa: SLF001
    assert gen._providers[1].gateway == "https://gw1/ipfs/"            # noqa: SLF001


def test_scan_and_snapshot_are_separate_and_blobs_are_ciphertext():
    repo = FakeRepo()
    w = build(repo=repo)
    # canary: fetch_mints returns [] but canary_mints=3 → refused, nothing scanned
    out = w.scan(5)
    assert "RECUSADO" in out
    assert BLOB_REGISTRY in repo.blobs and BLOB_SNAPSHOT not in repo.blobs
    rep = w.snapshot()
    assert rep.blocks_scanned == 0 and rep.scan_canary_ok
    assert rep.snapshot_is_fresh, rep.note
    assert BLOB_SNAPSHOT in repo.blobs
    for v in repo.blobs.values():
        assert v.startswith("gAAAA") and C not in v         # Fernet tokens
    # a stored snapshot with an EMPTY pool (the node has no tokens) gates RED
    g = w.gate_now()
    assert g is not None and g.verdict == "RED"
    assert w.snapshot_fingerprint().startswith("e1@")


def test_snapshot_fingerprint_changes_when_snapshot_is_rebuilt():
    repo = FakeRepo()
    w = build(repo=repo)
    w.snapshot()
    first = w.snapshot_fingerprint()
    w.snapshot()
    assert w.snapshot_fingerprint() != first


def test_keyed_gateway_has_no_default_and_is_required():
    """P0 (auditoria 09/09): ipfs.io era o default e está morto; um default
    morto alimentava um post público. Sem default, e nomeado em falta."""
    assert Settings(_env_file=None).target_ipfs_gateway == ""
    s = settings(target_ipfs_gateway="")
    assert any("target_ipfs_gateway " in m for m in s.target_missing())


def test_live_hash_is_tri_state():
    from finding_memeland.target.hunt import LIVE_HASH_UNAVAILABLE, LIVE_HASH_UNRESOLVABLE
    from finding_memeland.target.selector import Target
    t = Target(chain="ethereum", contract=C, token_id=1, name="x", name_onchain="x",
               description="", image="", metadata_sha256="m", epoch="e1",
               token_uri="ipfs://QmSiuJazyPgzAVqBiW3LMNdjAG4uaZFqzMwzU2kGS2KmCN",
               content_id="ipfs:QmSiuJazyPgzAVqBiW3LMNdjAG4uaZFqzMwzU2kGS2KmCN")
    from finding_memeland.target.hunt import SealedTarget
    sealed = SealedTarget(target=t, salt="s" * 32, commitment="c" * 64)

    def node(reply):
        def post(url, body, headers):
            req = json.loads(body)
            if req["method"] == "eth_call":
                return json.dumps(reply)
            return json.dumps({"jsonrpc": "2.0", "id": 1, "result": "0x6001"})
        return post
    abi = ("0x" + (32).to_bytes(32, "big").hex() + (52).to_bytes(32, "big").hex()
           + b"ipfs://QmSiuJazyPgzAVqBiW3LMNdjAG4uaZFqzMwzU2kGS2KmCN".hex() + "00" * 12)
    ok = {"jsonrpc": "2.0", "id": 1, "result": abi}
    revert = {"jsonrpc": "2.0", "id": 1, "error": {"code": 3, "message": "execution reverted"}}

    def boom(u, h):
        raise ConnectionError("pinata 503")
    w = build_target(settings(), anthropic=object(), repo=FakeRepo(),
                     http_get=boom, http_post=node(ok), http_get_bytes=lambda u, h: b"")
    assert w.ports.live_hash(sealed).status == LIVE_HASH_UNAVAILABLE      # our gateway
    w = build_target(settings(), anthropic=object(), repo=FakeRepo(),
                     http_get=boom, http_post=node(revert), http_get_bytes=lambda u, h: b"")
    assert w.ports.live_hash(sealed).status == LIVE_HASH_UNRESOLVABLE     # the chain
    w = build_target(settings(), anthropic=object(), repo=FakeRepo(),
                     http_get=lambda u, h: json.dumps({"name": "x", "image": "ipfs://i"}),
                     http_post=node(ok), http_get_bytes=lambda u, h: b"")
    lh = w.ports.live_hash(sealed)
    assert lh.status == "resolved" and len(lh.sha256) == 64
