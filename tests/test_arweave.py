"""A despensa aceita Arweave, de ponta a ponta (09/10 — passo A, desenho do Pedro).

Depois de duas sondas (10 de 58 peças da Manifold passariam COM Arweave, 1
sem), o Pedro aceitou o Arweave e aprovou o desenho com três exigências:

  1. UM GATEWAY DE RESERVA. Nada depende de um só host de Arweave: são
     precisos dois gateways válidos para ligar, e com menos de dois a despensa
     continua a recusar Arweave, exactamente como antes.
  2. A CHAVE DA PINATA NUNCA SAI PARA OUTRO HOST. Duas fechaduras: quem chama
     nunca constrói um cabeçalho para um host de Arweave, e o transporte
     recusa — sem fazer o pedido — qualquer coisa que leve a chave para fora
     do gateway dedicado.
  3. UMA HUNT EM CURSO NÃO DEPENDE DO ARWEAVE. A verificação ao vivo lê só a
     cadeia e compara identidades; com todos os gateways de Arweave em baixo
     nada muda. O único sítio que os lê depois do /prepare é o hash do post de
     void, e esse, sem resposta, diz "indisponível" — como com o IPFS.

E mais duas decisões: "pin morto" em Arweave só com 404 de TODOS os gateways
(a 07/10 um gateway parcial respondeu 404 a oito peças em oito que existiam),
e um alvo sem identidade nunca é selado.
"""
from __future__ import annotations

import json
import urllib.error
from urllib.parse import urlsplit

import pytest
from test_dedicated_gateway import _Harv, _Reader, _Store
from test_harvest import _harv, _mint
from test_image_verdicts import MP4, PNG
from test_metadata_failover import _abi_string
from test_probe import Chain, _Sampler, addr
from test_target_prepare import S, World, _finder, _preparer
from test_target_wiring import FakeRepo, settings

from finding_memeland import main
from finding_memeland.config import ARWEAVE_MIN_GATEWAYS
from finding_memeland.target.adapters import (
    GATEWAY_KEY_HEADER,
    FailoverMetadata,
    GatewayTally,
    arweave_url,
)
from finding_memeland.target.harvest import MintHarvester
from finding_memeland.target.hunt import (
    LIVE_BURNED,
    LIVE_HASH_RESOLVED,
    LIVE_HASH_UNAVAILABLE,
    LIVE_INTACT,
    LIVE_MUTATED,
    LiveCheck,
    LiveRead,
    SealedTarget,
)
from finding_memeland.target.prepare import (
    Candidate,
    Larder,
    PrepareRefused,
    Source,
    Tally,
)
from finding_memeland.target.probe import ContractProbe, cause_of
from finding_memeland.target.refresh import (
    TokenRead,
    arweave_ref,
    content_id,
    uri_is_content_addressed,
)
from finding_memeland.target.selector import Target, metadata_hash
from finding_memeland.target.sources import (
    ChainRpc,
    ChainUnavailable,
    GatewayNotJson,
    GatewayUnavailable,
    ImageGatewaysDown,
    ImagePinGone,
    ImageUnusable,
    MetadataInvalid,
    MetadataPinGone,
)
from finding_memeland.target.wiring import TargetWiring, build_target

KEY = "KEY-SECRET-XYZ-123"
DEDICATED = "https://dedicado.example/ipfs/"
AR1, AR2 = "https://ar-um.example/", "https://ar-dois.example/"
ID = "abcDEF123_-" * 3 + "abcDEF1234"                   # 43 characters
IMG_ID = "Z" * 43
AR_URI = f"ar://{ID}"
AR_HTTPS = f"https://arweave.net/{ID}"
AR_IMG = f"ar://{IMG_ID}"
CID = "bafkre" + "a" * 50
IPFS_URI = f"ipfs://{CID}"
IPFS_IMG = "ipfs://bafyimg" + "b" * 50
A, B = "0x" + "a1" * 20, "0x" + "b2" * 20              # A: IPFS piece · B: Arweave piece
DOC = {"name": "Quiet Lantern Above", "image": AR_IMG, "description": "d"}
IPFS_DOC = {"name": "Quiet Lantern Above", "image": IPFS_IMG, "description": "d"}


def _on(**over):
    return settings(target_arweave_gateways=f"{AR1},{AR2}",
                    harvest_canary_ethereum="100:1", **over)


def _on_with_key(**over):
    return _on(target_ipfs_dedicated_gateway=DEDICATED, target_ipfs_dedicated_key=KEY,
               **over)


def _rpc(url, body, headers):
    """A node where contract A keeps its tokenURI on IPFS and B on Arweave;
    every token has an owner."""
    req = json.loads(body)
    if req["method"] == "eth_call":
        call = req["params"][0]
        to, data = call["to"].lower(), call["data"]
        if data.startswith("0x6352211e"):               # ownerOf
            return json.dumps({"jsonrpc": "2.0", "id": 1,
                               "result": "0x" + "0" * 24 + "c3" * 20})
        uri = AR_URI if to == B else IPFS_URI
        return json.dumps({"jsonrpc": "2.0", "id": 1, "result": _abi_string(uri)})
    if req["method"] == "eth_getCode":
        return json.dumps({"jsonrpc": "2.0", "id": 1, "result": "0x6001"})
    return json.dumps({"jsonrpc": "2.0", "id": 1, "result": []})


class Net:
    """Every request, by host and with its headers; each host answers as
    told — text/bytes, an int (HTTP status) or an exception. A host nobody
    mentioned times out."""

    def __init__(self, **by_host):
        self.by_host = {k.replace("_", "-"): v for k, v in by_host.items()}
        self.seen: list[tuple[str, dict]] = []

    def _answer(self, url, headers):
        host = urlsplit(url).netloc
        self.seen.append((host, dict(headers or {})))
        got = self.by_host.get(host.split(".")[0], self.by_host.get(host, TimeoutError()))
        if isinstance(got, int):
            raise urllib.error.HTTPError(url, got, "x", {}, None)
        if isinstance(got, BaseException):
            raise got
        return got

    def get(self, url, headers=None):
        """The metadata transports: text."""
        got = self._answer(url, headers)
        return got.decode("utf-8", "ignore") if isinstance(got, bytes) else got

    def raw(self, url, headers=None):
        """The artwork transport: bytes."""
        got = self._answer(url, headers)
        return got.encode() if isinstance(got, str) else got

    def ranged(self, url, headers=None):
        return (self.raw(url, headers), 4096)

    def hosts(self) -> list[str]:
        return [h for h, _ in self.seen]

    def keyed_hosts(self) -> set[str]:
        return {h for h, hd in self.seen
                if any(k.lower() == GATEWAY_KEY_HEADER for k in hd)}


def _build(s, net: Net):
    return build_target(s, anthropic=object(), repo=FakeRepo(),
                        http_get=net.get, http_get_fallback=net.get,
                        http_post=_rpc, http_get_bytes=lambda u, h: b"",
                        http_get_range=net.ranged, http_get_larder_art=net.raw)


def _reader(net: Net, *, uri=AR_URI, arweave=(AR1, AR2), headers=None):
    rpc = ChainRpc(chain="ethereum", eth_call=lambda to, d: _abi_string(uri),
                   get_code=lambda a: "0x6080")
    return FailoverMetadata(rpcs={"ethereum": rpc}, gateways=[DEDICATED],
                            http_get=net.get, fallback_get=net.get,
                            gateway_headers=headers or {DEDICATED: {GATEWAY_KEY_HEADER: KEY}},
                            arweave_gateways=list(arweave))


def _target(uri=AR_URI, contract=B) -> Target:
    return Target(chain="ethereum", contract=contract, token_id=1, name="Quiet Lantern",
                  name_onchain="Quiet Lantern", description="", image=AR_IMG,
                  metadata_sha256=metadata_hash(DOC), epoch="e1", token_uri=uri,
                  content_id=content_id(uri) or "")


def _sealed(uri=AR_URI, contract=B) -> SealedTarget:
    return SealedTarget(target=_target(uri, contract), salt="s" * 32, commitment="c" * 64)


# --------------------------------------------------------------------------- #
# 1. O interruptor: dois gateways válidos, ou é como antes                      #
# --------------------------------------------------------------------------- #


def test_nothing_configured_is_off_and_says_so():
    s = settings()
    assert s.target_arweave_gateway_list == [] and s.target_arweave_state == "desligado"


def test_two_valid_gateways_switch_it_on_in_order():
    s = _on()
    assert s.target_arweave_gateway_list == [AR1, AR2]
    assert s.target_arweave_state == "activo — 2 gateways"
    assert ARWEAVE_MIN_GATEWAYS == 2


def test_one_gateway_alone_is_not_enough_nothing_may_depend_on_one_host():
    """A exigência 1 do Pedro, por construção: sem reserva, não liga."""
    s = settings(target_arweave_gateways=AR1)
    assert s.target_arweave_gateway_list == []
    assert s.target_arweave_state.startswith("incompleto — 1 gateway(s) válido(s)")
    assert "são precisos 2" in s.target_arweave_state
    assert "não aceita Arweave" in s.target_arweave_state


@pytest.mark.parametrize("bad", [
    "http://ar-um.example/",                # plain http: the answer could be rewritten
    "https://ar-um.example/raw/",           # a path: every id would be asked for under it
    "https://ar-um.example/?key=K",         # a query
    "https://user@ar-um.example/",          # credentials in a URL
    "ar-um.example", "ftp://ar-um.example/",
])
def test_a_malformed_gateway_does_not_count(bad):
    s = settings(target_arweave_gateways=f"{bad},{AR2}")
    assert s.target_arweave_gateway_list == []                   # one valid: still off
    assert "1 entrada(s) inválida(s) ignorada(s)" in s.target_arweave_state
    s = settings(target_arweave_gateways=f"{AR1},{bad},{AR2}")
    assert s.target_arweave_gateway_list == [AR1, AR2]
    assert s.target_arweave_state.startswith("activo — 2 gateways (1 entrada(s) inválida(s)")


def test_the_same_gateway_twice_is_one_gateway_and_a_bare_host_gets_its_slash():
    assert settings(target_arweave_gateways=f"{AR1},{AR1}").target_arweave_gateway_list == []
    s = settings(target_arweave_gateways="https://ar-um.example, https://ar-dois.example/")
    assert s.target_arweave_gateway_list == [AR1, AR2]


def test_the_state_never_names_a_host():
    for spec in (f"{AR1},{AR2}", AR1, f"http://x.example/,{AR1}"):
        state = settings(target_arweave_gateways=spec).target_arweave_state
        assert "example" not in state and "http" not in state.replace("https://host/", "")


def test_status_shows_the_arweave_state():
    src = open(main.__file__, encoding="utf-8").read()
    assert 'f"arweave: {s.target_arweave_state}"' in src


# --------------------------------------------------------------------------- #
# 2. O que é Arweave, e a identidade                                            #
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("uri, ref", [
    (AR_URI, (ID, "")), (AR_HTTPS, (ID, "")),
    (f"ar://{ID}/metadata.json", (ID, "/metadata.json")),
    (f"https://www.arweave.net/{ID}/1.json?x=1", (ID, "/1.json")),
    (f"http://arweave.net/{ID}", (ID, "")),
])
def test_two_shapes_are_arweave(uri, ref):
    assert arweave_ref(uri) == ref


@pytest.mark.parametrize("uri", [
    f"https://ar-um.example/{ID}",              # one of OUR gateways is not a URI shape
    f"https://evil.example/{ID}", f"https://xyz.arweave.net/{ID}",
    f"https://arweave.net.evil.example/{ID}", f"ar://{ID[:-1]}", f"ar://{ID}x",
    "ar://", "", None, IPFS_URI, "https://example.com/1.json",
])
def test_nothing_else_is(uri):
    assert arweave_ref(uri) is None and (uri is None or content_id(uri) != f"ar:{ID}")


def test_the_identity_is_the_transaction_id_whatever_the_transport():
    """`ar://X` e `https://arweave.net/X` são a MESMA peça — como `ipfs://CID`
    e um gateway com `/ipfs/CID`. É isto que a verificação ao vivo compara."""
    assert content_id(AR_URI) == content_id(AR_HTTPS) == f"ar:{ID}"
    assert content_id(f"ar://{ID}/1.json") == content_id(
        f"https://www.arweave.net/{ID}/1.json?v=2") == f"ar:{ID}/1.json"
    assert content_id(f"ar://{ID}/") == f"ar:{ID}"
    assert content_id(f"ar://{IMG_ID}") != content_id(AR_URI)


def test_recognising_is_not_accepting():
    """A regra de sempre não mexeu: quem aceita Arweave é a despensa, e só
    com gateways configurados."""
    assert uri_is_content_addressed(AR_URI) is False
    assert uri_is_content_addressed(AR_HTTPS) is False
    assert content_id(IPFS_URI) == f"ipfs:{CID}"                 # IPFS untouched


def test_the_url_is_built_on_our_gateway_never_on_the_host_in_the_uri():
    assert arweave_url(AR_HTTPS, AR2) == f"{AR2}{ID}"
    assert arweave_url(f"ar://{ID}/1.json", AR1) == f"{AR1}{ID}/1.json"
    assert arweave_url(AR_URI) == f"https://arweave.net/{ID}"    # the probe's default
    assert arweave_url(f"https://evil.example/{ID}", AR1) is None


# --------------------------------------------------------------------------- #
# 3. A leitura da metadata: os nossos gateways, com reserva                     #
# --------------------------------------------------------------------------- #


def test_the_first_gateway_serving_is_one_request_with_nothing_of_ours_in_it():
    net = Net(ar_um=json.dumps(DOC))
    m = _reader(net)
    read = m.read("ethereum", B, 1)
    assert read.token_uri == AR_URI and read.metadata["name"] == "Quiet Lantern Above"
    assert net.seen == [("ar-um.example", {"Accept": "application/json"})]
    assert m.arweave.stats["outcomes"] == {1: {"serviu": 1}}
    assert m.stats["outcomes"] == {}                 # not a request to an IPFS gateway


def test_a_uri_that_names_arweave_net_is_still_read_from_ours():
    net = Net(ar_um=json.dumps(DOC))
    _reader(net, uri=AR_HTTPS).read("ethereum", B, 1)
    assert net.hosts() == ["ar-um.example"]


@pytest.mark.parametrize("first", [TimeoutError(), 503, 429, 404, "<html>busy</html>"])
def test_the_reserve_serves_what_the_first_gateway_could_not(first):
    net = Net(ar_um=first, ar_dois=json.dumps(DOC))
    m = _reader(net)
    assert m.read("ethereum", B, 1).metadata == DOC
    assert net.hosts() == ["ar-um.example", "ar-dois.example"]
    assert m.arweave.stats["outcomes"][2] == {"serviu": 1}


def test_a_dead_pin_takes_every_gateway_saying_so():
    net = Net(ar_um=404, ar_dois=410)
    with pytest.raises(MetadataPinGone) as e:
        _reader(net).read("ethereum", B, 1)
    assert e.value.theirs is True


@pytest.mark.parametrize("answers", [
    dict(ar_um=404, ar_dois=TimeoutError()),         # the partial gateway of 07/10
    dict(ar_um=TimeoutError(), ar_dois=404),
    dict(ar_um=404, ar_dois=503),
    dict(ar_um=TimeoutError(), ar_dois=TimeoutError()),
    dict(ar_um=500, ar_dois=429),
])
def test_one_404_among_failures_is_ours_and_the_target_stays(answers):
    with pytest.raises(GatewayUnavailable) as e:
        _reader(Net(**answers)).read("ethereum", B, 1)
    assert not getattr(e.value, "theirs", False)
    assert not isinstance(e.value, MetadataPinGone)


def test_a_single_gateway_can_never_declare_a_dead_pin():
    """O interruptor nem liga com um, mas o leitor também não confia."""
    with pytest.raises(GatewayUnavailable):
        _reader(Net(ar_um=404), arweave=(AR1,)).read("ethereum", B, 1)


def test_an_answer_that_is_not_json_and_one_that_is_not_an_object():
    with pytest.raises(GatewayNotJson):
        _reader(Net(ar_um="<html>", ar_dois="<html>")).read("ethereum", B, 1)
    with pytest.raises(MetadataInvalid):
        _reader(Net(ar_um="[1, 2]")).read("ethereum", B, 1)
    with pytest.raises(MetadataInvalid):
        _reader(Net(ar_um="x" * 2_000_001)).read("ethereum", B, 1)


def test_without_gateways_an_arweave_uri_is_what_it_always_was():
    net = Net(ar_um=json.dumps(DOC))
    read = _reader(net, arweave=()).read("ethereum", B, 1)
    assert read.token_uri == AR_URI and read.metadata is None
    assert net.seen == []


def test_an_ipfs_uri_still_goes_to_the_ipfs_gateways_with_the_key():
    net = Net(dedicado=json.dumps(IPFS_DOC))
    m = _reader(net, uri=IPFS_URI)
    assert m.read("ethereum", A, 1).metadata == IPFS_DOC
    assert net.keyed_hosts() == {"dedicado.example"} and m.arweave.stats["outcomes"] == {}


# --------------------------------------------------------------------------- #
# 4. O teste da imagem e a arte para a visão                                    #
# --------------------------------------------------------------------------- #


def test_the_image_test_asks_the_arweave_gateways_and_counts_them_apart():
    net = Net(ar_um=PNG)
    w = _build(_on_with_key(), net)
    head, size = w.finder._probe_image(AR_IMG)                     # noqa: SLF001
    assert head == PNG and size == 4096
    assert net.hosts() == ["ar-um.example"] and not net.keyed_hosts()
    assert set(net.seen[0][1]) == {"Range"}
    assert w.arweave_image.stats["outcomes"] == {1: {"serviu": 1}}
    assert w.larder_image.stats["outcomes"] == {}         # not the dedicated's quota


def test_the_reserve_serves_the_image_too():
    net = Net(ar_um=TimeoutError(), ar_dois=PNG)
    w = _build(_on(), net)
    assert w.finder._probe_image(AR_IMG)[0] == PNG                 # noqa: SLF001
    assert net.hosts() == ["ar-um.example", "ar-dois.example"]


@pytest.mark.parametrize("answers, verdict", [
    (dict(ar_um=MP4), ImageUnusable),                       # theirs: a video
    (dict(ar_um=404, ar_dois=404), ImagePinGone),           # theirs: every gateway
    (dict(ar_um=404, ar_dois=TimeoutError()), ImageGatewaysDown),   # ours
    (dict(ar_um=TimeoutError(), ar_dois=503), ImageGatewaysDown),
    (dict(ar_um=b"<html>busy</html>", ar_dois=b""), ImageGatewaysDown),
])
def test_the_image_verdicts_are_the_larder_s_with_the_unanimous_404(answers, verdict):
    w = _build(_on(), Net(**answers))
    with pytest.raises(verdict):
        w.finder._probe_image(AR_IMG)                              # noqa: SLF001


def test_an_ipfs_image_keeps_the_rule_it_had_one_404_is_a_dead_pin():
    w = _build(_on(), Net(**{"gateway.pinata.cloud": 404}))
    with pytest.raises(ImagePinGone):
        w.finder._probe_image(IPFS_IMG)                            # noqa: SLF001


def test_the_artwork_for_vision_comes_from_the_arweave_gateways_without_headers():
    net = Net(ar_um=TimeoutError(), ar_dois=PNG)
    w = _build(_on_with_key(), net)
    with pytest.raises(ChainUnavailable):
        w.larder_preparer._fetch_image(AR_IMG)       # PNG magic that won't open: ours
    assert net.seen == [("ar-um.example", {}), ("ar-dois.example", {})]


def test_with_the_switch_off_an_arweave_image_is_not_even_asked_for():
    net = Net(ar_um=PNG)
    w = _build(settings(harvest_canary_ethereum="100:1"), net)
    assert w.arweave_gateways == 0
    assert w.finder._accepts(AR_IMG) is False                      # noqa: SLF001
    assert w.harvesters["ethereum"]._accepts_image(AR_IMG) is False   # noqa: SLF001
    assert w.harvesters["ethereum"]._read_meta(B, 1).metadata is None  # noqa: SLF001
    assert net.seen == []


# --------------------------------------------------------------------------- #
# 5. A colheita e o depósito aceitam-no — com o interruptor ligado              #
# --------------------------------------------------------------------------- #


def test_the_larder_s_rule_widens_only_with_gateways():
    on, off = _build(_on(), Net()), _build(settings(), Net())
    for uri in (AR_URI, AR_HTTPS, AR_IMG):
        assert on.finder._accepts(uri) is True                     # noqa: SLF001
        assert off.finder._accepts(uri) is False                   # noqa: SLF001
    for w in (on, off):
        assert w.finder._accepts(IPFS_IMG) is True                 # noqa: SLF001
        assert w.finder._accepts("https://example.com/1.png") is False   # noqa: SLF001
        assert w.finder._accepts(f"https://evil.example/{ID}") is False  # noqa: SLF001


def test_the_harvest_reads_an_arweave_token_and_keeps_its_arweave_image():
    net = Net(ar_um=json.dumps(DOC))
    w = _build(_on(), net)
    h = w.harvesters["ethereum"]
    read = h._read_meta(B, 1)                                      # noqa: SLF001
    assert read.metadata == DOC and h._accepts_image(DOC["image"]) is True  # noqa: SLF001


def _harvest_one(read: TokenRead, accepts=None):
    """One block with one mint (test_harvest's world), read as `read`."""
    h, _ = _harv(logs_by_block={100: [_mint()], 7: [_mint(B, 3)]},
                 reads={(B, 3): read}, canary_mints=1)
    if accepts is not None:
        h._accepts_image = accepts                                 # noqa: SLF001

    class _Seven:
        def randrange(self, lo, hi):
            return 7
    h._rng = _Seven()                                              # noqa: SLF001
    return h.harvest(1)


def test_the_harvester_filters_the_image_by_the_rule_it_is_given():
    read = TokenRead(token_uri=AR_URI, metadata=DOC)
    refs, rep = _harvest_one(read)                          # the default rule
    assert refs == [] and rep.image_not_ca == {"arweave": 1}
    refs, rep = _harvest_one(read, accepts=_wide)           # the larder's, switched on
    assert len(refs) == 1 and rep.image_not_ca == {}


def test_the_rule_is_a_constructor_argument_and_defaults_to_the_old_one():
    def make(**kw):
        return MintHarvester(chain="ethereum", latest_block=lambda: 1,
                             get_logs=lambda a, b: [], read_meta=lambda c, t: None, **kw)
    assert make(accepts_image=_wide)._accepts_image is _wide       # noqa: SLF001
    assert make()._accepts_image is uri_is_content_addressed       # noqa: SLF001


class _ArweaveChain(World):
    """The fake chain of test_target_prepare, with every piece on Arweave."""

    reads_arweave = True

    def read_token(self, chain, contract, token_id):
        if not self.reads_arweave:
            return TokenRead(token_uri=AR_URI, metadata=None)
        return TokenRead(token_uri=AR_URI, metadata={
            "name": self.names.get(token_id, self.default_name),
            "image": AR_IMG, "description": "d"})


def _wide(uri: str) -> bool:
    return uri_is_content_addressed(uri) or arweave_ref(uri) is not None


def test_a_deposited_arweave_piece_is_sealed_with_its_identity():
    chain = _ArweaveChain()
    larder = Larder()
    rep = _finder(chain, accepts_uri=_wide).deposit(larder, [f"ethereum:{S.contract}:5"])
    assert rep.added == 1 and chain.paid_calls == 1         # every check ran
    target = larder.candidates[0].to_target("e1")
    assert target.token_uri == AR_URI and target.content_id == f"ar:{ID}"
    assert target.image == AR_IMG


def test_with_the_rule_of_always_the_same_piece_is_refused_by_its_image():
    chain = _ArweaveChain()
    larder = Larder()
    rep = _finder(chain).deposit(larder, [f"ethereum:{S.contract}:5"])
    assert rep.added == 0 and rep.rejected.not_ca == {"arweave": 1}
    assert chain.probes == [] and chain.paid_calls == 0


# --------------------------------------------------------------------------- #
# 6. Um alvo sem identidade nunca é selado                                      #
# --------------------------------------------------------------------------- #


class _NoIdentity(World):
    """`ipfs://` em frente de algo que não é um CID que saibamos nomear — e
    um leitor que, mesmo assim, resolveu a metadata."""

    def read_token(self, chain, contract, token_id):
        return TokenRead(token_uri="ipfs://not-a-cid-we-can-name",
                         metadata={"name": self.default_name,
                                   "image": f"ipfs://img{token_id}", "description": "d"})


def test_what_the_live_check_would_do_with_such_a_target():
    """Porque é que a guarda existe: sem identidade, a primeira verificação
    ao vivo levanta — a hunt ficava em hold com a Clue 1 publicada."""
    assert content_id("ipfs://not-a-cid-we-can-name") is None
    sealed = _sealed(uri="ipfs://not-a-cid-we-can-name")
    with pytest.raises(ValueError):
        LiveCheck(read_live=lambda *a: LiveRead("x", "0xowner")).check(sealed)


def test_it_is_refused_at_the_door_with_its_own_cause_and_nothing_spent():
    chain = _NoIdentity()
    larder = Larder()
    rep = _finder(chain).deposit(larder, [f"ethereum:{S.contract}:5"])
    assert rep.added == 0 and rep.rejected.no_identity == 1
    assert chain.probes == [] and chain.paid_calls == 0
    assert "sem-identidade 1" in rep.render()
    assert cause_of(Tally(no_identity=1)) == ("sem-identidade", None)


def test_every_other_cause_keeps_its_own_name():
    """A última das verificações grátis: só conta o que de outro modo seguia."""
    tally = _finder(_NoIdentity(default_name="Untitled")).fill(Larder(), 1, max_draws=3)
    assert tally.name == 3 and tally.no_identity == 0


def test_prepare_discards_one_it_finds_in_the_larder_and_says_why():
    chain = _NoIdentity()
    larder = Larder()
    larder.add(Candidate(chain="ethereum", contract=S.contract, token_id=5,
                         name="Some Two Words", name_onchain="Some Two Words",
                         description="d", image="ipfs://img5", token_uri="ipfs://x",
                         artist="", metadata={"name": "Some Two Words"}))
    said: list[str] = []
    with pytest.raises(PrepareRefused) as e:
        _preparer(chain, _finder(chain), notify=said.append).prepare(larder)
    assert larder.size() == 0 and chain.full_reads == [] and chain.paid_calls == 0
    assert any("não tem identidade" in m for m in said)
    assert "sem-identidade 1" in str(e.value)


# --------------------------------------------------------------------------- #
# 7. Uma hunt em curso: a verificação ao vivo lê só a cadeia                    #
# --------------------------------------------------------------------------- #


def _live(uri, owner="0xowner"):
    return LiveCheck(read_live=lambda c, k, t: LiveRead(token_uri=uri, owner=owner))


def test_the_same_transaction_under_another_transport_is_intact():
    assert _live(AR_HTTPS).check(_sealed(AR_URI)).status == LIVE_INTACT
    assert _live(AR_URI).check(_sealed(AR_HTTPS)).status == LIVE_INTACT


@pytest.mark.parametrize("now", [f"ar://{IMG_ID}", IPFS_URI, "https://example.com/1.json",
                                 f"ar://{ID}/other.json"])
def test_another_transaction_or_another_scheme_is_a_mutation(now):
    assert _live(now).check(_sealed(AR_URI)).status == LIVE_MUTATED


def test_a_token_without_an_owner_is_burned_as_ever():
    assert _live(AR_URI, owner=None).check(_sealed(AR_URI)).status == LIVE_BURNED


def test_with_every_arweave_gateway_down_the_live_check_does_not_notice():
    """A exigência 3 do Pedro. `Net()` não responde a ninguém — nem Arweave,
    nem IPFS — e a verificação ao vivo, pelo caminho de produção (provedores
    públicos, só RPC), dá INTACTO sem ter feito um único pedido a um gateway."""
    net = Net()
    w = _build(_on_with_key(), net)
    verdict = w.ports.live_check.check(_sealed(AR_URI))
    assert verdict.status == LIVE_INTACT
    assert net.seen == []


# --------------------------------------------------------------------------- #
# 8. Gateways tirados da configuração: o alvo de Arweave fica na despensa       #
# --------------------------------------------------------------------------- #


def _arweave_candidate(token_id=5) -> Candidate:
    return Candidate(chain="ethereum", contract=S.contract, token_id=token_id,
                     name="Some Two Words", name_onchain="Some Two Words",
                     description="d", image=AR_IMG, token_uri=AR_URI, artist="",
                     metadata=DOC)


def test_prepare_keeps_an_arweave_target_when_the_gateways_are_gone():
    """Desligar o Arweave não pode gastar alvos: é configuração NOSSA, não
    uma resposta sobre o conteúdo (regra do Pedro, 29/09)."""
    chain = _ArweaveChain()
    chain.reads_arweave = False
    larder = Larder()
    for tid in (5, 6, 7):
        larder.add(_arweave_candidate(tid))
    said: list[str] = []
    with pytest.raises(PrepareRefused) as e:
        _preparer(chain, _finder(chain), notify=said.append).prepare(larder)
    assert larder.size() == 3                               # nothing was spent
    assert "despensa INTACTA" in str(e.value)
    assert any("arweave-desligado" in m and "MANTIDO" in m for m in said)
    assert chain.paid_calls == 0 and chain.full_reads == []


def test_and_it_is_prepared_again_the_day_they_come_back():
    chain = _ArweaveChain()
    larder = Larder()
    larder.add(_arweave_candidate(5))
    prepared, left = _preparer(chain, _finder(chain, accepts_uri=_wide)).prepare(larder)
    assert prepared.target.content_id == f"ar:{ID}" and left.size() == 0


def test_the_draw_and_the_deposit_do_not_change_for_an_unread_arweave_uri():
    chain = _ArweaveChain()
    chain.reads_arweave = False
    rep = _finder(chain).deposit(Larder(), [f"ethereum:{S.contract}:5"])
    assert rep.rejected.metadata == 1                       # as before this change


# --------------------------------------------------------------------------- #
# 9. O hash do post de void: com reserva, e "indisponível" quando ninguém serve #
# --------------------------------------------------------------------------- #


def test_the_live_hash_reads_arweave_through_our_gateways():
    net = Net(ar_um=json.dumps(DOC))
    h = _build(_on_with_key(), net).ports.live_hash(_sealed())
    assert (h.status, h.sha256) == (LIVE_HASH_RESOLVED, metadata_hash(DOC))
    assert net.hosts() == ["ar-um.example"] and not net.keyed_hosts()


def test_the_reserve_serves_the_live_hash_when_the_first_gateway_is_down():
    net = Net(ar_um=TimeoutError(), ar_dois=json.dumps(DOC))
    h = _build(_on(), net).ports.live_hash(_sealed())
    assert h.status == LIVE_HASH_RESOLVED and h.sha256 == metadata_hash(DOC)


@pytest.mark.parametrize("answers", [
    dict(), dict(ar_um=503, ar_dois=TimeoutError()),
    dict(ar_um=404, ar_dois=404),            # a gateway's 404 is not the CHAIN's word
    dict(ar_um="<html>", ar_dois="<html>"),
])
def test_with_no_gateway_serving_the_post_says_unavailable_never_a_guess(answers):
    h = _build(_on(), Net(**answers)).ports.live_hash(_sealed())
    assert h.status == LIVE_HASH_UNAVAILABLE and h.sha256 is None


def test_an_ipfs_target_s_live_hash_is_read_exactly_as_before():
    net = Net(**{"gateway.pinata.cloud": json.dumps(IPFS_DOC)})
    h = _build(_on_with_key(), net).ports.live_hash(_sealed(IPFS_URI, contract=A))
    assert h.status == LIVE_HASH_RESOLVED
    assert net.hosts() == ["gateway.pinata.cloud"] and not net.keyed_hosts()


# --------------------------------------------------------------------------- #
# 10. A chave da Pinata nunca sai para outro host                               #
# --------------------------------------------------------------------------- #


def _everything(w) -> None:
    """Every larder path and the void hash, for an IPFS piece and an Arweave
    one. Failures are part of the run: what matters is who was asked what."""
    for step in (
            lambda: w.harvesters["ethereum"]._read_meta(A, 1),         # noqa: SLF001
            lambda: w.harvesters["ethereum"]._read_meta(B, 1),         # noqa: SLF001
            lambda: w.finder._probe_image(IPFS_IMG),                   # noqa: SLF001
            lambda: w.finder._probe_image(AR_IMG),                     # noqa: SLF001
            lambda: w.larder_preparer._fetch_image(IPFS_IMG),          # noqa: SLF001
            lambda: w.larder_preparer._fetch_image(AR_IMG),            # noqa: SLF001
            lambda: w.ports.live_hash(_sealed(IPFS_URI, contract=A)),
            lambda: w.ports.live_hash(_sealed())):
        try:
            step()
        except ChainUnavailable:
            pass


@pytest.mark.parametrize("world", [
    dict(),                                                  # nobody answers anybody
    dict(dedicado=json.dumps(IPFS_DOC), ar_um=json.dumps(DOC)),
    dict(dedicado=404, ar_um=404, ar_dois=404),
    dict(dedicado=TimeoutError(), ar_um=TimeoutError(), ar_dois=json.dumps(DOC),
         **{"gateway.pinata.cloud": json.dumps(IPFS_DOC)}),
    dict(dedicado=PNG, ar_um=PNG, ar_dois=PNG),
])
def test_first_lock_no_caller_ever_puts_the_key_on_another_host(world):
    """O teste que o Pedro pediu, pelo lado de quem chama: uma corrida
    inteira, com IPFS e Arweave misturados, em vários estados da rede."""
    net = Net(**world)
    _everything(_build(_on_with_key(), net))
    assert net.keyed_hosts() == {"dedicado.example"}
    asked = set(net.hosts())
    assert {"dedicado.example", "ar-um.example"} <= asked    # both worlds were asked
    for host, headers in net.seen:
        if host != "dedicado.example":
            assert not any(k.lower() == GATEWAY_KEY_HEADER for k in headers), host
            assert KEY not in repr(headers)


class _Wire:
    """urllib's opener, replaced: records what would have gone on the wire."""

    def __init__(self, body: bytes = b"{}"):
        self.sent: list[tuple[str, dict]] = []
        self._body = body

    def open(self, req, timeout=None):
        self.sent.append((urlsplit(req.full_url).netloc,
                          {k.lower(): v for k, v in req.header_items()}))
        return _Body(self._body)

    def keyed(self) -> set[str]:
        return {h for h, hd in self.sent if GATEWAY_KEY_HEADER in hd}


class _Body:
    def __init__(self, data: bytes):
        self._data, self.headers = data, {"Content-Length": str(len(data))}

    def read(self, n=-1):
        out, self._data = (self._data, b"") if n < 0 else (self._data[:n], self._data[n:])
        return out

    read1 = read

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


@pytest.fixture
def wire(monkeypatch):
    w = _Wire()
    monkeypatch.setattr(main._GATEWAY_OPENER, "open", w.open)      # noqa: SLF001
    monkeypatch.setattr(main, "_KEY_HOME", ("https", "dedicado.example"))
    return w


TRANSPORTS = [main._http_get_bytes, main._http_get, main._http_get_fallback,       # noqa: SLF001
              main._http_get_image_bytes, main._http_get_range,                    # noqa: SLF001
              main._http_get_larder_art]                                           # noqa: SLF001


@pytest.mark.parametrize("get", TRANSPORTS)
@pytest.mark.parametrize("url", [
    f"{AR1}{ID}", f"https://arweave.net/{ID}", "https://gateway.pinata.cloud/ipfs/x",
    "http://dedicado.example/ipfs/x",                 # the right host in clear text
    "https://dedicado.example.evil.example/ipfs/x", "https://dedicado.example:8443/ipfs/x",
])
def test_second_lock_the_transport_refuses_the_key_for_any_other_host(wire, get, url):
    """…e pelo lado do transporte: mesmo que um dia alguém construa o pedido
    errado, ele não sai. Nem uma ligação é aberta."""
    with pytest.raises(main.GatewayKeyRefused) as e:
        get(url, {GATEWAY_KEY_HEADER: KEY})
    assert wire.sent == []
    assert KEY not in str(e.value) and "example" not in str(e.value)


@pytest.mark.parametrize("get", TRANSPORTS)
def test_the_key_still_reaches_the_dedicated_gateway(wire, get):
    get("https://dedicado.example/ipfs/x", {GATEWAY_KEY_HEADER: KEY})
    assert wire.keyed() == {"dedicado.example"}


@pytest.mark.parametrize("get", TRANSPORTS)
def test_a_request_without_the_key_goes_anywhere_as_before(wire, get):
    get(f"{AR1}{ID}", {"Accept": "application/json"})
    get("https://gateway.pinata.cloud/ipfs/x", None)
    assert len(wire.sent) == 2 and wire.keyed() == set()


def test_the_header_is_recognised_in_any_case(wire):
    with pytest.raises(main.GatewayKeyRefused):
        main._http_get_bytes(f"{AR1}{ID}", {GATEWAY_KEY_HEADER.upper(): KEY})   # noqa: SLF001
    assert wire.sent == []


def test_with_no_dedicated_gateway_the_key_may_go_nowhere(wire, monkeypatch):
    monkeypatch.setattr(main, "_KEY_HOME", None)
    with pytest.raises(main.GatewayKeyRefused):
        main._http_get_bytes("https://dedicado.example/ipfs/x",                 # noqa: SLF001
                             {GATEWAY_KEY_HEADER: KEY})
    assert wire.sent == []


def test_the_post_transport_is_held_to_the_same_rule(monkeypatch):
    monkeypatch.setattr(main, "_KEY_HOME", ("https", "dedicado.example"))
    with pytest.raises(main.GatewayKeyRefused):
        main._http_post("https://rpc.example/", b"{}", {GATEWAY_KEY_HEADER: KEY})  # noqa: SLF001


def test_the_home_comes_from_the_settings_the_agent_was_built_with():
    assert main._home_of(_on_with_key().target_ipfs_dedicated) == (             # noqa: SLF001
        "https", "dedicado.example")
    assert main._home_of(settings().target_ipfs_dedicated) is None              # noqa: SLF001
    src = open(main.__file__, encoding="utf-8").read()
    assert "_KEY_HOME = _home_of(s.target_ipfs_dedicated)" in src


def test_both_locks_together_a_whole_run_through_the_real_transports(wire):
    """A corrida inteira outra vez, agora pelos transportes de produção: o
    que chega ao fio leva a chave só para o dedicado, e a segunda fechadura
    nunca teve de recusar nada — quem chama já não a põe noutro sítio."""
    w = build_target(
        _on_with_key(), anthropic=object(), repo=FakeRepo(), http_post=_rpc,
        http_get=main._http_get, http_get_fallback=main._http_get_fallback,     # noqa: SLF001
        http_get_bytes=main._http_get_image_bytes,                              # noqa: SLF001
        http_get_range=main._http_get_range,                                    # noqa: SLF001
        http_get_larder_art=main._http_get_larder_art)                          # noqa: SLF001
    _everything(w)
    assert wire.keyed() == {"dedicado.example"}
    hosts = {h for h, _ in wire.sent}
    assert {"dedicado.example", "ar-um.example"} <= hosts
    assert "gateway.pinata.cloud" in hosts                   # the void hash's gateway


def test_the_key_is_in_no_report_no_error_and_no_repr():
    net = Net()
    w = _build(_on_with_key(), net)
    errors = []
    for step in (lambda: w.harvesters["ethereum"]._read_meta(B, 1),             # noqa: SLF001
                 lambda: w.finder._probe_image(AR_IMG)):                         # noqa: SLF001
        with pytest.raises(ChainUnavailable) as e:
            step()
        errors += [str(e.value), repr(e.value)]
    for text in [*errors, repr(w), str(w._arweave_snapshot())]:                 # noqa: SLF001
        assert KEY not in text and "example" not in text


# --------------------------------------------------------------------------- #
# 11. O relatório: os gateways de Arweave à parte, por posição                  #
# --------------------------------------------------------------------------- #


def _report(*, gateways: int) -> str:
    meta, image = _Reader(), GatewayTally()
    meta.gateway_count = 2
    ar_meta, ar_image = GatewayTally(), GatewayTally()

    class _H(_Harv):
        def harvest(self, n, **kw):
            meta.note(1, "serviu", 10)
            for _ in range(3):
                ar_meta.note(1, "serviu")
            ar_meta.note(1, "timeout")
            ar_meta.note(2, "serviu")
            return super().harvest(n, **kw)

    world = World()
    inner = world.probe_image

    def probe(url):
        ar_image.note(1, "404")
        ar_image.note(2, "serviu")
        return inner(url)
    world.probe_image = probe
    tw = TargetWiring(
        ports=None, pipeline=None, epoch=None, snapshot_store=None,
        scan_blocks=0, writability_rates={}, uniqueness_rates={},
        finder=_finder(world), larder_store=_Store(),
        harvesters={"ethereum": _H([f"ethereum:{A}:1"])},
        deposit_chains=frozenset({"ethereum"}),
        larder_meta=meta, larder_image=image, dedicated_gateway=True,
        arweave_meta=ar_meta, arweave_image=ar_image, arweave_gateways=gateways)
    return tw.harvest(200)


def test_the_harvest_line_names_each_arweave_gateway_by_position():
    out = _report(gateways=2)
    assert "arweave 1 — colheita: serviu 3, timeout 1; depósito (imagem): 404 1" in out, out
    assert "arweave 2 — colheita: serviu 1; depósito (imagem): serviu 1" in out, out
    assert "example" not in out and "http" not in out


def test_arweave_requests_are_not_the_dedicated_gateway_s_quota():
    out = _report(gateways=2)
    assert out.rstrip().endswith(
        "gateway dedicado: 10 pedido(s) nesta corrida (metadata 10, imagem 0)"), out


def test_a_run_that_asked_arweave_nothing_says_nothing_about_it():
    tw = TargetWiring(
        ports=None, pipeline=None, epoch=None, snapshot_store=None,
        scan_blocks=0, writability_rates={}, uniqueness_rates={},
        finder=_finder(World()), larder_store=_Store(),
        harvesters={"ethereum": _Harv([f"ethereum:{A}:1"])},
        deposit_chains=frozenset({"ethereum"}),
        arweave_meta=GatewayTally(), arweave_image=GatewayTally(), arweave_gateways=2)
    assert "arweave" not in tw.harvest(200)


# --------------------------------------------------------------------------- #
# 12. A sonda: "como hoje" passa a ser o que a despensa aceita                  #
# --------------------------------------------------------------------------- #


def _probe_one(uri, meta, *, accepts_today=None):
    c = addr(1)
    chain = Chain({c: {"tokens": {1: uri}}}, metadata={uri: meta})
    # a reader that resolves Arweave itself, as the larder's does when on
    chain.read_token = lambda contract, tid: TokenRead(token_uri=uri, metadata=meta)
    return ContractProbe(
        source="manifold", chain="base", sampler=_Sampler([c]),
        eth_call=chain.eth_call, token_uri=chain.token_uri,
        read_token=chain.read_token, arweave_json=chain.arweave_json,
        finder=_finder(World(), accepts_uri=_wide),
        accepts_today=accepts_today).run(1)


def test_with_the_switch_on_both_columns_say_the_same():
    meta = {"name": "Grease Pencil Gospel", "image": AR_IMG}
    rep = _probe_one(AR_URI, meta, accepts_today=_wide)
    assert rep.today.passed == 1 and rep.with_arweave.passed == 1
    assert not rep.today.causes


def test_with_it_off_the_image_still_stops_the_piece_today():
    meta = {"name": "Grease Pencil Gospel", "image": AR_IMG}
    rep = _probe_one(IPFS_URI, meta)
    assert rep.today.causes == {("imagem fora de IPFS", "arweave"): 1}
    assert rep.with_arweave.passed == 1


def test_production_gives_the_probe_the_larder_s_rule():
    on, off = _build(_on(), Net()), _build(settings(), Net())
    assert on.probes["manifold"]._accepts_today(AR_IMG) is True       # noqa: SLF001
    assert off.probes["manifold"]._accepts_today(AR_IMG) is False     # noqa: SLF001
    assert on.arweave_gateways == 2 and off.arweave_gateways == 0
    assert on.arweave_meta is on.larder_meta.arweave                 # one counter
    assert Source("x", "base", addr(1)).chain == "base"
