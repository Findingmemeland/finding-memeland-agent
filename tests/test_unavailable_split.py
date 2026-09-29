"""O "indisponível-NOSSO" dividido por onde falhou — e o que não é nosso, fora.

29/09. A colheita perdeu 116 leituras numa corrida (76 em Ethereum, 40 em
Base) sob um só número. Ao olhar para o código, o número misturava duas
coisas de natureza oposta:

  NOSSO   rpc            o nó não respondeu, ou respondeu erro que não é revert
          gateway        o gateway IPFS rebentou (timeout, HTTP) na metadata
          gateway-imagem todos os gateways rebentaram no teste da imagem
  ?       gateway-não-json  respondeu, mas não JSON: página de throttle (nosso)
                            ou um CID que nem é metadata (deles) — fica à parte
  DELES   metadata-inválida  não é um objecto, é grande demais, `data:` torto,
                             esquema desconhecido
          tokenURI-ilegível  o contrato devolveu algo que não é uma string ABI

Os dois últimos saem do "nosso" e passam a "metadata-inválida". SÓ MEDIÇÃO:
todas as classes novas continuam a ser ChainUnavailable, por isso nenhum
`except` existente muda de comportamento — o candidato é descartado (ou
mantido, no /prepare estrito) exactamente como antes.
"""
from __future__ import annotations

import random

import pytest

from finding_memeland.target.adapters import Erc721Metadata, RpcError
from finding_memeland.target.harvest import MintHarvester, TRANSFER_TOPIC
from finding_memeland.target.prepare import Larder, Source, TargetFinder, Tally
from finding_memeland.target.refresh import TokenRead
from finding_memeland.target.sources import (
    ChainRpc,
    ChainUnavailable,
    GatewayNotJson,
    GatewayUnavailable,
    ImageGatewaysDown,
    MetadataInvalid,
    RpcUnavailable,
    TokenUriUndecodable,
)

A = "0x" + "aa" * 20
B = "0x" + "bb" * 20
ZERO = "0x" + "0" * 64
IPFS_URI = "ipfs://bafkre" + "a" * 50
IPFS_IMG = "ipfs://bafyimg" + "b" * 50
PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 32


def _abi_string(s: str) -> str:
    b = s.encode()
    return ("0x" + (32).to_bytes(32, "big").hex() + len(b).to_bytes(32, "big").hex()
            + b.hex() + "00" * (-len(b) % 32))


def _meta(*, eth_call=None, http=None) -> Erc721Metadata:
    rpc = ChainRpc(chain="ethereum",
                   eth_call=eth_call or (lambda to, data: _abi_string(IPFS_URI)),
                   get_code=lambda addr: "0x6080")
    return Erc721Metadata(rpcs={"ethereum": rpc}, gateway="https://gw.example/ipfs/",
                          http_get=http or (lambda url, h: '{"name": "Two Words"}'))


def _raises(exc):
    def f(*a, **k):
        raise exc
    return f


# --------------------------------------------------------------------------- #
# 1. O leitor diz onde falhou                                                   #
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("meta, cls", [
    (lambda: _meta(eth_call=_raises(ChainUnavailable("timeout"))), RpcUnavailable),
    (lambda: _meta(eth_call=_raises(RpcError(-32603, "Internal error", revert=False))),
     RpcUnavailable),
    (lambda: _meta(eth_call=_raises(ValueError("adapter blew up"))), RpcUnavailable),
    (lambda: _meta(eth_call=lambda to, d: "0x1234"), TokenUriUndecodable),
    (lambda: _meta(http=_raises(TimeoutError())), GatewayUnavailable),
    (lambda: _meta(http=lambda u, h: "<html>slow down</html>"), GatewayNotJson),
    (lambda: _meta(http=lambda u, h: "[1, 2, 3]"), MetadataInvalid),
])
def test_the_reader_says_where_it_failed(meta, cls):
    with pytest.raises(cls):
        meta().read("ethereum", A, 1)


def test_every_new_kind_is_still_a_chain_unavailable():
    """O CONTRATO que torna isto "só medição": quem apanhava ChainUnavailable
    continua a apanhar tudo, e nada muda de comportamento."""
    for cls in (RpcUnavailable, GatewayUnavailable, GatewayNotJson,
                ImageGatewaysDown, MetadataInvalid, TokenUriUndecodable):
        assert issubclass(cls, ChainUnavailable), cls


def test_only_the_nft_s_own_defects_are_theirs():
    assert MetadataInvalid.theirs and TokenUriUndecodable.theirs
    for cls in (ChainUnavailable, RpcUnavailable, GatewayUnavailable,
                GatewayNotJson, ImageGatewaysDown):
        assert not cls.theirs, cls


def test_a_revert_is_still_a_burned_token_not_a_failure():
    m = _meta(eth_call=_raises(RpcError(3, "execution reverted", revert=True)))
    assert m.read("ethereum", A, 1) is None


# --------------------------------------------------------------------------- #
# 2. A colheita conta por onde                                                  #
# --------------------------------------------------------------------------- #


def _topic(n: int) -> str:
    return "0x" + f"{n:064x}"


def _mint(contract, tid):
    return {"address": contract,
            "topics": [TRANSFER_TOPIC, ZERO, _topic(99), _topic(tid)]}


def _render(read_result) -> str:
    """Um bloco com um só mint (B:3) cuja leitura é `read_result`."""
    def read_meta(contract, tid):
        if contract == B:
            if isinstance(read_result, Exception):
                raise read_result
            return read_result
        return TokenRead(token_uri=IPFS_URI,
                         metadata={"name": "Grease Pencil Gospel", "image": IPFS_IMG})

    logs = {100: [_mint(A, 1)], 7: [_mint(B, 3)]}
    h = MintHarvester(chain="ethereum", latest_block=lambda: 1_000_000,
                      get_logs=lambda a, b: logs.get(a, []), read_meta=read_meta,
                      canary_block=100, canary_mints=1, rng=random.Random(0))

    class _R:
        def randrange(self, lo, hi):
            return 7
    h._rng = _R()
    _refs, rep = h.harvest(1)
    return rep.render()


def test_harvest_splits_ours_by_where():
    out = _render(GatewayUnavailable("gateway: TimeoutError"))
    assert "indisponível-NOSSO 1 (gateway 1)" in out, out


def test_harvest_keeps_the_nft_s_defects_out_of_ours():
    out = _render(MetadataInvalid("metadata is not an object"))
    assert "indisponível-NOSSO" not in out, out
    assert "defeito-DELES 1 (metadata-inválida 1)" in out, out


def test_an_unknown_exception_is_still_ours_as_before():
    """Um adaptador que rebente sem dizer onde conta como nosso — como até
    hoje. Assumir que é deles apagaria uma falha nossa do relatório."""
    out = _render(RuntimeError("?"))
    assert "indisponível-NOSSO 1 (outro 1)" in out, out


# --------------------------------------------------------------------------- #
# 3. O depósito também                                                          #
# --------------------------------------------------------------------------- #


def _deposit(*, read=None, probe=None) -> str:
    def read_token(c, k, t):
        if isinstance(read, Exception):
            raise read
        return TokenRead(token_uri=IPFS_URI,
                         metadata={"name": "Some Two Words", "image": IPFS_IMG})

    def probe_image(u):
        if isinstance(probe, Exception):
            raise probe
        return (PNG, len(PNG))

    f = TargetFinder(
        sources=[Source("x", "base", "0x" + "cd" * 20)],
        total_supply=lambda c, k: 10, token_by_index=lambda c, k, i: i + 1,
        read_token=read_token, probe_image=probe_image,
        owner_is_eoa=lambda *a: True, name_is_unique=lambda *a: True,
        rng=random.Random(0))
    return f.deposit(Larder(), ["base:0x" + "ab" * 20 + ":1"],
                     chain_ok=lambda c: True).render()


def test_deposit_splits_a_read_failure():
    assert "indisponível-NOSSO 1 (rpc 1)" in _deposit(read=RpcUnavailable("x"))


def test_deposit_names_the_image_gateways():
    out = _deposit(probe=ImageGatewaysDown("no gateway answered (4 tried)"))
    assert "indisponível-NOSSO 1 (gateway-imagem 1)" in out, out


def test_deposit_keeps_broken_metadata_out_of_ours():
    out = _deposit(read=TokenUriUndecodable("tokenURI undecodable"))
    assert "indisponível-NOSSO" not in out, out
    assert "defeito-DELES 1 (tokenURI-ilegível 1)" in out, out


def test_prepare_steps_are_named_so_the_split_adds_up():
    """No /prepare há três falhas nossas que não passam pelo leitor — a
    leitura estrita, a visão e as guardas. Têm nome, para a soma bater."""
    t = Tally()
    t.note_ours("visão")
    t.note_ours("guarda")
    t.count_unavailable(GatewayUnavailable("x"))
    assert t.unavailable == 3
    assert "indisponível-NOSSO 3 (gateway 1, guarda 1, visão 1)" in t.render()


def test_the_split_never_names_the_candidate():
    out = _deposit(read=RpcUnavailable("x")) + _render(GatewayUnavailable("x"))
    for leak in ("Some Two Words", "Grease Pencil", "abab", "cdcd", "bbbb"):
        assert leak not in out, leak
