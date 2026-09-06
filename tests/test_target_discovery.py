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
CANARY = 999_999          # bloco fixado fora das eras dos testes


def make_world(mints_by_block, codes):
    def fetch(b):
        if b == CANARY:                      # canário: bloco com mints conhecidos
            return [("0xCanary", 1)]
        return mints_by_block.get(b, [])

    def code(c):
        return codes[c]
    return fetch, code


def test_scan_accumulates_and_skips_scanned():
    fetch, code = make_world(
        {100: [("0xA", 1)], 200: [("0xA", 2), ("0xB", 1)]},
        {"0xa": b"x" * 100, "0xb": b"y" * 200})
    d = EraDiscovery(chain="ethereum", canary_block=CANARY, canary_mints=1, fetch_mints=fetch, get_code=code, era=(100, 200))
    st = DiscoveryState()
    rng = random.Random(1)
    out = d.scan(st, 40, rng)           # era tem ~101 blocos; 40 novos
    assert out.scanned == 40 and out.failed == 0
    assert len(st.scanned) == 40
    before = dict(st.contracts["0xa"]) if "0xa" in st.contracts else None
    d.scan(st, 40, rng)                 # não re-varre os mesmos
    assert len(st.scanned) == 80
    if before:
        assert st.contracts["0xa"]["mints"] >= before["mints"]


def test_get_code_failure_drops_whole_block_and_retries_later():
    """P0-3 (revisão Opus 05/09): um blip de rede no get_code não pode
    marcar o bloco como varrido e apagar o contrato para sempre — o bloco
    cai inteiro (sem escrita parcial) e o run seguinte retenta-o."""
    rpc = {"up": False}

    def fetch(b):
        return [("0xA", 1), ("0xB", 1)]      # o canário também devolve mints

    def flaky_code(c):
        if c == "0xb" and not rpc["up"]:
            raise RuntimeError("rpc blip")
        return b"x" * 100 if c == "0xa" else b"y" * 200

    d = EraDiscovery(chain="ethereum", canary_block=CANARY, canary_mints=2,
                     fetch_mints=fetch, get_code=flaky_code, era=(1, 1))
    st = DiscoveryState()
    out1 = d.scan(st, 1, random.Random(0))
    assert out1.scanned == 0 and out1.failed >= 1
    assert st.scanned == set()          # bloco NÃO marcado
    assert st.contracts == {}           # sem escrita parcial (nem 0xA)
    rpc["up"] = True                    # RPC volta; run seguinte retenta
    out2 = d.scan(st, 1, random.Random(0))
    assert out2.scanned == 1
    assert set(st.contracts) == {"0xa", "0xb"}
    assert st.contracts["0xa"]["mints"] == 1    # sem dupla contagem


def test_classification_manifold_tail_and_collections():
    # 0xM: proxy manifold (len 2141); 0xT1/0xT2: artistas avulsos (1 mint);
    # 0xC: colecção (família própria, 50 mints num contrato)
    mints = {1: [("0xM", 1), ("0xT1", 1)],
             2: [("0xT2", 1)] + [("0xC", i) for i in range(50)]}
    codes = {"0xm": b"m" * MANIFOLD_RUNTIME_LEN, "0xt1": b"a" * 300,
             "0xt2": b"b" * 400, "0xc": b"c" * 5000}
    fetch, code = make_world(mints, codes)
    d = EraDiscovery(chain="ethereum", canary_block=CANARY, canary_mints=1, fetch_mints=fetch, get_code=code, era=(1, 2))
    st = DiscoveryState()
    d.scan(st, 2, random.Random(0))
    reg = ContractRegistry()
    rep = d.classify_into(st, reg)
    assert reg.entries("manifold2021") == (("ethereum", "0xm"),)
    assert {c for _, c in reg.entries("tail2021")} == {"0xt1", "0xt2"}
    assert all(ch == "ethereum" for ch, _ in reg.entries("tail2021"))
    assert rep.excluded_collections == 1
    assert rep.manifold_total == 1 and rep.tail_total == 2


def test_classification_is_idempotent_on_registry():
    mints = {1: [("0xT1", 1)]}
    codes = {"0xt1": b"a" * 300}
    fetch, code = make_world(mints, codes)
    d = EraDiscovery(chain="ethereum", canary_block=CANARY, canary_mints=1, fetch_mints=fetch, get_code=code, era=(1, 1))
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
    st.contracts[SECRET] = {"mints": 1, "fam": "ff", "len": 10, "chain": "ethereum"}
    st.scanned.add(1)
    assert SECRET not in repr(st)
    fetch, code = make_world({}, {})
    d = EraDiscovery(chain="ethereum", canary_block=CANARY, canary_mints=1, fetch_mints=fetch, get_code=code)
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
    st.contracts[SECRET] = {"mints": 3, "fam": "ab", "len": 9, "chain": "ethereum"}
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


def test_garbled_state_store_fails_closed_without_leaking():
    """P2 (revisão Opus 05/09): o store mais sensível também tem guarda —
    estado ilegível recusa, nunca vira 'vazio' em silêncio nem despeja
    conteúdo no traceback."""
    import pytest
    from finding_memeland.target.discovery import DiscoveryIntegrityError
    store = DiscoveryStateStore(cipher=XorCipher(),
                                read=lambda: "garbled", write=lambda b: None)
    with pytest.raises(DiscoveryIntegrityError) as e:
        store.load()
    assert SECRET not in str(e.value)


def test_canary_failure_refuses_to_scan_and_touches_nothing():
    """R2 (Opus 06/09): fetch_mints a devolver [] em todos os blocos (nó
    não-arquivo, filtro errado, cadeia errada) marcava blocos como varridos
    com zero contratos — degradado, não vazio, invisível ao gate. O bloco
    fixado tem de devolver mints, senão o run recusa-se a varrer."""
    d = EraDiscovery(chain="ethereum", canary_block=CANARY, canary_mints=1,
                     fetch_mints=lambda b: [], get_code=lambda c: b"x",
                     era=(1, 100))
    st = DiscoveryState()
    out = d.scan(st, 10, random.Random(0))
    assert out.canary_ok is False and out.scanned == 0
    assert st.scanned == set() and st.contracts == {}


def test_canary_transport_failure_also_refuses():
    def fetch(b):
        raise RuntimeError("rpc em baixo")
    d = EraDiscovery(chain="ethereum", canary_block=CANARY, canary_mints=1, fetch_mints=fetch,
                     get_code=lambda c: b"x", era=(1, 100))
    out = d.scan(DiscoveryState(), 5, random.Random(0))
    assert out.canary_ok is False and out.scanned == 0


def test_zero_mint_blocks_are_counted_as_second_signal():
    fetch, code = make_world({1: [("0xA", 1)]}, {"0xa": b"x" * 10})
    d = EraDiscovery(chain="ethereum", canary_block=CANARY, canary_mints=1, fetch_mints=fetch,
                     get_code=code, era=(1, 4))
    out = d.scan(DiscoveryState(), 4, random.Random(0))
    assert out.canary_ok and out.scanned == 4
    assert out.zero_mint_blocks == 3


def test_canary_block_is_required():
    import pytest
    with pytest.raises(TypeError):
        EraDiscovery(chain="ethereum", fetch_mints=lambda b: [], get_code=lambda c: b"")
    with pytest.raises(ValueError):
        EraDiscovery(chain="ethereum", canary_block=0, canary_mints=1,
                     fetch_mints=lambda b: [], get_code=lambda c: b"")
    with pytest.raises(ValueError):
        EraDiscovery(chain="ethereum", canary_block=CANARY, canary_mints=0,
                     fetch_mints=lambda b: [], get_code=lambda c: b"")


def test_canary_requires_exact_mint_count_catches_truncation():
    """Opus 06/09: um provedor que trunca logs em silêncio passa um canário
    '>= 1' com folga — o bloco tem mints, só que menos. Igualdade com a
    contagem MEDIDA apanha truncagem, filtro parcial e mudança de shape."""
    full = [("0xA", i) for i in range(5)]

    def truncating(b):
        return full[:3] if b == CANARY else full       # página parcial
    d = EraDiscovery(chain="ethereum", canary_block=CANARY, canary_mints=5,
                     fetch_mints=truncating, get_code=lambda c: b"x",
                     era=(1, 10))
    out = d.scan(DiscoveryState(), 3, random.Random(0))
    assert out.canary_ok is False and out.scanned == 0

    exact = EraDiscovery(chain="ethereum", canary_block=CANARY, canary_mints=5,
                         fetch_mints=lambda b: full, get_code=lambda c: b"x",
                         era=(1, 10))
    assert exact.scan(DiscoveryState(), 3, random.Random(0)).canary_ok
