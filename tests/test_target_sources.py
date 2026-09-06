"""Epoch-1 sources — registry secrecy, chain enumeration with the R2
canary, EOA check with the R2 canary, composition; R1 (chain travels with
the entry, no defaults) throughout."""

from __future__ import annotations

import inspect

import pytest

from finding_memeland.target.sources import (
    EPOCH1_CAP_EXEMPT,
    EPOCH1_CLASSIC,
    ChainContractLister,
    ChainEoaCheck,
    ChainRpc,
    ChainUnavailable,
    ContractInvisible,
    ContractRegistry,
    RegistryIntegrityError,
    RegistryStore,
    RegistryStratumLister,
    epoch1_listers,
)

SECRET = "0xdeadbeefdeadbeefdeadbeefdeadbeefdeadbeef"
EOA = "0x" + "11" * 20
VAULT = "0x" + "22" * 20


# --------------------------------------------------------------------------- #
# Registo reservado (entradas = (chain, contract))                             #
# --------------------------------------------------------------------------- #


def test_registry_repr_never_shows_contracts():
    reg = ContractRegistry()
    reg.add("tail2021", [SECRET, "0xAAA"], chain="ethereum")
    assert SECRET not in repr(reg)
    assert SECRET not in str(reg)
    assert repr(reg) == "ContractRegistry(tail2021: 2)"


def test_registry_dedupes_per_chain_and_counts():
    reg = ContractRegistry()
    assert reg.add("tail2021", ["0xAAA", "0xaaa", "0xBBB"], chain="ethereum") == 2
    assert reg.add("tail2021", ["0xbbb"], chain="ethereum") == 0
    assert reg.add("tail2021", ["0xbbb"], chain="base") == 1   # outra cadeia
    assert reg.counts() == {"tail2021": 3}
    assert ("base", "0xbbb") in reg.entries("tail2021")


def test_registry_add_requires_chain_keyword():
    p = inspect.signature(ContractRegistry.add).parameters["chain"]
    assert p.default is inspect.Parameter.empty
    assert p.kind is inspect.Parameter.KEYWORD_ONLY


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


def test_registry_store_round_trip_is_ciphered_and_keeps_chain():
    mem = MemStore()
    store = RegistryStore(cipher=XorCipher(), read=mem.read, write=mem.write)
    reg = ContractRegistry()
    reg.add("manifold2021", [SECRET], chain="ethereum")
    store.save(reg)
    assert SECRET not in mem.blob            # cifrado em repouso
    loaded = store.load()
    assert loaded.entries("manifold2021") == (("ethereum", SECRET),)


def test_registry_store_fails_closed_without_leaking():
    mem = MemStore()
    mem.blob = "garbled"
    store = RegistryStore(cipher=XorCipher(), read=mem.read, write=mem.write)
    with pytest.raises(RegistryIntegrityError) as e:
        store.load()
    assert SECRET not in str(e.value)


def test_registry_store_refuses_chainless_v1_payload():
    import json
    mem = MemStore()
    mem.blob = XorCipher().encrypt(json.dumps(
        {"v": 1, "strata": {"tail2021": [SECRET]}}))
    store = RegistryStore(cipher=XorCipher(), read=mem.read, write=mem.write)
    with pytest.raises(RegistryIntegrityError) as e:
        store.load()
    assert SECRET not in str(e.value)


# --------------------------------------------------------------------------- #
# Enumeração pela chain                                                        #
# --------------------------------------------------------------------------- #


def make_rpc(*, chain="ethereum", supply=None, ids=None, existing=None,
             code=None, owners=None, transport_down=False):
    """ChainRpc fake. `code`: addr -> hex code ('0x' = sem código); por
    omissão TODOS os contratos consultados têm código (canário passa).
    `owners`: token -> owner addr para ownerOf."""
    code = code or {}
    owners = owners or {}

    def eth_call(to, data):
        if transport_down:
            raise ChainUnavailable("rpc em baixo")
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
            if arg in owners:
                return "0x" + owners[arg][2:].rjust(64, "0")
            if existing and arg in existing:
                return "0x" + "11" * 32
            raise RuntimeError("execution reverted")
        raise RuntimeError("selector?")

    def get_code(addr):
        if transport_down:
            raise ChainUnavailable("rpc em baixo")
        return code.get(addr.lower(), "0x6001")

    return ChainRpc(chain=chain, eth_call=eth_call, get_code=get_code)


def test_lister_enumerates_by_index_when_available():
    rpc = make_rpc(supply=3, ids=[7, 9, 42])
    items = list(ChainContractLister(rpc=rpc, platform="foundation",
                                     contract="0xF").items())
    assert [i.token_id for i in items] == [7, 9, 42]
    assert all(i.platform == "foundation" and i.name == "" for i in items)
    assert all(i.chain == "ethereum" for i in items)   # cadeia do rpc, no item


def test_lister_probes_densely_when_no_enumeration():
    # tokens 1..5 e 8 existem; buraco de 2 (burns) tolerado, pára no fim
    rpc = make_rpc(supply=None, existing={1, 2, 3, 4, 5, 8})
    items = list(ChainContractLister(rpc=rpc, platform="tail2021",
                                     contract="0xT",
                                     probe_miss_budget=3).items())
    assert [i.token_id for i in items] == [1, 2, 3, 4, 5, 8]


def test_lister_canary_refuses_contract_without_code():
    """R2: RPC da cadeia errada responde '0x' a tudo — sem canário isto era
    'zero tokens' em silêncio. Com canário: ContractInvisible, alto e bom
    som, sem endereço na mensagem."""
    rpc = make_rpc(supply=0, ids=[], code={"0xf": "0x"})
    lister = ChainContractLister(rpc=rpc, platform="foundation", contract="0xF")
    with pytest.raises(ContractInvisible) as e:
        list(lister.items())
    assert "0xf" not in str(e.value).lower()      # nunca o endereço
    assert "foundation" in str(e.value)            # só o slug


def test_lister_transport_failure_propagates():
    rpc = make_rpc(transport_down=True)
    with pytest.raises(ChainUnavailable):
        list(ChainContractLister(rpc=rpc, platform="p", contract="0xF").items())


def test_registry_stratum_lister_tags_stratum_and_uses_entry_chain():
    reg = ContractRegistry()
    reg.add("tail2021", ["0xa"], chain="ethereum")
    reg.add("tail2021", ["0xb"], chain="base")
    rpcs = {"ethereum": make_rpc(chain="ethereum", existing={1}),
            "base": make_rpc(chain="base", existing={1})}
    items = list(RegistryStratumLister(rpcs=rpcs, stratum="tail2021",
                                       registry=reg).items())
    assert len(items) == 2                    # token 1 de cada contrato
    assert all(i.platform == "tail2021" for i in items)
    assert {i.chain for i in items} == {"ethereum", "base"}   # R1


def test_registry_entry_on_unconfigured_chain_fails_loud():
    reg = ContractRegistry()
    reg.add("tail2021", ["0xa"], chain="polygon")
    rpcs = {"ethereum": make_rpc(existing={1})}
    with pytest.raises(KeyError):
        list(RegistryStratumLister(rpcs=rpcs, stratum="tail2021",
                                   registry=reg).items())


def test_rpc_registered_under_wrong_chain_is_refused():
    rpcs = {"base": make_rpc(chain="ethereum", existing={1})}   # desacordo
    reg = ContractRegistry()
    reg.add("tail2021", ["0xa"], chain="base")
    with pytest.raises(ValueError):
        list(RegistryStratumLister(rpcs=rpcs, stratum="tail2021",
                                   registry=reg).items())


def test_epoch1_composition_matches_ratified_decision():
    reg = ContractRegistry()
    listers = epoch1_listers(rpcs={"ethereum": make_rpc(supply=0, ids=[])},
                             registry=reg)
    names = [l.name for l in listers]
    assert names[:4] == [slug for slug, _, _ in EPOCH1_CLASSIC]
    assert [l.chain for l in listers[:4]] == [c for _, c, _ in EPOCH1_CLASSIC]
    assert names[4:] == ["manifold2021", "tail2021"]
    assert EPOCH1_CAP_EXEMPT == frozenset({"tail2021"})


def test_epoch1_missing_chain_rpc_is_a_configuration_error():
    with pytest.raises(KeyError):
        epoch1_listers(rpcs={}, registry=ContractRegistry())


def test_no_chain_defaults_anywhere():
    """R1: keyword obrigatória, sem valor por omissão."""
    for cls in (ChainContractLister, RegistryStratumLister):
        assert "chain" not in inspect.signature(cls.__init__).parameters
    assert inspect.signature(ChainRpc).parameters["chain"].default \
        is inspect.Parameter.empty


# --------------------------------------------------------------------------- #
# EOA com canário                                                              #
# --------------------------------------------------------------------------- #


def test_eoa_check_true_for_eoa_owner_false_for_contract_owner():
    rpc = make_rpc(owners={1: EOA, 2: VAULT},
                   code={EOA: "0x", VAULT: "0x6001"})
    check = ChainEoaCheck(rpcs={"ethereum": rpc})
    assert check("ethereum", "0xF", 1) is True
    assert check("ethereum", "0xF", 2) is False


def test_eoa_check_canary_refuses_when_contract_invisible():
    """R2: '0x' no dono só vale se o RPC vê o contrato — se não vê, o '0x'
    não significa nada: None (inverificável), nunca True."""
    rpc = make_rpc(owners={1: EOA}, code={"0xf": "0x", EOA: "0x"})
    check = ChainEoaCheck(rpcs={"ethereum": rpc})
    assert check("ethereum", "0xF", 1) is None


def test_eoa_check_none_on_missing_chain_revert_or_transport():
    rpc = make_rpc(owners={1: EOA}, code={EOA: "0x"})
    check = ChainEoaCheck(rpcs={"ethereum": rpc})
    assert check("base", "0xF", 1) is None            # cadeia sem rpc
    assert check("ethereum", "0xF", 9) is None        # ownerOf reverte
    down = ChainEoaCheck(rpcs={"ethereum": make_rpc(transport_down=True)})
    assert down("ethereum", "0xF", 1) is None
