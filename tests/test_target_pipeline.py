"""Snapshot pipeline — wiring, fail-closed paths, Telegram-safe output."""

from __future__ import annotations

import random

from finding_memeland.target.discovery import (
    MANIFOLD_RUNTIME_LEN,
    DiscoveryStateStore,
    EraDiscovery,
)
from finding_memeland.target.pipeline import SnapshotPipeline
from finding_memeland.target.snapshot import CurationEpoch, SnapshotStore
from finding_memeland.target.sources import ChainUnavailable, RegistryStore

EPOCH = CurationEpoch(epoch_id="e1")
SECRET = "0xfeedfacefeedfacefeedfacefeedfacefeedface"


class MemStore:
    def __init__(self):
        self.blob = None

    def read(self):
        return self.blob

    def write(self, b):
        self.blob = b


class XorCipher:
    def encrypt(self, p):
        return p[::-1]

    def decrypt(self, t):
        return t[::-1]


def build_world(*, classic_break=False):
    """Mundo fake: um contrato manifold (SECRET) com o token 1; as clássicas
    vazias (supply 0) — ou a rebentar, para o caminho de refresh falhado."""
    meta = {"name": "Salt Harbor", "image": "ipfs://img"}

    def fetch_mints(b):
        return [(SECRET, 1)]

    def get_code(c):
        return b"m" * MANIFOLD_RUNTIME_LEN if c == SECRET else b""

    def eth_call(to, data):
        sel = data[:10]
        if to != SECRET and classic_break:
            raise ChainUnavailable("plataforma em baixo")
        if sel == "0x18160ddd":                 # totalSupply
            if to == SECRET:
                raise RuntimeError("revert")    # manifold: sonda densa
            return hex(0)                       # clássicas vazias
        if sel == "0x6352211e":                 # ownerOf (sonda)
            tid = int(data[10:], 16)
            if to == SECRET and tid == 1:
                return "0x" + "11" * 32
            raise RuntimeError("revert")
        raise RuntimeError("selector?")

    discovery = EraDiscovery(fetch_mints=fetch_mints, get_code=get_code,
                             era=(1, 400))
    stores = {k: MemStore() for k in ("d", "r", "s")}
    pipeline = SnapshotPipeline(
        discovery=discovery,
        discovery_store=DiscoveryStateStore(
            cipher=XorCipher(), read=stores["d"].read, write=stores["d"].write),
        registry_store=RegistryStore(
            cipher=XorCipher(), read=stores["r"].read, write=stores["r"].write),
        snapshot_store=SnapshotStore(
            cipher=XorCipher(), read=stores["s"].read, write=stores["s"].write),
        eth_call=eth_call,
        fetch_metadata=lambda c, t: dict(meta),
        owner_is_eoa=lambda c, t: True,
        name_is_unique=lambda n, c, t: True,
        now_iso=lambda: "2026-09-05T20:00:00Z",
        writability_rates={"manifold2021": 0.5},
        cap_exempt=frozenset({"tail2021"}),
    )
    return pipeline, stores


def test_full_run_builds_snapshot_and_gate():
    pipeline, stores = build_world()
    rep = pipeline.run(EPOCH, scan_blocks=10, rng=random.Random(0))
    assert rep.blocks_scanned == 10
    assert rep.registry_counts.get("manifold2021") == 1
    assert rep.snapshot_is_fresh and rep.snapshot_size == 1
    assert rep.gate is not None
    assert rep.gate.rows[0].stratum == "manifold2021"
    assert stores["s"].blob is not None          # snapshot persistido

    out = rep.render()
    assert SECRET not in out                     # Telegram-safe
    assert "manifold2021" in out


def test_refresh_failure_serves_previous_snapshot():
    good, stores = build_world()
    good.run(EPOCH, scan_blocks=5, rng=random.Random(0))
    prev_blob = stores["s"].blob

    broken, bstores = build_world(classic_break=True)
    # partilha o snapshot anterior e o estado
    bstores["s"].blob = prev_blob
    rep = broken.run(EPOCH, scan_blocks=5, rng=random.Random(1))
    assert not rep.snapshot_is_fresh
    assert rep.snapshot_size == 1                # a servir o anterior
    assert rep.gate is not None
    assert "ANTERIOR" in rep.render()
    assert bstores["s"].blob == prev_blob        # não sobrescreveu


def test_refresh_failure_with_no_previous_snapshot_fails_closed():
    broken, _ = build_world(classic_break=True)
    rep = broken.run(EPOCH, scan_blocks=5, rng=random.Random(2))
    assert rep.gate is None
    assert "fail-closed" in rep.render()


def test_second_run_accumulates_scan():
    pipeline, stores = build_world()
    pipeline.run(EPOCH, scan_blocks=10, rng=random.Random(0))
    rep2 = pipeline.run(EPOCH, scan_blocks=10, rng=random.Random(0))
    # mesmo rng semente: blocos repetidos são saltados, mas completa 10 novos
    assert rep2.blocks_scanned == 10
