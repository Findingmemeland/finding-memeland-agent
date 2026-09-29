"""A metadata da despensa lida com rotação de gateways (29/09).

O /harvest de 29/09 perdeu 116 leituras de metadata no gateway (93 em Base),
e 6 candidatos de Base que a colheita tinha acabado de ler falharam a
segunda leitura, no depósito, pelo mesmo gateway. A imagem já rodava todos
os gateways desde 17/09; a metadata passou a fazer o mesmo — no /harvest, no
/fill, no depósito e na releitura do /prepare.

Regra do Pedro (29/09) para quando nenhum gateway serve:
  · um gateway disse CLARAMENTE que não tem (404/410) → pin morto, DELES;
    é a única falha de metadata que deixa o /prepare descartar um alvo
  · timeouts e 5xx em todos → NOSSO; o alvo fica na despensa
"""
from __future__ import annotations

import json
import urllib.error

import pytest
from test_harvest_command import _Harv, _Store
from test_target_prepare import World, _finder, _preparer
from test_target_wiring import FakeRepo, settings
from test_unavailable_split import _deposit, _render

from finding_memeland.target.adapters import Erc721Metadata, FailoverMetadata
from finding_memeland.target.prepare import (
    Larder,
    PrepareRefused,
    ReadUnavailable,
    Source,
    Tally,
)
from finding_memeland.target.sources import (
    ChainRpc,
    ChainUnavailable,
    GatewayNotJson,
    GatewayUnavailable,
    MetadataInvalid,
    MetadataPinGone,
)
from finding_memeland.target.wiring import TargetWiring, build_target

A = "0x" + "aa" * 20
CID = "bafkre" + "a" * 50
OURS, GW0, GW1 = "https://ours/ipfs/", "https://gw0/ipfs/", "https://gw1/ipfs/"
DOC = '{"name": "Two Words", "image": "ipfs://bafyimg"}'


def _abi_string(s: str) -> str:
    b = s.encode()
    return ("0x" + (32).to_bytes(32, "big").hex() + len(b).to_bytes(32, "big").hex()
            + b.hex() + "00" * (-len(b) % 32))


def _http_error(url: str, code: int) -> urllib.error.HTTPError:
    return urllib.error.HTTPError(url, code, "x", {}, None)


class Gateways:
    """Cada host responde como lhe mandarem: texto, ou uma excepção.
    Regista quem pediu o quê — e por que transporte (primário ou reserva)."""

    def __init__(self, **by_host):
        self.by_host = by_host
        self.calls: list[tuple[str, str]] = []

    def _answer(self, url, via):
        host = url.split("/")[2]
        self.calls.append((host, via))
        got = self.by_host.get(host, TimeoutError("no answer"))
        if isinstance(got, int):
            raise _http_error(url, got)
        if isinstance(got, BaseException):
            raise got
        return got

    def primary(self, url, headers):
        return self._answer(url, "primary")

    def fallback(self, url, headers):
        return self._answer(url, "fallback")


def _meta(gw: Gateways, *, uri=f"ipfs://{CID}", gateways=(OURS, GW0, GW1)):
    rpc = ChainRpc(chain="ethereum", eth_call=lambda to, d: _abi_string(uri),
                   get_code=lambda addr: "0x6080")
    return FailoverMetadata(rpcs={"ethereum": rpc}, gateways=list(gateways),
                            http_get=gw.primary, fallback_get=gw.fallback)


# --------------------------------------------------------------------------- #
# 1. O leitor                                                                   #
# --------------------------------------------------------------------------- #


def test_our_gateway_serving_is_one_request_and_no_rescue():
    gw = Gateways(ours=DOC)
    m = _meta(gw)
    assert m.read("ethereum", A, 1).metadata["name"] == "Two Words"
    assert gw.calls == [("ours", "primary")]
    assert m.stats["rescued"] == 0


def test_a_timeout_on_ours_is_rescued_by_the_next_gateway_on_the_short_transport():
    gw = Gateways(ours=TimeoutError(), gw0=DOC)
    m = _meta(gw)
    assert m.read("ethereum", A, 1).metadata["name"] == "Two Words"
    assert gw.calls == [("ours", "primary"), ("gw0", "fallback")]
    assert m.stats["rescued"] == 1


def test_a_throttle_page_on_ours_is_rescued_too():
    gw = Gateways(ours="<html>slow down</html>", gw0=DOC)
    m = _meta(gw)
    assert m.read("ethereum", A, 1).metadata["name"] == "Two Words"
    assert m.stats["rescued"] == 1


def test_timeouts_and_5xx_everywhere_are_ours():
    """A regra do Pedro: sem um 'não tenho' claro, a falha é nossa."""
    gw = Gateways(ours=TimeoutError(), gw0=503, gw1=502)
    m = _meta(gw)
    with pytest.raises(GatewayUnavailable) as e:
        m.read("ethereum", A, 1)
    assert not e.value.theirs
    assert len(gw.calls) == 3                   # every gateway was asked
    assert m.stats["rescued"] == 0


@pytest.mark.parametrize("code", [404, 410])
def test_a_clear_not_found_with_nobody_serving_is_a_dead_pin(code):
    gw = Gateways(ours=TimeoutError(), gw0=code, gw1=503)
    with pytest.raises(MetadataPinGone) as e:
        _meta(gw).read("ethereum", A, 1)
    assert e.value.theirs and e.value.kind == "pin-morto"


def test_a_404_is_overruled_by_a_gateway_that_has_it():
    gw = Gateways(ours=404, gw0=DOC)
    assert _meta(gw).read("ethereum", A, 1).metadata["name"] == "Two Words"


def test_other_4xx_are_not_a_clear_not_found():
    """429 é um throttle, 403 uma recusa do gateway — nenhum diz nada sobre
    o conteúdo. Só 404/410 contam como 'não tenho'."""
    gw = Gateways(ours=429, gw0=403, gw1=TimeoutError())
    with pytest.raises(GatewayUnavailable):
        _meta(gw).read("ethereum", A, 1)


def test_non_json_everywhere_stays_ambiguous():
    gw = Gateways(ours="<html>", gw0="<html>", gw1=TimeoutError())
    with pytest.raises(GatewayNotJson):
        _meta(gw).read("ethereum", A, 1)


def test_a_body_that_is_not_an_object_is_theirs_at_once():
    """O mesmo CID dá os mesmos bytes em qualquer gateway: perguntar a outro
    não muda a resposta."""
    gw = Gateways(ours="[1, 2, 3]", gw0=DOC)
    with pytest.raises(MetadataInvalid):
        _meta(gw).read("ethereum", A, 1)
    assert gw.calls == [("ours", "primary")]


def test_the_same_gateway_twice_is_asked_once():
    gw = Gateways(ours=TimeoutError())
    with pytest.raises(GatewayUnavailable):
        _meta(gw, gateways=(OURS, OURS)).read("ethereum", A, 1)
    assert gw.calls == [("ours", "primary")]


def test_inline_metadata_never_touches_a_gateway():
    uri = "data:application/json," + json.dumps({"name": "On Chain", "image": "x"})
    gw = Gateways()
    assert _meta(gw, uri=uri).read("ethereum", A, 1).metadata["name"] == "On Chain"
    assert gw.calls == []


def test_a_revert_is_still_a_burned_token():
    from finding_memeland.target.adapters import RpcError

    def revert(to, d):
        raise RpcError(3, "execution reverted", revert=True)
    rpc = ChainRpc(chain="ethereum", eth_call=revert, get_code=lambda a: "0x6080")
    gw = Gateways()
    m = FailoverMetadata(rpcs={"ethereum": rpc}, gateways=[OURS, GW0],
                         http_get=gw.primary, fallback_get=gw.fallback)
    assert m.read("ethereum", A, 1) is None
    assert gw.calls == []


def test_no_gateway_is_a_configuration_error():
    with pytest.raises(ValueError):
        FailoverMetadata(rpcs={}, gateways=["", ""], http_get=lambda u, h: "")


def test_the_single_gateway_reader_is_unchanged():
    """O Erc721Metadata (refresh, live hash) continua com UM gateway."""
    gw = Gateways(ours=TimeoutError(), gw0=DOC)
    rpc = ChainRpc(chain="ethereum", eth_call=lambda to, d: _abi_string(f"ipfs://{CID}"),
                   get_code=lambda a: "0x6080")
    m = Erc721Metadata(rpcs={"ethereum": rpc}, gateway=OURS, http_get=gw.primary)
    with pytest.raises(GatewayUnavailable):
        m.read("ethereum", A, 1)
    assert gw.calls == [("ours", "primary")]


def test_a_dead_pin_is_a_chain_unavailable_and_theirs():
    assert issubclass(MetadataPinGone, ChainUnavailable)
    assert MetadataPinGone.theirs and not GatewayUnavailable.theirs


# --------------------------------------------------------------------------- #
# 2. Os relatórios                                                              #
# --------------------------------------------------------------------------- #


def test_harvest_files_a_dead_pin_under_theirs():
    out = _render(MetadataPinGone("1 of 3 gateway(s) said 404/410"))
    assert "defeito-DELES 1 (pin-morto 1)" in out, out
    assert "indisponível-NOSSO" not in out, out


def test_deposit_files_a_dead_pin_under_theirs():
    out = _deposit(read=MetadataPinGone("x"))
    assert "defeito-DELES 1 (pin-morto 1)" in out, out
    assert "indisponível-NOSSO" not in out, out


class _Counter:
    def __init__(self):
        self.stats = {"rescued": 0}


def test_the_harvest_line_says_what_the_rotation_rescued_where():
    meta = _Counter()

    class _RescuingHarv(_Harv):
        def harvest(self, n, **kw):
            meta.stats["rescued"] += 2          # two saved while harvesting
            return super().harvest(n, **kw)

    world = World()
    inner = world.read_token

    def read_token(chain, contract, tid):
        meta.stats["rescued"] += 1              # one saved in the deposit
        return inner(chain, contract, tid)
    world.read_token = read_token

    tw = TargetWiring(
        ports=None, pipeline=None, epoch=None, snapshot_store=None,
        scan_blocks=0, writability_rates={}, uniqueness_rates={},
        finder=_finder(world), larder_store=_Store(),
        harvesters={"ethereum": _RescuingHarv([f"ethereum:{A}:1"])},
        deposit_chains=frozenset({"ethereum"}), larder_meta=meta)
    out = tw.harvest(200)
    assert "rotação de gateways salvou 2 na colheita e 1 no depósito" in out, out


def test_without_the_reader_the_line_says_nothing_about_rotation():
    tw = TargetWiring(
        ports=None, pipeline=None, epoch=None, snapshot_store=None,
        scan_blocks=0, writability_rates={}, uniqueness_rates={},
        finder=_finder(World()), larder_store=_Store(),
        harvesters={"ethereum": _Harv([f"ethereum:{A}:1"])},
        deposit_chains=frozenset({"ethereum"}))
    assert "rotação" not in tw.harvest(200)


# --------------------------------------------------------------------------- #
# 3. O /prepare: só um 'não tenho' claro descarta                               #
# --------------------------------------------------------------------------- #


def _strict_read(exc) -> tuple[object, Tally]:
    world = World()

    def read_token(chain, contract, tid):
        raise exc
    world.read_token = read_token
    tally = Tally()
    got = _finder(world).named_token(Source("larder", "ethereum", A), 1, tally,
                                     strict=True)
    return got, tally


def test_prepare_read_names_a_dead_pin_and_drops_it():
    got, tally = _strict_read(MetadataPinGone("1 of 3 gateway(s) said 404/410"))
    assert got is None
    assert tally.bad_meta == {"pin-morto": 1}


@pytest.mark.parametrize("exc", [
    GatewayUnavailable("3 gateway(s), none answered"),
    GatewayNotJson("x"),
    MetadataInvalid("x"),
    TimeoutError("x"),
])
def test_prepare_read_keeps_every_other_failure(exc):
    with pytest.raises(ReadUnavailable):
        _strict_read(exc)


def _full_larder(world):
    finder = _finder(world)
    larder = Larder()
    finder.fill(larder, want=8, max_draws=200)
    return finder, larder


def test_prepare_spends_candidates_only_on_a_clear_dead_pin():
    world = World()
    finder, larder = _full_larder(world)
    before = larder.size()

    def gone(chain, contract, tid):
        raise MetadataPinGone("1 of 3 gateway(s) said 404/410")
    finder._read_token = gone                   # noqa: SLF001
    with pytest.raises(PrepareRefused) as e:
        _preparer(world, finder).prepare(larder)
    assert "INTACTA" not in str(e.value)
    assert larder.size() < before


def test_prepare_keeps_the_larder_when_every_gateway_merely_fails():
    world = World()
    finder, larder = _full_larder(world)
    before = larder.size()

    def down(chain, contract, tid):
        raise GatewayUnavailable("3 gateway(s), none answered")
    finder._read_token = down                   # noqa: SLF001
    with pytest.raises(PrepareRefused) as e:
        _preparer(world, finder).prepare(larder)
    assert "INTACTA" in str(e.value)
    assert larder.size() == before


# --------------------------------------------------------------------------- #
# 4. A composição                                                               #
# --------------------------------------------------------------------------- #


def _rpc_with_token(url, body, headers):
    req = json.loads(body)
    if req["method"] == "eth_call":
        return json.dumps({"jsonrpc": "2.0", "id": 1,
                           "result": _abi_string(f"ipfs://{CID}")})
    if req["method"] == "eth_getCode":
        return json.dumps({"jsonrpc": "2.0", "id": 1, "result": "0x6001"})
    return json.dumps({"jsonrpc": "2.0", "id": 1, "result": []})


def _built():
    gw = Gateways(**{"gateway.pinata.cloud": TimeoutError(), "gw0": DOC})
    w = build_target(settings(harvest_canary_ethereum="100:1"),
                     anthropic=object(), repo=FakeRepo(),
                     http_get=gw.primary, http_get_fallback=gw.fallback,
                     http_post=_rpc_with_token, http_get_bytes=lambda u, h: b"")
    return w, gw


def test_the_larder_reader_is_ours_first_then_every_public_gateway():
    w, _ = _built()
    assert isinstance(w.larder_meta, FailoverMetadata)
    assert w.larder_meta._gateways == (                          # noqa: SLF001
        "https://gateway.pinata.cloud/ipfs/", "https://gw0/ipfs/", "https://gw1/ipfs/")
    assert w.finder._read_token == w.larder_meta.read            # noqa: SLF001


def test_the_harvest_reads_through_the_rotation():
    w, gw = _built()
    read = w.harvesters["ethereum"]._read_meta(A, 1)               # noqa: SLF001
    assert read.metadata["name"] == "Two Words"
    assert gw.calls == [("gateway.pinata.cloud", "primary"), ("gw0", "fallback")]
    assert w.larder_meta.stats["rescued"] == 1


def test_the_live_check_still_rotates_per_batch_not_per_read():
    """A rotação por leitura é da despensa. O live check continua com um
    provedor por lote — e sem nenhum FailoverMetadata."""
    w, _ = _built()
    gen = w.ports.live_check._generic                              # noqa: SLF001
    with gen.batch() as fetch:
        assert type(fetch) is Erc721Metadata                       # one gateway
    assert [p.gateway for p in gen._providers] == ["https://gw0/ipfs/", "https://gw1/ipfs/"]  # noqa: SLF001
