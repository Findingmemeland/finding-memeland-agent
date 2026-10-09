"""O gateway dedicado da despensa (30/09, plano B do Pedro).

O TARGET_IPFS_GATEWAY era o gateway PÚBLICO da Pinata — daí os 52 × 429 em
Base. O Pedro tem um gateway DEDICADO (plano Free: 10 mil pedidos/mês, o
limite prático) que só serve CIDs de outros com uma Gateway Key no header
x-pinata-gateway-token. O que isto fixa:
  · com os DOIS valores novos no Doppler, a despensa (/harvest, /fill,
    /prepare) lê primeiro pelo dedicado e usa o público como reserva;
  · sem eles — ou com só um — tudo exactamente como antes;
  · a chave vai só para o host do dedicado: nunca para o público, nunca num
    redirect para outro host (o urllib reenviava-a — medido), nunca em
    relatórios, /status, repr, excepções;
  · o live check, o live hash e o reveal não mudam;
  · o /harvest diz quantos pedidos cada corrida gastou no dedicado.
"""
from __future__ import annotations

import http.server
import json
import threading
import urllib.request

import pytest
from test_gateway_outcomes import _Reader
from test_harvest_command import _Harv, _Store
from test_image_verdicts import PNG, URI
from test_metadata_failover import DOC, _abi_string
from test_target_prepare import World, _finder
from test_target_wiring import FakeRepo, settings

from finding_memeland import main
from finding_memeland.target.adapters import GATEWAY_KEY_HEADER, GatewayTally
from finding_memeland.target.sources import ChainUnavailable
from finding_memeland.target.wiring import TargetWiring, build_target

KEY = "KEY-SECRET-XYZ-123"
DEDICATED = "https://dedicado.example/ipfs/"
A = "0x" + "a1" * 20
CID = "bafkre" + "a" * 50


def _with_dedicated(**over):
    return settings(target_ipfs_dedicated_gateway=DEDICATED,
                    target_ipfs_dedicated_key=KEY, harvest_canary_ethereum="100:1",
                    **over)


def _rpc(url, body, headers):
    req = json.loads(body)
    if req["method"] == "eth_call":
        return json.dumps({"jsonrpc": "2.0", "id": 1,
                           "result": _abi_string(f"ipfs://{CID}")})
    if req["method"] == "eth_getCode":
        return json.dumps({"jsonrpc": "2.0", "id": 1, "result": "0x6001"})
    return json.dumps({"jsonrpc": "2.0", "id": 1, "result": []})


class Net:
    """Regista cada pedido (host, headers) e responde por host."""

    def __init__(self, **by_host):
        self.by_host = by_host
        self.seen: list[tuple[str, dict]] = []

    def _answer(self, url, headers):
        host = url.split("/")[2]
        self.seen.append((host, dict(headers or {})))
        got = self.by_host.get(host, TimeoutError("no answer"))
        if isinstance(got, BaseException):
            raise got
        return got

    def get(self, url, headers):
        return self._answer(url, headers)

    def ranged(self, url, headers):
        return (self._answer(url, headers), 4096)

    def keyed_hosts(self):
        return {h for h, hd in self.seen
                if any(k.lower() == GATEWAY_KEY_HEADER for k in hd)}


def _build(s, net: Net):
    return build_target(s, anthropic=object(), repo=FakeRepo(),
                        http_get=net.get, http_get_fallback=net.get,
                        http_post=_rpc, http_get_bytes=lambda u, h: b"",
                        http_get_range=net.ranged, http_get_larder_art=net.get)


# --------------------------------------------------------------------------- #
# 1. A configuração: os dois valores, ou é como antes                           #
# --------------------------------------------------------------------------- #


def test_both_values_turn_it_on():
    s = _with_dedicated()
    assert s.target_ipfs_dedicated == (DEDICATED, KEY)
    assert s.target_ipfs_dedicated_state == "activo"


def test_neither_value_is_exactly_as_before():
    s = settings()
    assert s.target_ipfs_dedicated is None
    assert s.target_ipfs_dedicated_state == "não configurado"


@pytest.mark.parametrize("over, missing", [
    ({"target_ipfs_dedicated_gateway": DEDICATED}, "TARGET_IPFS_DEDICATED_KEY"),
    ({"target_ipfs_dedicated_key": KEY}, "TARGET_IPFS_DEDICATED_GATEWAY"),
])
def test_one_value_alone_is_ignored_and_named(over, missing):
    """Nunca recusar o arranque por isto: um redeploy que não arranca a meio
    de uma hunt partia tudo o resto."""
    s = settings(**over)
    assert s.target_ipfs_dedicated is None
    assert missing in s.target_ipfs_dedicated_state
    assert KEY not in s.target_ipfs_dedicated_state


@pytest.mark.parametrize("url", [
    "https://nome.mypinata.cloud",                     # no /ipfs/: 404 for every CID
    "https://nome.mypinata.cloud/",
    "http://nome.mypinata.cloud/ipfs/",                # the key would travel in clear
    "https://nome.mypinata.cloud/ipfs/?pinataGatewayToken=K",   # key in the URL
    "nome.mypinata.cloud/ipfs/",
])
def test_a_malformed_url_switches_it_off(url):
    """Um URL sem /ipfs/ dá 404 a tudo — e um 404 é pin morto pela regra
    de 29/09: o NOSSO erro gastaria alvos no /prepare."""
    s = settings(target_ipfs_dedicated_gateway=url, target_ipfs_dedicated_key=KEY)
    assert s.target_ipfs_dedicated is None
    assert "URL inválido" in s.target_ipfs_dedicated_state
    assert KEY not in s.target_ipfs_dedicated_state and "nome" not in s.target_ipfs_dedicated_state


@pytest.mark.parametrize("url", ["https://nome.mypinata.cloud/ipfs/",
                                 "https://nome.mypinata.cloud/ipfs"])
def test_the_gateway_shape_is_accepted(url):
    s = settings(target_ipfs_dedicated_gateway=url, target_ipfs_dedicated_key=KEY)
    assert s.target_ipfs_dedicated == (url, KEY)


def test_the_key_never_shows_in_the_settings():
    s = _with_dedicated()
    for text in (repr(s), str(s), str(s.model_dump()), s.model_dump_json()):
        assert KEY not in text


# --------------------------------------------------------------------------- #
# 2. Sem os valores: exactamente como hoje                                      #
# --------------------------------------------------------------------------- #


def test_without_it_the_larder_reads_the_public_gateways_as_before():
    net = Net(**{"gateway.pinata.cloud": DOC})
    w = _build(settings(harvest_canary_ethereum="100:1"), net)
    assert w.larder_meta._gateways == (                           # noqa: SLF001
        "https://gateway.pinata.cloud/ipfs/", "https://gw0/ipfs/", "https://gw1/ipfs/")
    assert not w.dedicated_gateway
    w.harvesters["ethereum"]._read_meta(A, 1)                      # noqa: SLF001
    assert net.seen and not net.keyed_hosts()


# --------------------------------------------------------------------------- #
# 3. Com os valores: o dedicado primeiro, a chave só para ele                   #
# --------------------------------------------------------------------------- #


def test_the_metadata_goes_to_the_dedicated_first_with_its_key():
    net = Net(**{"dedicado.example": DOC})
    w = _build(_with_dedicated(), net)
    w.harvesters["ethereum"]._read_meta(A, 1)                      # noqa: SLF001
    assert net.seen[0][0] == "dedicado.example"
    assert net.seen[0][1][GATEWAY_KEY_HEADER] == KEY
    assert w.larder_meta.gateway_count == 4


def test_the_public_fallback_never_sees_the_key():
    net = Net(**{"dedicado.example": TimeoutError(), "gateway.pinata.cloud": DOC})
    w = _build(_with_dedicated(), net)
    w.harvesters["ethereum"]._read_meta(A, 1)                      # noqa: SLF001
    assert [h for h, _ in net.seen] == ["dedicado.example", "gateway.pinata.cloud"]
    assert net.keyed_hosts() == {"dedicado.example"}
    assert w.larder_meta.stats["rescued"] == 1


def test_the_image_test_and_the_artwork_carry_the_key_to_the_dedicated_only():
    net = Net(**{"dedicado.example": TimeoutError(), "gateway.pinata.cloud": PNG})
    w = _build(_with_dedicated(), net)
    w.finder._probe_image(URI)                                     # noqa: SLF001
    with pytest.raises(ChainUnavailable):
        w.larder_preparer._fetch_image(URI)     # PNG magic that won't open: ours
    assert net.keyed_hosts() == {"dedicado.example"}
    assert sum(1 for h, _ in net.seen if h == "dedicado.example") == 2


def test_the_hunt_time_paths_never_touch_the_dedicated():
    """Live check: só RPC. Live hash (fim da hunt): o público, como antes.
    Os provedores do live check continuam os de TARGET_IPFS_GATEWAYS."""
    from finding_memeland.target.hunt import SealedTarget
    from finding_memeland.target.selector import Target
    net = Net(**{"gateway.pinata.cloud": DOC})
    w = _build(_with_dedicated(), net)
    gen = w.ports.live_check._generic                              # noqa: SLF001
    assert [p.gateway for p in gen._providers] == ["https://gw0/ipfs/", "https://gw1/ipfs/"]  # noqa: SLF001
    t = Target(chain="ethereum", contract=A, token_id=1, name="x", name_onchain="x",
               description="", image=URI, metadata_sha256="m", epoch="e1",
               token_uri=f"ipfs://{CID}", content_id=f"ipfs:{CID}")
    w.ports.live_hash(SealedTarget(target=t, salt="s" * 32, commitment="c" * 64))
    assert net.seen and all(h == "gateway.pinata.cloud" for h, _ in net.seen)
    assert not net.keyed_hosts()


def test_the_key_never_shows_in_an_error_or_the_wiring():
    net = Net()                                         # every gateway times out
    w = _build(_with_dedicated(), net)
    with pytest.raises(ChainUnavailable) as e:
        w.harvesters["ethereum"]._read_meta(A, 1)                  # noqa: SLF001
    with pytest.raises(ChainUnavailable) as e2:
        w.finder._probe_image(URI)                                 # noqa: SLF001
    for text in (str(e.value), repr(e.value), str(e2.value), repr(w),
                 str(w._meta_snapshot())):                         # noqa: SLF001
        assert KEY not in text and "dedicado.example" not in text


# --------------------------------------------------------------------------- #
# 4. O relatório: "dedicado" e os pedidos gastos                                #
# --------------------------------------------------------------------------- #


def _run(*, dedicated: bool):
    meta, image = _Reader(), GatewayTally()
    meta.gateway_count = 2

    class _H(_Harv):
        def harvest(self, n, **kw):
            meta.note(1, "serviu", 120)
            meta.note(1, "timeout", 5)
            meta.note(2, "serviu", 5)
            return super().harvest(n, **kw)

    world = World()
    inner = world.probe_image

    def probe(url):
        image.note(1, "serviu")
        return inner(url)
    world.probe_image = probe
    tw = TargetWiring(
        ports=None, pipeline=None, epoch=None, snapshot_store=None,
        scan_blocks=0, writability_rates={}, uniqueness_rates={},
        finder=_finder(world), larder_store=_Store(),
        harvesters={"ethereum": _H([f"ethereum:{A}:1"])},
        deposit_chains=frozenset({"ethereum"}),
        larder_meta=meta, larder_image=image, dedicated_gateway=dedicated)
    return tw.harvest(200)


def test_the_report_names_the_dedicated_and_counts_what_the_run_spent():
    out = _run(dedicated=True)
    assert "dedicado — colheita: serviu 120, timeout 5; depósito (imagem): serviu 1" in out, out
    assert "gateway 2 — colheita: serviu 5" in out, out
    assert out.rstrip().endswith(
        "gateway dedicado: 126 pedido(s) nesta corrida (metadata 125, imagem 1)"), out


def test_without_the_dedicated_the_report_is_as_before():
    out = _run(dedicated=False)
    assert "dedicado" not in out and "gateway 1 — colheita" in out, out


def test_status_shows_the_state_never_a_value():
    src = open(main.__file__, encoding="utf-8").read()
    assert 'f"gateway dedicado: {s.target_ipfs_dedicated_state}"' in src


# --------------------------------------------------------------------------- #
# 5. O transporte: a chave não sai do host dela num redirect                    #
# --------------------------------------------------------------------------- #


class _Hop(http.server.BaseHTTPRequestHandler):
    to = ""
    got: list = []

    def log_message(self, *a):
        pass

    def do_GET(self):
        type(self).got.append((self.server.server_port, self.path,
                               self.headers.get(GATEWAY_KEY_HEADER)))
        if self.path == "/hop":
            self.send_response(302)
            self.send_header("Location", type(self).to)
            self.end_headers()
            return
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"{}")


@pytest.fixture
def two_hosts():
    _Hop.got = []
    a = http.server.ThreadingHTTPServer(("127.0.0.1", 0), _Hop)
    b = http.server.ThreadingHTTPServer(("127.0.0.1", 0), _Hop)
    for srv in (a, b):
        threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield a.server_port, b.server_port
    a.shutdown()
    b.shutdown()


def test_a_redirect_to_another_host_drops_the_key(two_hosts, monkeypatch):
    pa, pb = two_hosts
    # the transport only lets the key leave for the dedicated gateway's host
    # (09/10, tests/test_arweave.py) — here, the first test server
    monkeypatch.setattr(main, "_KEY_HOME", ("http", f"127.0.0.1:{pa}"))
    _Hop.to = f"http://127.0.0.1:{pb}/landed"
    main._http_get(f"http://127.0.0.1:{pa}/hop", {GATEWAY_KEY_HEADER: KEY})  # noqa: SLF001
    assert (pa, "/hop", KEY) in _Hop.got
    assert (pb, "/landed", None) in _Hop.got           # arrived WITHOUT the key


def test_a_redirect_on_the_same_host_keeps_the_key(two_hosts, monkeypatch):
    pa, _ = two_hosts
    monkeypatch.setattr(main, "_KEY_HOME", ("http", f"127.0.0.1:{pa}"))
    _Hop.to = "/landed"
    main._http_get(f"http://127.0.0.1:{pa}/hop", {GATEWAY_KEY_HEADER: KEY})  # noqa: SLF001
    assert (pa, "/landed", KEY) in _Hop.got


def test_a_downgrade_to_plain_http_drops_the_key():
    h = main._GatewayKeyStaysHome()                                # noqa: SLF001
    req = urllib.request.Request("https://dedicado.example/ipfs/x",
                                 headers={GATEWAY_KEY_HEADER: KEY})
    new = h.redirect_request(req, None, 302, "Found", {}, "http://dedicado.example/ipfs/x")
    assert not any(k.lower() == GATEWAY_KEY_HEADER for k in new.headers)
