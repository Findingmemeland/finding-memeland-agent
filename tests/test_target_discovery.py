"""Era discovery — scan accumulation, classification, secrecy."""

from __future__ import annotations

import random

from finding_memeland.target.discovery import (
    MANIFOLD_RUNTIME_LEN,
    DiscoveryState,
    DiscoveryStateStore,
    EraDiscovery,
)
from finding_memeland.target.sources import ContractRegistry

SECRET = "0xfeedfacefeedfacefeedfacefeedfacefeedface"


def make_world(mints_by_block, codes):
    def fetch(b):
        return mints_by_block.get(b, [])

    def code(c):
        return codes[c]
    return fetch, code


def test_scan_accumulates_and_skips_scanned():
    fetch, code = make_world(
        {100: [("0xA", 1)], 200: [("0xA", 2), ("0xB", 1)]},
        {"0xa": b"x" * 100, "0xb": b"y" * 200})
    d = EraDiscovery(fetch_mints=fetch, get_code=code, era=(100, 200))
    st = DiscoveryState()
    rng = random.Random(1)
    done = d.scan(st, 40, rng)          # era tem ~101 blocos; 40 novos
    assert done == 40
    assert len(st.scanned) == 40
    before = dict(st.contracts["0xa"]) if "0xa" in st.contracts else None
    d.scan(st, 40, rng)                 # não re-varre os mesmos
    assert len(st.scanned) == 80
    if before:
        assert st.contracts["0xa"]["mints"] >= before["mints"]


def test_classification_manifold_tail_and_collections():
    # 0xM: proxy manifold (len 2141); 0xT1/0xT2: artistas avulsos (1 mint);
    # 0xC: colecção (família própria, 50 mints num contrato)
    mints = {1: [("0xM", 1), ("0xT1", 1)],
             2: [("0xT2", 1)] + [("0xC", i) for i in range(50)]}
    codes = {"0xm": b"m" * MANIFOLD_RUNTIME_LEN, "0xt1": b"a" * 300,
             "0xt2": b"b" * 400, "0xc": b"c" * 5000}
    fetch, code = make_world(mints, codes)
    d = EraDiscovery(fetch_mints=fetch, get_code=code, era=(1, 2))
    st = DiscoveryState()
    d.scan(st, 2, random.Random(0))
    reg = ContractRegistry()
    rep = d.classify_into(st, reg)
    assert reg.contracts("manifold2021") == ("0xm",)
    assert set(reg.contracts("tail2021")) == {"0xt1", "0xt2"}
    assert rep.excluded_collections == 1
    assert rep.manifold_total == 1 and rep.tail_total == 2


def test_classification_is_idempotent_on_registry():
    mints = {1: [("0xT1", 1)]}
    codes = {"0xt1": b"a" * 300}
    fetch, code = make_world(mints, codes)
    d = EraDiscovery(fetch_mints=fetch, get_code=code, era=(1, 1))
    st = DiscoveryState()
    d.scan(st, 1, random.Random(0))
    reg = ContractRegistry()
    d.classify_into(st, reg)
    rep2 = d.classify_into(st, reg)
    assert rep2.new_contracts == 0
    assert reg.counts() == {"manifold2021": 0, "tail2021": 1} or \
        reg.counts() == {"tail2021": 1}


def test_state_repr_and_report_never_leak_addresses():
    st = DiscoveryState()
    st.contracts[SECRET] = {"mints": 1, "fam": "ff", "len": 10}
    st.scanned.add(1)
    assert SECRET not in repr(st)
    fetch, code = make_world({}, {})
    d = EraDiscovery(fetch_mints=fetch, get_code=code)
    rep = d.classify_into(st, ContractRegistry())
    assert SECRET not in rep.render()


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


def test_state_store_round_trip_is_ciphered():
    mem = MemStore()
    store = DiscoveryStateStore(cipher=XorCipher(), read=mem.read,
                                write=mem.write)
    st = DiscoveryState()
    st.scanned.add(42)
    st.contracts[SECRET] = {"mints": 3, "fam": "ab", "len": 9}
    store.save(st)
    assert SECRET not in mem.blob
    loaded = store.load()
    assert loaded.scanned == {42}
    assert loaded.contracts[SECRET]["mints"] == 3


def test_empty_store_loads_fresh_state():
    store = DiscoveryStateStore(cipher=XorCipher(),
                                read=lambda: None, write=lambda b: None)
    st = store.load()
    assert st.scanned == set() and st.contracts == {}
