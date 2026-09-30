"""Duas medições, nenhuma decisão (30/09, Pedro): "quero perceber o que
falta para entrar o primeiro alvo de Base".

1. Donos-contrato — 8 candidatos caíram a 30/09 porque o dono é um contrato.
   Pergunta-se-lhe ERC-1271 (isValidSignature): responde como carteira, ou
   reverte como um cofre? O veredicto continua False em todos os casos.
2. Índice cego — 19 de 35 candidatos caíram no "único" porque a pesquisa do
   OpenSea não devolve a própria peça. Pergunta-se ao OpenSea pela peça:
   não a conhece, ou conhece-a (marcada ou não) e a pesquisa esconde-a? A
   resposta da guarda continua None.
Só contagens: nunca um endereço, um contrato ou um nome.
"""
from __future__ import annotations

import json
import random

import pytest
from test_opensea_surface import HttpErr, _text, fixture
from test_target_prepare import World
from test_unavailable_split import IPFS_IMG, IPFS_URI

from finding_memeland.target.adapters import JsonRpc, OpenSeaChainProbe, RpcError
from finding_memeland.target.prepare import Larder, Source, TargetFinder
from finding_memeland.target.refresh import TokenRead
from finding_memeland.target.search_guard import MarketNameUniqueness
from finding_memeland.target.sources import (
    SEL_IS_VALID_SIGNATURE,
    ChainEoaCheck,
    ChainRpc,
    ChainUnavailable,
    _erc1271_probe_calldata,
)

NFT = "0x" + "ab" * 20
OWNER = "0x" + "cd" * 20
WORD_OWNER = "0x" + "0" * 24 + OWNER[2:]
SAFE_CODE = "0x6080604052"


def _rpc(on_1271, *, owner_code=SAFE_CODE, calls=None):
    def eth_call(to, data):
        if calls is not None:
            calls.append(data[:10])
        if data.startswith("0x6352211e"):                   # ownerOf
            return WORD_OWNER
        if data.startswith(SEL_IS_VALID_SIGNATURE):
            if isinstance(on_1271, BaseException):
                raise on_1271
            return on_1271
        raise AssertionError(f"unexpected call {data[:10]}")

    def get_code(addr):
        return owner_code if addr.lower() == OWNER else SAFE_CODE
    return ChainRpc(chain="base", eth_call=eth_call, get_code=get_code)


# --------------------------------------------------------------------------- #
# 1. Donos-contrato: ERC-1271                                                   #
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("answer, kind", [
    ("0x1626ba7e" + "00" * 28, "1271"),                     # the magic value
    ("0xffffffff" + "00" * 28, "1271"),                     # a wallet saying "no"
    (RpcError(3, "execution reverted: GS026", revert=True), "reason"),
    (RpcError(3, "execution reverted", revert=True, data="0x8baa579f"), "reason"),
    (RpcError(3, "execution reverted", revert=True, data="0x"), "mute"),
    (RpcError(3, "execution reverted", revert=True), "mute"),
    ("0x", "empty"),
    ("0x1234", "other"),
    (ChainUnavailable("timeout"), "noanswer"),
    (RpcError(-32603, "Internal error", revert=False), "noanswer"),
])
def test_a_contract_owner_is_counted_by_how_it_answers_erc1271(answer, kind):
    check = ChainEoaCheck(rpcs={"base": _rpc(answer)})
    assert check("base", NFT, 7) is False                   # the verdict is unchanged
    assert check.stats["contract"] == 1
    assert check.stats[f"contract_{kind}"] == 1
    assert sum(v for k, v in check.stats.items() if k.startswith("contract_")) == 1


def test_a_person_s_key_is_never_asked():
    calls = []
    check = ChainEoaCheck(rpcs={"base": _rpc("0x", owner_code="0x", calls=calls)})
    assert check("base", NFT, 7) is True
    assert SEL_IS_VALID_SIGNATURE not in calls
    assert all(v == 0 for k, v in check.stats.items() if k.startswith("contract_"))


def test_the_question_is_well_formed_abi():
    """isValidSignature(bytes32 0, bytes sig), sig = abi.encode((0, 65 zeros))."""
    data = _erc1271_probe_calldata()
    assert data.startswith(SEL_IS_VALID_SIGNATURE)
    words = [int(data[10 + i:74 + i], 16) for i in range(0, len(data) - 10, 64)]
    assert words[0] == 0 and words[1] == 0x40               # hash, offset of `bytes`
    assert words[2] == 224                                  # signature length
    assert words[3:7] == [0x20, 0, 0x40, 65]                # tuple, uint, offset, len


def test_the_node_s_revert_data_reaches_the_error():
    def post(url, body, headers):
        return json.dumps({"jsonrpc": "2.0", "id": 1, "error": {
            "code": 3, "message": "execution reverted", "data": "0x8baa579f"}})
    with pytest.raises(RpcError) as e:
        JsonRpc(url="https://node.example", http_post=post).eth_call(OWNER, "0x00")
    assert e.value.revert and e.value.data == "0x8baa579f"


def test_the_deposit_names_how_the_owner_answered():
    check = ChainEoaCheck(rpcs={"base": _rpc("0xffffffff" + "00" * 28)})
    out = _deposit(owner_is_eoa=check)
    assert "dono 1 (contrato:responde-1271 1)" in out, out
    assert OWNER[2:] not in out and NFT[2:] not in out


# --------------------------------------------------------------------------- #
# 2. Índice cego: a peça existe no OpenSea?                                     #
# --------------------------------------------------------------------------- #


class _Search:
    """A non-full page that does NOT contain the target: blind."""

    def named_items(self, base):
        return [("base:0x" + "ee" * 20 + ":1", "Something Else")]


@pytest.mark.parametrize("status, key", [
    ("missing", "blind_unindexed"), ("flagged", "blind_flagged"),
    ("clean", "blind_clean"), (None, "blind_unknown"),
])
def test_the_blind_canary_is_split_by_asking_for_the_item(status, key):
    guard = MarketNameUniqueness(search=_Search(), page_size=50,
                                 item_status=lambda c, k, t: status)
    assert guard("Two Words", "base", NFT, 7) is None       # the answer is unchanged
    assert guard.stats["blind"] == 1 and guard.stats[key] == 1


def test_a_lookup_that_blows_up_is_unknown_not_a_crash():
    def boom(*a):
        raise RuntimeError("x")
    guard = MarketNameUniqueness(search=_Search(), page_size=50, item_status=boom)
    assert guard("Two Words", "base", NFT, 7) is None
    assert guard.stats["blind_unknown"] == 1


def test_without_the_lookup_blind_is_as_before():
    guard = MarketNameUniqueness(search=_Search(), page_size=50)
    assert guard("Two Words", "base", NFT, 7) is None
    assert guard.stats["blind"] == 1
    assert all(guard.stats[k] == 0 for k in ("blind_unindexed", "blind_flagged",
                                             "blind_clean", "blind_unknown"))


def _probe_answering(doc_or_exc):
    def get(url, headers):
        if isinstance(doc_or_exc, BaseException):
            raise doc_or_exc
        return doc_or_exc
    return OpenSeaChainProbe(http_get=get, api_key="k")


def test_opensea_item_status_on_the_measured_fixture():
    item = fixture("opensea_item_ethereum")
    nft = json.loads(_text(item))["nft"]
    probe = _probe_answering(_text(item))
    assert probe.item_status("ethereum", nft["contract"], int(nft["identifier"])) == "clean"
    flagged = dict(json.loads(_text(item)))
    flagged["nft"] = {**nft, "is_suspicious": True}
    probe = _probe_answering(json.dumps(flagged))
    assert probe.item_status("ethereum", nft["contract"], int(nft["identifier"])) == "flagged"
    # an answer about ANOTHER item is no answer
    assert probe.item_status("ethereum", "0x" + "11" * 20, 1) is None


def test_opensea_item_status_miss_and_failure():
    assert _probe_answering(HttpErr(404, "not found")).item_status("base", NFT, 7) == "missing"
    assert _probe_answering(HttpErr(500, "boom")).item_status("base", NFT, 7) is None
    assert _probe_answering("<html>").item_status("base", NFT, 7) is None
    assert _probe_answering("{}").item_status("polygon-zkevm", NFT, 7) is None


def test_the_deposit_names_the_blind_split():
    guard = MarketNameUniqueness(search=_Search(), page_size=50,
                                 item_status=lambda c, k, t: "flagged")
    out = _deposit(name_is_unique=guard)
    assert "único 1 (índice-cego:indexada-marcada 1)" in out, out


# --------------------------------------------------------------------------- #


def _deposit(**over) -> str:
    world = World()
    kw = dict(sources=[Source("x", "base", NFT)],
              total_supply=lambda c, k: 10, token_by_index=lambda c, k, i: i + 1,
              read_token=lambda c, k, t: TokenRead(
                  token_uri=IPFS_URI, metadata={"name": "Two Words", "image": IPFS_IMG}),
              probe_image=world.probe_image, owner_is_eoa=lambda *a: True,
              name_is_unique=lambda *a: True, rng=random.Random(0))
    kw.update(over)
    f = TargetFinder(**kw)
    return f.deposit(Larder(), [f"base:{NFT}:7"], chain_ok=lambda c: True).render()


def test_production_wires_the_item_lookup_on_opensea_and_nothing_on_rarible():
    from test_target_wiring import FakeRepo, rpc_ok, settings

    from finding_memeland.target.wiring import build_target

    def build(s):
        return build_target(s, anthropic=object(), repo=FakeRepo(),
                            http_get=lambda u, h: "{}", http_post=rpc_ok,
                            http_get_bytes=lambda u, h: b"")
    on_opensea = build(settings(opensea_api_key="ok"))
    assert on_opensea.market_surface == "opensea"
    assert on_opensea.finder._name_is_unique._item_status is not None   # noqa: SLF001
    on_rarible = build(settings())
    assert on_rarible.finder._name_is_unique._item_status is None       # noqa: SLF001
