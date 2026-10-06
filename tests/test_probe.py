"""/probe manifold [n] — medir uma fonte antes de a adoptar (06/10).

Os mints ao calhas em Base não dão alvos (0 em 101), o Pedro não quer
escolhas à mão ("no human picks"), e a arte 1/1 de Base vive nos contratos
de criador da Manifold — dos quais 56 em 57 guardam o tokenURI em Arweave,
que a despensa hoje não aceita. O que isto fixa:

  · o sorteio é uniforme e sem lista: um número de criação do deployer, e o
    bloco por bissecção sobre o nonce dele;
  · as cinco verificações são as do depósito, pelo mesmo código;
  · duas colunas — como hoje / se o Arweave fosse aceite — e a segunda lê
    metadata E imagem de arweave.net, só de lá;
  · a despensa continua a recusar Arweave: o finder que o aceita é só o da
    sonda;
  · não guarda nada, e o relatório são contagens — nunca um contrato, um
    tokenId ou um nome.
"""
from __future__ import annotations

import json
import random
import urllib.error

import pytest
from test_metadata_failover import _abi_string
from test_target_prepare import World, _finder
from test_target_wiring import FakeRepo, settings

from finding_memeland.target.adapters import (
    ArweaveGateway,
    GatewayTally,
    RpcError,
    arweave_url,
)
from finding_memeland.target.prepare import Tally
from finding_memeland.target.probe import (
    MANIFOLD_BASE_DEPLOYER,
    PROBE_USAGE,
    ContractProbe,
    DeployerSampler,
    ProbeBlind,
    ProbeReport,
    cause_of,
    parse_probe_args,
)
from finding_memeland.target.refresh import TokenRead, uri_is_content_addressed
from finding_memeland.target.sources import (
    GatewayUnavailable,
    ImageGatewaysDown,
    ImagePinGone,
    MetadataInvalid,
    MetadataPinGone,
)
from finding_memeland.target.wiring import TargetWiring, build_target

D = MANIFOLD_BASE_DEPLOYER
AR_ID = "abcDEF123_-" * 3 + "abcDEF1234"                    # 43 chars
AR_META = f"https://arweave.net/{AR_ID}"
AR_IMG = "ar://" + "Z" * 43
IPFS_META = "ipfs://bafkre" + "a" * 50
IPFS_IMG = "ipfs://bafyimg" + "b" * 50
OT = "0x8be0079c531659141344cd1fd0a4f28419497f9722a3daafe3b4186f6b6457e0"
PAD = "0x" + "0" * 24 + D[2:]
PNG = b"\x89PNG\r\n\x1a\n" + b"x" * 64


def addr(n: int) -> str:
    return "0x" + f"{n:040x}"


# --------------------------------------------------------------------------- #
# 0. O comando e o URL de Arweave                                               #
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("arg, want", [
    ("manifold", ("manifold", 60)), ("manifold 25", ("manifold", 25)),
    ("25 MANIFOLD", ("manifold", 25)),
])
def test_the_command_takes_a_source_name_and_a_count(arg, want):
    assert parse_probe_args(arg) == want


@pytest.mark.parametrize("arg", ["", "60", "manifold 0", "manifold 301", "zora",
                                 "manifold 5 5", "0x" + "ab" * 20, "manifold " + "0x" + "ab" * 20])
def test_anything_else_is_refused_and_never_an_address(arg):
    with pytest.raises(ValueError) as e:
        parse_probe_args(arg)
    assert str(e.value) == PROBE_USAGE


@pytest.mark.parametrize("uri, url", [
    (f"ar://{AR_ID}", f"https://arweave.net/{AR_ID}"),
    (f"https://arweave.net/{AR_ID}", f"https://arweave.net/{AR_ID}"),
    (f"https://www.arweave.net/{AR_ID}/1.json?x=1", f"https://arweave.net/{AR_ID}/1.json"),
    (f"http://arweave.net/{AR_ID}", f"https://arweave.net/{AR_ID}"),
])
def test_an_arweave_uri_is_read_from_arweave_net_only(uri, url):
    assert arweave_url(uri) == url


@pytest.mark.parametrize("uri", [
    "", None, IPFS_META, "https://example.com/meta.json",
    f"https://arweave.net.evil.example/{AR_ID}",          # a look-alike host
    f"https://evil.example/arweave.net/{AR_ID}",
    "https://arweave.net/short", f"ar://{AR_ID[:40]}",
    f"https://169.254.169.254/{AR_ID}",
])
def test_nothing_else_becomes_an_arweave_read(uri):
    assert arweave_url(uri) is None


# --------------------------------------------------------------------------- #
# 1. O sorteio: a criação n.º k do deployer                                     #
# --------------------------------------------------------------------------- #


class FakeNode:
    """Um deployer que criou contratos em certos blocos."""

    def __init__(self, creations, *, head=5000, archive=True, silent=()):
        self.creations = list(creations)           # [(bloco, endereço)], por ordem
        self.head = head
        self.archive = archive
        self.silent = set(silent)                  # criações sem o evento
        self.calls: list[str] = []

    def call(self, method, params):
        self.calls.append(method)
        if method == "eth_blockNumber":
            return hex(self.head)
        if method == "eth_getTransactionCount":
            block = int(params[1], 16)
            if not self.archive and block != self.head:
                raise RpcError(-32000, "missing trie node", revert=False)
            return hex(1 + sum(1 for b, _ in self.creations if b <= block))
        if method == "eth_getBlockByNumber":
            block = int(params[0], 16)
            txs = [{"hash": f"0xnoise{block}", "to": addr(999)}]
            txs += [{"hash": f"0xtx{i}", "to": D.upper().replace("0X", "0x")}
                    for i, (b, _) in enumerate(self.creations) if b == block]
            return {"transactions": txs}
        if method == "eth_getTransactionReceipt":
            if params[0].startswith("0xnoise"):
                return {"status": "0x1", "logs": []}
            _, made = self.creations[int(params[0][4:])]
            logs = [] if made in self.silent else [
                {"address": made, "topics": [OT, "0x" + "0" * 64, PAD]},
                {"address": made, "topics": [OT, PAD, "0x" + "0" * 24 + "ee" * 20]}]
            logs.append({"address": D, "topics": ["0x4db17dd5" + "0" * 56]})
            return {"status": "0x1", "logs": logs}
        raise AssertionError(method)


def test_every_creation_number_finds_its_own_contract():
    made = [(100, addr(1)), (250, addr(2)), (250, addr(3)), (4000, addr(4))]
    node = FakeNode(made)
    s = DeployerSampler(call=node.call, deployer=D)
    head = s.head()
    assert s.total(head) == 4
    assert [s.contract_of(k, head) for k in (1, 2, 3, 4)] == [a for _, a in made]


def test_the_search_is_a_bisection_not_a_scan():
    node = FakeNode([(123_456, addr(1))], head=50_000_000)
    s = DeployerSampler(call=node.call, deployer=D)
    assert s.contract_of(1, s.head()) == addr(1)
    assert node.calls.count("eth_getTransactionCount") <= 30


def test_a_creation_without_the_event_is_no_contract_not_a_guess():
    node = FakeNode([(100, addr(1))], silent={addr(1)})
    s = DeployerSampler(call=node.call, deployer=D)
    assert s.contract_of(1, s.head()) is None


def test_a_node_without_old_state_is_blind_not_empty():
    """R8: sem arquivo a bissecção não existe — não se mede às cegas."""
    s = DeployerSampler(call=FakeNode([(100, addr(1))], archive=False).call, deployer=D)
    with pytest.raises(ProbeBlind) as e:
        s.total(s.head())
    assert "arquivo" in str(e.value)


def test_a_deployer_without_creations_is_blind():
    s = DeployerSampler(call=FakeNode([]).call, deployer=D)
    with pytest.raises(ProbeBlind):
        s.total(s.head())


# --------------------------------------------------------------------------- #
# 2. A sonda                                                                    #
# --------------------------------------------------------------------------- #


class _Revert(Exception):
    revert = True


class Chain:
    """Contratos de mentira: {endereço: {"erc721": bool, "tokens": {id: uri}}}
    e a metadata por URI."""

    def __init__(self, contracts, metadata=None, arweave=None):
        self.contracts = contracts
        self.metadata = metadata or {}
        self.arweave = arweave or {}
        self.exists_calls = 0

    def eth_call(self, contract, data):
        c = self.contracts[contract]
        if c.get("erc721") == "revert":
            raise _Revert()
        return "0x" + "0" * 63 + ("1" if c.get("erc721", True) else "0")

    def token_uri(self, contract, tid):
        self.exists_calls += 1
        return self.contracts[contract]["tokens"].get(tid)

    def read_token(self, contract, tid):
        uri = self.contracts[contract]["tokens"].get(tid)
        if uri is None:
            return None
        meta = self.metadata.get(uri)
        if isinstance(meta, BaseException):
            raise meta
        return TokenRead(token_uri=uri, metadata=meta if uri_is_content_addressed(uri) else None)

    def arweave_json(self, url):
        got = self.arweave.get(url)
        if isinstance(got, BaseException):
            raise got
        return got


class _Sampler:
    def __init__(self, contracts, universe=12_262):
        self.queue = list(contracts)
        self.universe = universe

    def head(self):
        return 1000

    def total(self, head):
        return self.universe

    def contract_of(self, k, head):
        got = self.queue.pop(0)
        if isinstance(got, BaseException):
            raise got
        return got


def _accepts(u):
    return uri_is_content_addressed(u) or arweave_url(u) is not None


def _probe(chain, order, **world_kw):
    world = World(**world_kw)
    return ContractProbe(
        source="manifold", chain="base", sampler=_Sampler(order),
        eth_call=chain.eth_call, token_uri=chain.token_uri,
        read_token=chain.read_token, arweave_json=chain.arweave_json,
        finder=_finder(world, accepts_uri=_accepts), rng=random.Random(3))


def _one(uri, meta=None, arweave=None, **world_kw):
    c = addr(1)
    chain = Chain({c: {"tokens": {1: uri}}}, metadata={uri: meta} if meta else None,
                  arweave=arweave)
    return _probe(chain, [c], **world_kw).run(1)


GOOD = {"name": "Grease Pencil Gospel", "image": AR_IMG}


def test_an_arweave_piece_fails_today_and_passes_if_arweave_were_accepted():
    rep = _one(AR_META, arweave={AR_META: GOOD})
    assert rep.tested == 1 and rep.uri_kinds == {"arweave": 1}
    assert rep.today.passed == 0
    assert rep.today.causes == {("tokenURI fora de IPFS", "arweave"): 1}
    assert rep.with_arweave.passed == 1 and not rep.with_arweave.causes


def test_an_ipfs_piece_reads_the_same_in_both_columns():
    rep = _one(IPFS_META, meta={"name": "Grease Pencil Gospel", "image": IPFS_IMG})
    assert rep.today.passed == 1 and rep.with_arweave.passed == 1
    assert rep.uri_kinds == {"ipfs": 1}


def test_an_ipfs_piece_with_an_arweave_image_is_stopped_today_by_the_image():
    rep = _one(IPFS_META, meta={"name": "Grease Pencil Gospel", "image": AR_IMG})
    assert rep.today.causes == {("imagem fora de IPFS", "arweave"): 1}
    assert rep.with_arweave.passed == 1


def test_a_plain_http_uri_is_out_in_both_columns():
    rep = _one("https://example.com/1.json")
    cause = {("tokenURI fora de IPFS", "http"): 1}
    assert rep.today.causes == cause and rep.with_arweave.causes == cause


def test_our_failed_arweave_read_is_ours_in_the_arweave_column():
    rep = _one(AR_META, arweave={AR_META: GatewayUnavailable("arweave: TimeoutError")})
    assert rep.today.causes == {("tokenURI fora de IPFS", "arweave"): 1}
    assert rep.with_arweave.causes == {("indisponível-NOSSO", "gateway"): 1}


def test_a_dead_arweave_pin_is_theirs():
    rep = _one(AR_META, arweave={AR_META: MetadataPinGone("arweave.net said 404/410")})
    assert rep.with_arweave.causes == {("defeito-DELES", "pin-morto"): 1}


@pytest.mark.parametrize("meta, cause", [
    ({"name": "Thing #12", "image": AR_IMG}, ("numerados", None)),
    ({"image": AR_IMG}, ("sem nome", None)),
    ({"name": "Grease Pencil Gospel", "image": "https://example.com/a.png"},
     ("imagem fora de IPFS", "http")),
])
def test_the_harvest_s_filters_run_first(meta, cause):
    rep = _one(AR_META, arweave={AR_META: meta})
    assert rep.with_arweave.causes == {cause: 1}
    assert rep.today.causes == {("tokenURI fora de IPFS", "arweave"): 1}


@pytest.mark.parametrize("world_kw, group", [
    ({"eoa": False}, "dono"), ({"unique": False}, "único"),
    ({"dead": {AR_IMG}}, "imagem"),
])
def test_the_five_checks_are_the_deposit_s_own(world_kw, group):
    rep = _one(AR_META, arweave={AR_META: GOOD}, **world_kw)
    assert rep.with_arweave.passed == 0
    assert [g for g, _ in rep.with_arweave.causes] == [group]


def test_what_is_not_a_piece_is_counted_not_tested():
    chain = Chain({
        addr(1): {"erc721": False, "tokens": {}},            # an ERC-1155
        addr(2): {"erc721": "revert", "tokens": {}},         # no ERC-165 at all
        addr(3): {"tokens": {}},                             # a creator contract, empty
        addr(4): {"tokens": {1: AR_META}},
    }, arweave={AR_META: GOOD})
    order = [addr(1), addr(2), addr(3), None, TimeoutError("rpc"), addr(4)]
    rep = _probe(chain, order).run(6)
    assert (rep.asked, rep.not_erc721, rep.empty, rep.no_event) == (6, 2, 1, 1)
    assert rep.lost == {"TimeoutError": 1}
    assert rep.tested == 1 and rep.with_arweave.passed == 1


@pytest.mark.parametrize("n_tokens", [1, 2, 3, 37, 64, 65, 1000])
def test_the_last_token_is_found_by_doubling_then_bisection(n_tokens):
    c = addr(1)
    chain = Chain({c: {"tokens": {i: AR_META for i in range(1, n_tokens + 1)}}})
    p = _probe(chain, [c])
    assert p._last_token(c) == n_tokens                             # noqa: SLF001
    assert chain.exists_calls <= 2 * n_tokens.bit_length() + 3


def test_the_piece_is_drawn_inside_the_contract_not_always_the_first():
    c = addr(1)
    tokens = {i: AR_META for i in range(1, 41)}
    seen = []
    chain = Chain({c: {"tokens": tokens}}, arweave={AR_META: GOOD})
    inner = chain.read_token

    def read(contract, tid):
        seen.append(tid)
        return inner(contract, tid)
    chain.read_token = read
    _probe(chain, [c] * 12).run(12)
    assert len(set(seen)) > 3 and all(1 <= t <= 40 for t in seen)


def test_the_report_is_counts_and_causes_never_a_contract_or_a_name():
    chain = Chain({addr(1): {"tokens": {1: AR_META}}, addr(2): {"erc721": False, "tokens": {}}},
                  arweave={AR_META: GOOD})
    out = _probe(chain, [addr(1), addr(2)], unique=False).run(2).render()
    assert "manifold/base: 2 contrato(s) sorteado(s) de 12262 criações" in out
    assert "não-ERC-721 1" in out and "1 peça(s) testada(s)" in out
    assert "tokenURI: arweave 1" in out
    assert "como hoje: passariam 0 de 1 — tokenURI fora de IPFS 1 (arweave 1)" in out
    assert "com Arweave: passariam 0 de 1 — único 1" in out
    for leak in ("0x", "Grease", "Pencil", AR_ID, "arweave.net/", "ipfs://"):
        assert leak not in out, leak


def test_cause_of_reads_one_candidate_s_tally():
    t = Tally()
    t.owner += 1
    t.owner_kinds["contrato:reverte-mudo"] = 1
    assert cause_of(t) == ("dono", "contrato:reverte-mudo")
    assert cause_of(Tally()) == ("outro", None)


# --------------------------------------------------------------------------- #
# 3. O leitor de Arweave (só medição)                                           #
# --------------------------------------------------------------------------- #


def _http_error(code):
    return urllib.error.HTTPError("u", code, "x", {}, None)


def test_arweave_metadata_is_read_and_counted():
    gw = ArweaveGateway(http_get=lambda u, h: json.dumps(GOOD))
    assert gw.metadata(AR_META)["name"] == "Grease Pencil Gospel"
    assert gw.tally.stats["outcomes"] == {1: {"serviu": 1}}


@pytest.mark.parametrize("answer, exc, outcome", [
    (_http_error(404), MetadataPinGone, "404"),
    (_http_error(503), GatewayUnavailable, "503"),
    (TimeoutError(), GatewayUnavailable, "timeout"),
])
def test_arweave_failures_get_the_larder_s_verdicts(answer, exc, outcome):
    def get(u, h):
        raise answer
    gw = ArweaveGateway(http_get=get)
    with pytest.raises(exc):
        gw.metadata(AR_META)
    assert gw.tally.stats["outcomes"] == {1: {outcome: 1}}


def test_arweave_reads_no_other_host():
    asked = []
    gw = ArweaveGateway(http_get=lambda u, h: asked.append(u) or "{}")
    with pytest.raises(MetadataInvalid):
        gw.metadata("https://example.com/x.json")
    assert asked == []


# --------------------------------------------------------------------------- #
# 4. A composição: o finder da despensa não muda; a sonda não guarda nada       #
# --------------------------------------------------------------------------- #


def _built(*, get=None, ranged=None, post=None, **over):
    from test_target_wiring import rpc_ok
    return build_target(settings(**over), anthropic=object(), repo=FakeRepo(),
                        http_get=get or (lambda u, h: "{}"),
                        http_post=post or rpc_ok,
                        http_get_bytes=lambda u, h: b"",
                        http_get_range=ranged or (lambda u, h: (PNG, 4096)))


def test_the_larder_still_refuses_arweave_only_the_probe_reads_it():
    w = _built()
    assert not w.finder._accepts(AR_IMG)                           # noqa: SLF001
    probe_finder = w.probes["manifold"]._finder                    # noqa: SLF001
    assert probe_finder is not w.finder
    assert probe_finder._accepts(AR_IMG) and probe_finder._accepts(IPFS_IMG)   # noqa: SLF001
    assert not probe_finder._accepts("https://example.com/a.png")  # noqa: SLF001


def test_the_arweave_image_is_read_from_arweave_net_and_counted_apart():
    seen = []

    def ranged(url, headers):
        seen.append(url)
        return (PNG, 4096)
    w = _built(ranged=ranged)
    head, size = w.probes["manifold"]._finder._probe_image(AR_IMG)  # noqa: SLF001
    assert head == PNG and size == 4096
    assert seen == ["https://arweave.net/" + "Z" * 43]
    assert w.arweave_image.stats["outcomes"] == {1: {"serviu": 1}}
    assert w.larder_image.stats["outcomes"] == {}       # not the dedicated gateway's quota


@pytest.mark.parametrize("answer, exc", [(404, ImagePinGone), (503, ImageGatewaysDown),
                                         (TimeoutError(), ImageGatewaysDown)])
def test_the_arweave_image_gets_the_larder_s_verdicts(answer, exc):
    def ranged(url, headers):
        raise _http_error(answer) if isinstance(answer, int) else answer
    w = _built(ranged=ranged)
    with pytest.raises(exc):
        w.probes["manifold"]._finder._probe_image(AR_IMG)           # noqa: SLF001


def test_an_ipfs_image_in_the_probe_goes_through_the_larder_s_own_test():
    seen = []

    def ranged(url, headers):
        seen.append(url.split("/")[2])
        return (PNG, 4096)
    w = _built(ranged=ranged)
    w.probes["manifold"]._finder._probe_image(IPFS_IMG)             # noqa: SLF001
    assert seen == ["gateway.pinata.cloud"]
    assert w.arweave_image.stats["outcomes"] == {}


def test_without_a_base_rpc_there_is_no_probe():
    w = _built(base_rpc_url="", target_public_rpcs_base="")
    assert w.probes == {}
    assert "não configurada" in w.probe("manifold", 5)


class _NoLarder:
    def __getattr__(self, name):
        raise AssertionError(f"the probe touched the larder ({name})")


def _wiring_with(probe, **kw):
    return TargetWiring(
        ports=None, pipeline=None, epoch=None, snapshot_store=None,
        scan_blocks=0, writability_rates={}, uniqueness_rates={},
        finder=_NoLarder(), larder_store=_NoLarder(),
        probes={"manifold": probe}, **kw)


def test_the_probe_never_reads_or_writes_the_larder():
    chain = Chain({addr(1): {"tokens": {1: AR_META}}}, arweave={AR_META: GOOD})
    out = _wiring_with(_probe(chain, [addr(1)])).probe("manifold", 1)
    assert out.startswith("probe (só medição, nada guardado):")
    assert "com Arweave: passariam 1 de 1" in out


def test_a_blind_probe_and_a_dead_rpc_say_so_and_conclude_nothing():
    class _Blind:
        def run(self, n, **kw):
            raise ProbeBlind("o RPC não lê estado antigo (RpcError) — precisa de arquivo")

    class _Dead:
        def run(self, n, **kw):
            raise ConnectionError("https://base.example/v2/SECRET-KEY refused")
    assert "⛔" in _wiring_with(_Blind()).probe("manifold", 5)
    out = _wiring_with(_Dead()).probe("manifold", 5)
    assert "NÃO MEDIDO" in out and "ConnectionError" in out
    assert "SECRET-KEY" not in out and "base.example" not in out


def test_the_report_says_what_arweave_net_answered_and_what_the_dedicated_spent():
    meta, image = GatewayTally(), GatewayTally()

    class _Arweave:
        tally = meta

    class _Reader:
        stats = {"rescued": 0, "outcomes": {}}

    larder_image = GatewayTally()

    class _P:
        def run(self, n, **kw):
            meta.note(1, "serviu")
            meta.note(1, "timeout")
            image.note(1, "serviu")
            larder_image.note(1, "serviu")
            return ProbeReport(source="manifold", chain="base", asked=1, universe=9)
    out = _wiring_with(_P(), arweave_meta=_Arweave(), arweave_image=image,
                       larder_meta=_Reader(), larder_image=larder_image,
                       dedicated_gateway=True).probe("manifold", 1)
    assert "arweave.net — metadata: serviu 1, timeout 1; imagem: serviu 1" in out
    assert "gateway dedicado: 1 pedido(s) nesta corrida (metadata 0, imagem 1)" in out


def test_main_routes_the_command_through_the_hunt_guard():
    import inspect

    from finding_memeland import main
    from finding_memeland.telegram.approval_queue import TELEGRAM_COMMANDS
    src = inspect.getsource(main.build_agent)
    assert '_target_job("probe", lambda: target_wiring.probe(source, n))' in src
    assert '"probe": _probe' in src and "probe" in TELEGRAM_COMMANDS


# --------------------------------------------------------------------------- #
# 5. De ponta a ponta, pela composição real                                     #
# --------------------------------------------------------------------------- #


def test_end_to_end_one_arweave_piece_reaches_the_last_check():
    """Um contrato de criador, uma peça em Arweave: sorteio → contrato →
    peça → metadata e imagem de arweave.net → dono → único. O mercado não
    devolve a peça (índice cego) — a ÚLTIMA verificação, o que prova que as
    outras quatro correram por cima de Arweave."""
    contract, owner = addr(0xC0), addr(0xAA)

    def post(url, body, headers):
        req = json.loads(body)
        m, p = req["method"], req["params"]

        def ok(result):
            return json.dumps({"jsonrpc": "2.0", "id": req["id"], "result": result})

        def revert():
            return json.dumps({"jsonrpc": "2.0", "id": req["id"],
                               "error": {"code": 3, "message": "execution reverted"}})
        if m == "eth_blockNumber":
            return ok(hex(1000))
        if m == "eth_getTransactionCount":
            return ok(hex(2 if int(p[1], 16) >= 500 else 1))
        if m == "eth_getBlockByNumber":
            return ok({"transactions": [{"hash": "0xtx0", "to": D}]})
        if m == "eth_getTransactionReceipt":
            return ok({"status": "0x1", "logs": [
                {"address": contract, "topics": [OT, "0x" + "0" * 64, PAD]}]})
        if m == "eth_getCode":
            return ok("0x" if p[0].lower() == owner else "0x6080")
        if m == "eth_call":
            data = p[0]["data"]
            if p[0]["to"].lower() != contract:
                return revert()
            if data.startswith("0x01ffc9a7"):
                return ok("0x" + "0" * 63 + "1")
            if data.startswith("0xc87b56dd"):                   # tokenURI
                return ok(_abi_string(AR_META)) if int(data[10:], 16) == 1 else revert()
            if data.startswith("0x6352211e"):                   # ownerOf
                return ok("0x" + "0" * 24 + owner[2:])
            return revert()
        return ok([])

    asked = []

    def get(url, headers):
        asked.append(url)
        if url.startswith("https://arweave.net/"):
            return json.dumps(GOOD)
        if "/search?" in url:
            return json.dumps({"results": []})
        raise _http_error(404)                                   # the item lookup: unknown

    w = _built(get=get, post=post, opensea_api_key="ok")
    out = w.probe("manifold", 1)
    assert "1 contrato(s) sorteado(s) de 1 criações" in out, out
    assert "1 peça(s) testada(s)" in out and "tokenURI: arweave 1" in out, out
    assert "como hoje: passariam 0 de 1 — tokenURI fora de IPFS 1 (arweave 1)" in out, out
    assert "com Arweave: passariam 0 de 1 — único 1 (índice-cego:não-indexada 1)" in out, out
    assert "arweave.net — metadata: serviu 1; imagem: serviu 1" in out, out
    assert contract[2:] not in out and owner[2:] not in out and "Grease" not in out
    assert all(u.startswith(("https://arweave.net/", "https://api.opensea.io/")) for u in asked)
