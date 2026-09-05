"""Epoch-1 sources — registry secrecy, chain enumeration, composition."""

from __future__ import annotations

import pytest

from finding_memeland.target.sources import (
    EPOCH1_CAP_EXEMPT,
    EPOCH1_CLASSIC,
    ChainContractLister,
    ContractRegistry,
    RegistryIntegrityError,
    RegistryStore,
    RegistryStratumLister,
    epoch1_listers,
)

SECRET = "0xdeadbeefdeadbeefdeadbeefdeadbeefdeadbeef"


# --------------------------------------------------------------------------- #
# Registo reservado                                                            #
# --------------------------------------------------------------------------- #


def test_registry_repr_never_shows_contracts():
    reg = ContractRegistry()
    reg.add("tail2021", [SECRET, "0xAAA"])
    assert SECRET not in repr(reg)
    assert SECRET not in str(reg)
    assert repr(reg) == "ContractRegistry(tail2021: 2)"


def test_registry_dedupes_and_counts():
    reg = ContractRegistry()
    assert reg.add("tail2021", ["0xAAA", "0xaaa", "0xBBB"]) == 2
    assert reg.add("tail2021", ["0xbbb"]) == 0
    assert reg.counts() == {"tail2021": 2}


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


def test_registry_store_round_trip_is_ciphered():
    mem = MemStore()
    store = RegistryStore(cipher=XorCipher(), read=mem.read, write=mem.write)
    reg = ContractRegistry()
    reg.add("manifold2021", [SECRET])
    store.save(reg)
    assert SECRET not in mem.blob            # cifrado em repouso
    loaded = store.load()
    assert loaded.contracts("manifold2021") == (SECRET,)


def test_registry_store_fails_closed_without_leaking():
    mem = MemStore()
    mem.blob = "garbled"
    store = RegistryStore(cipher=XorCipher(), read=mem.read, write=mem.write)
    with pytest.raises(RegistryIntegrityError) as e:
        store.load()
    assert SECRET not in str(e.value)


# --------------------------------------------------------------------------- #
# Enumeração pela chain                                                        #
# --------------------------------------------------------------------------- #


def make_call(*, supply=None, ids=None, existing=None):
    """eth_call fake: totalSupply/tokenByIndex/ownerOf conforme configurado."""
    def call(to, data):
        sel = data[:10]
        arg = int(data[10:], 16) if len(data) > 10 else None
        if sel == "0x18160ddd":
            if supply is None:
                raise RuntimeError("execution reverted")
            return hex(supply)
        if sel == "0x4f6ccce7":
            if ids is None:
                raise RuntimeError("execution reverted")
            return hex(ids[arg])
        if sel == "0x6352211e":
            if existing and arg in existing:
                return "0x" + "11" * 32
            raise RuntimeError("execution reverted")
        raise RuntimeError("selector?")
    return call


def test_lister_enumerates_by_index_when_available():
    call = make_call(supply=3, ids=[7, 9, 42])
    items = list(ChainContractLister(eth_call=call, platform="foundation",
                                     contract="0xF", chain="ethereum").items())
    assert [i.token_id for i in items] == [7, 9, 42]
    assert all(i.platform == "foundation" and i.name == "" for i in items)
    assert all(i.chain == "ethereum" for i in items)   # cadeia viaja no item


def test_lister_probes_densely_when_no_enumeration():
    # tokens 1..5 e 8 existem; buraco de 2 (burns) tolerado, pára no fim
    call = make_call(supply=None, existing={1, 2, 3, 4, 5, 8})
    items = list(ChainContractLister(eth_call=call, platform="tail2021",
                                     contract="0xT", chain="ethereum",
                                     probe_miss_budget=3).items())
    assert [i.token_id for i in items] == [1, 2, 3, 4, 5, 8]


def test_registry_stratum_lister_tags_stratum_not_contract():
    reg = ContractRegistry()
    reg.add("tail2021", ["0xa", "0xb"])
    call = make_call(supply=None, existing={1})
    items = list(RegistryStratumLister(eth_call=call, stratum="tail2021",
                                       registry=reg, chain="ethereum").items())
    assert len(items) == 2                    # token 1 de cada contrato
    assert all(i.platform == "tail2021" for i in items)


def test_chain_is_a_required_keyword_on_every_lister():
    """Uma cadeia com valor por omissão é a forma exacta do bug P0-1
    (revisão Opus 05/09) — os listers recusam construir sem ela."""
    import inspect
    for cls in (ChainContractLister, RegistryStratumLister):
        p = inspect.signature(cls.__init__).parameters["chain"]
        assert p.default is inspect.Parameter.empty
        assert p.kind is inspect.Parameter.KEYWORD_ONLY


def test_epoch1_composition_matches_ratified_decision():
    reg = ContractRegistry()
    listers = epoch1_listers(eth_call=make_call(supply=0, ids=[]),
                             registry=reg)
    names = [l.name for l in listers]
    assert names[:4] == [slug for slug, _, _ in EPOCH1_CLASSIC]
    assert names[4:] == ["manifold2021", "tail2021"]
    assert EPOCH1_CAP_EXEMPT == frozenset({"tail2021"})
