"""Snapshot pool — persistence, drawing, lazy writability, the gate."""

from __future__ import annotations

import random

import pytest

from finding_memeland.target.selector import CurationEpoch, SelectionRefused
from finding_memeland.target.snapshot import (
    GateVerdict,
    Snapshot,
    SnapshotEntry,
    SnapshotIntegrityError,
    SnapshotSource,
    SnapshotStore,
    gate_verdict,
    select_writable,
    snapshot_selector,
)
from finding_memeland.target.selector import metadata_hash

EPOCH = CurationEpoch(epoch_id="e1")


def entry(i: int, name: str = "Whispering Harbor") -> SnapshotEntry:
    meta = {"name": f"{name} #{i}", "image": f"ipfs://img{i}",
            "description": "quiet"}
    return SnapshotEntry(
        contract=f"0x{i:040x}", token_id=i, name=name,
        name_onchain=f"{name} #{i}", metadata=meta,
        metadata_sha256=metadata_hash(meta), platform="testplat",
    )


def snap(n: int = 5) -> Snapshot:
    return Snapshot(epoch_id="e1", built_at="2026-09-04T00:00:00Z",
                    entries=[entry(i) for i in range(1, n + 1)])


class MemStore:
    def __init__(self):
        self.blob: str | None = None

    def read(self):
        return self.blob

    def write(self, blob: str):
        self.blob = blob


class XorCipher:
    """Not-crypto stand-in proving save/load round-trips THROUGH the cipher."""

    def encrypt(self, plaintext: str) -> str:
        return plaintext[::-1]

    def decrypt(self, token: str) -> str:
        return token[::-1]


def make_store(mem: MemStore) -> SnapshotStore:
    return SnapshotStore(cipher=XorCipher(), read=mem.read, write=mem.write)


# --------------------------------------------------------------------------- #
# Store                                                                        #
# --------------------------------------------------------------------------- #


def test_save_load_round_trip():
    mem = MemStore()
    store = make_store(mem)
    store.save(snap(3))
    assert mem.blob is not None and "Whispering" not in mem.blob  # ciphered
    loaded = store.load()
    assert loaded.size() == 3
    assert loaded.epoch_id == "e1"
    assert loaded.entries[0].name == "Whispering Harbor"


def test_load_missing_returns_none():
    assert make_store(MemStore()).load() is None


def test_tampered_entry_hash_fails_closed():
    mem = MemStore()
    store = make_store(mem)
    s = snap(1)
    object.__setattr__(s.entries[0], "metadata_sha256", "0" * 64)
    store.save(s)
    with pytest.raises(SnapshotIntegrityError) as e:
        store.load()
    assert "Whispering" not in str(e.value)      # never names an entry


def test_garbled_blob_fails_closed():
    mem = MemStore()
    mem.blob = "not-a-snapshot"
    with pytest.raises(SnapshotIntegrityError):
        make_store(mem).load()


# --------------------------------------------------------------------------- #
# Drawing                                                                      #
# --------------------------------------------------------------------------- #


def test_epoch_mismatch_refuses():
    src = SnapshotSource(snap(3))
    with pytest.raises(SelectionRefused) as e:
        next(src.candidates(CurationEpoch(epoch_id="e2")))
    assert "refresh the snapshot" in str(e.value)


def test_snapshot_selector_draws_offline_and_uniformly():
    s = snap(20)
    seen = set()
    for seed in range(30):
        sel = snapshot_selector(s, rng=random.Random(seed))
        seen.add(sel.select(EPOCH).token_id)
    assert len(seen) > 5              # different seeds, different picks
    t = snapshot_selector(s, rng=random.Random(1)).select(EPOCH)
    assert t.name == "Whispering Harbor"          # base name from the store
    assert t.metadata_sha256 == s.entries[t.token_id - 1].metadata_sha256


def test_select_writable_skips_unwritable_draws():
    s = snap(10)
    sel = snapshot_selector(s, rng=random.Random(7))
    verdicts = iter([False, None, True])
    picked = select_writable(sel, EPOCH,
                             is_writable=lambda t: next(verdicts))
    assert picked.name == "Whispering Harbor"


def test_select_writable_exhaustion_refuses():
    sel = snapshot_selector(snap(10), rng=random.Random(7))
    with pytest.raises(SelectionRefused) as e:
        select_writable(sel, EPOCH, is_writable=lambda t: False, max_draws=3)
    assert "re-measure" in str(e.value)


# --------------------------------------------------------------------------- #
# Gate                                                                         #
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("pool,rate,verdict", [
    (500_000, 0.22, "GREEN"),        # 110k effective
    (200_000, 0.05, "RED"),          # 10k
    (300_000, 0.20, "AMBER"),        # 60k
    (100_000, 1.0, "GREEN"),         # exactly the floor
    (20_000, 1.0, "RED"),            # exactly the ceiling
])
def test_gate_thresholds(pool, rate, verdict):
    v = gate_verdict(pool, rate)
    assert isinstance(v, GateVerdict)
    assert v.verdict == verdict


def test_gate_red_detail_carries_the_anticircularity_rule():
    assert "never loosen quality filters" in gate_verdict(10_000, 0.5).detail
