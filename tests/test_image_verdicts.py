"""A imagem segue a regra da metadata (Pedro, 29/09).

Só se culpa o candidato — e só o /prepare gasta um alvo — quando um gateway
responde SOBRE O CONTEÚDO:
  · 404/410                                   → pin morto (DELES)
  · bytes que claramente não são imagem fixa  → vídeo, PDF, SVG (DELES)
Timeouts, 5xx, 429, corpo vazio, uma página HTML, uma arte que não abre:
NOSSO, e o alvo fica na despensa.

Até 29/09 era ao contrário em dois sítios: uma página HTML no meio de
timeouts dava "pin morto" (e o /prepare gastava o alvo), um 404 em todos os
gateways guardava-o; e a leitura completa da arte no /prepare, com timeouts
em todos os gateways, devolvia None — e o alvo era gasto.
"""
from __future__ import annotations

import io
import re
import urllib.error

import pytest
from test_target_prepare import World, _finder, _preparer
from test_target_wiring import FakeRepo, rpc_ok, settings
from test_unavailable_split import _deposit

from finding_memeland.target import wiring
from finding_memeland.target.prepare import Larder, PrepareRefused, ReadUnavailable, Source, Tally
from finding_memeland.target.sources import (
    ArtworkUnreadable,
    ChainUnavailable,
    ImageGatewaysDown,
    ImagePinGone,
    ImageUnusable,
)
from finding_memeland.target.wiring import build_target

URI = "ipfs://QmSiuJazyPgzAVqBiW3LMNdjAG4uaZFqzMwzU2kGS2KmCN"
PNG = b"\x89PNG\r\n\x1a\n" + b"x" * 64
MP4 = b"\x00\x00\x00\x18ftypmp42" + b"\x00" * 64
SVG = b'<?xml version="1.0"?><svg xmlns="http://www.w3.org/2000/svg"></svg>'
XML_ERROR = b'<?xml version="1.0"?><Error><Code>SlowDown</Code></Error>'
HTML = b"<html><body>Too Many Requests</body></html>"
A = "0x" + "aa" * 20


def _by_host(**hosts):
    """Um transporte em que cada gateway responde o que lhe mandarem.
    Hosts do settings(): gateway.pinata.cloud (o nosso), gw0, gw1."""
    def get(url, headers):
        host = url.split("/")[2]
        got = hosts.get(host, TimeoutError("no answer"))
        if isinstance(got, int):
            raise urllib.error.HTTPError(url, got, "x", {}, None)
        if isinstance(got, BaseException):
            raise got
        return got
    return get


def _probe(**hosts):
    get = _by_host(**hosts)

    def ranged(url, headers):
        data = get(url, headers)
        return (data, 4096)
    w = build_target(settings(), anthropic=object(), repo=FakeRepo(),
                     http_get=lambda u, h: "{}", http_post=rpc_ok,
                     http_get_bytes=lambda u, h: b"", http_get_range=ranged)
    return w.finder._probe_image(URI)                              # noqa: SLF001


def _fetch(**hosts):
    w = build_target(settings(), anthropic=object(), repo=FakeRepo(),
                     http_get=lambda u, h: "{}", http_post=rpc_ok,
                     http_get_bytes=lambda u, h: b"",
                     get_artwork_bytes=_by_host(**hosts))
    return w.larder_preparer._fetch_image(URI)                     # noqa: SLF001


# --------------------------------------------------------------------------- #
# 1. O teste da imagem (depósito, /fill, /prepare)                             #
# --------------------------------------------------------------------------- #


def test_an_image_on_any_gateway_passes():
    head, size = _probe(**{"gateway.pinata.cloud": 503, "gw0": PNG})
    assert head == PNG and size == 4096


@pytest.mark.parametrize("page", [HTML, b"", XML_ERROR])
def test_a_page_that_is_not_the_artwork_is_ours_not_a_dead_pin(page):
    """O defeito que isto fecha: uma página de throttle com 200, no meio de
    timeouts, dava 'pin morto' — e o /prepare gastava um alvo verificado."""
    with pytest.raises(ImageGatewaysDown):
        _probe(**{"gateway.pinata.cloud": page})


@pytest.mark.parametrize("code", [404, 410])
def test_a_clear_not_found_is_a_dead_pin(code):
    """O outro lado: um 404 claro guardava o alvo como falha nossa."""
    with pytest.raises(ImagePinGone) as e:
        _probe(**{"gateway.pinata.cloud": TimeoutError(), "gw0": code, "gw1": 503})
    assert e.value.theirs and e.value.kind == "pin-morto"


def test_a_404_is_overruled_by_a_gateway_that_has_the_image():
    head, _ = _probe(**{"gateway.pinata.cloud": 404, "gw0": PNG})
    assert head == PNG


def test_timeouts_5xx_and_429_everywhere_are_ours():
    with pytest.raises(ImageGatewaysDown) as e:
        _probe(**{"gateway.pinata.cloud": 429, "gw0": 503, "gw1": TimeoutError()})
    assert not e.value.theirs


@pytest.mark.parametrize("data, kind", [(MP4, "video/mp4"), (SVG, "svg"),
                                        (b"%PDF-1.7" + b"\x00" * 32, "pdf")])
def test_bytes_that_are_clearly_not_a_still_image_are_theirs(data, kind):
    with pytest.raises(ImageUnusable) as e:
        _probe(**{"gateway.pinata.cloud": data})
    assert e.value.theirs and e.value.kind == kind


def test_the_content_s_answer_beats_a_404():
    """Se um gateway serviu o vídeo, o conteúdo existe — o 404 de outro
    está errado, e a razão verdadeira é 'não é imagem'."""
    with pytest.raises(ImageUnusable):
        _probe(**{"gateway.pinata.cloud": 404, "gw0": MP4})


# --------------------------------------------------------------------------- #
# 2. A leitura completa da arte, no /prepare                                    #
# --------------------------------------------------------------------------- #


def test_the_full_artwork_with_every_gateway_down_is_ours_not_none():
    """O defeito maior: devolvia None, e o /prepare gastava o alvo."""
    with pytest.raises(ImageGatewaysDown):
        _fetch(**{"gateway.pinata.cloud": TimeoutError(), "gw0": 503, "gw1": 502})


def test_the_full_artwork_names_a_dead_pin():
    with pytest.raises(ImagePinGone):
        _fetch(**{"gateway.pinata.cloud": 404})


def test_the_full_artwork_that_will_not_open_is_ours():
    """Bytes com cara de PNG que não abrem: um ficheiro estragado (deles) ou
    uma leitura nossa cortada — ambíguo, portanto nosso."""
    with pytest.raises(ArtworkUnreadable) as e:
        _fetch(**{"gateway.pinata.cloud": PNG})
    assert not e.value.theirs


def test_the_full_artwork_too_big_to_download_is_theirs(monkeypatch):
    monkeypatch.setattr(wiring, "MAX_IMAGE_BYTES", 32)
    with pytest.raises(ImageUnusable) as e:
        _fetch(**{"gateway.pinata.cloud": PNG})
    assert e.value.kind == "tamanho"


def test_the_full_artwork_that_opens_is_returned():
    Image = pytest.importorskip("PIL.Image")
    buf = io.BytesIO()
    Image.new("RGB", (4, 4)).save(buf, "PNG")
    assert _fetch(**{"gateway.pinata.cloud": TimeoutError(), "gw0": buf.getvalue()})


# --------------------------------------------------------------------------- #
# 3. Os relatórios                                                              #
# --------------------------------------------------------------------------- #


def test_deposit_names_the_image_verdicts():
    assert "imagem 1 (pin-morto 1)" in _deposit(probe=ImagePinGone("x"))
    assert "imagem 1 (video/mp4 1)" in _deposit(probe=ImageUnusable("video/mp4"))
    out = _deposit(probe=ImageGatewaysDown("x"))
    assert "indisponível-NOSSO 1 (gateway-imagem 1)" in out
    assert not re.search(r"(^|[\s,·])imagem \d", out), out     # not blamed on the NFT


def test_every_image_verdict_is_still_a_chain_unavailable():
    for cls in (ImagePinGone, ImageUnusable, ArtworkUnreadable):
        assert issubclass(cls, ChainUnavailable)
    assert ImagePinGone.theirs and ImageUnusable.theirs
    assert not ArtworkUnreadable.theirs and not ImageGatewaysDown.theirs


# --------------------------------------------------------------------------- #
# 4. O /prepare: só a resposta do conteúdo gasta um alvo                        #
# --------------------------------------------------------------------------- #


def _strict_verify(exc):
    world = World()

    def probe(url):
        raise exc
    world.probe_image = probe
    finder = _finder(world)
    tally = Tally()
    got = finder.verify(Source("larder", "ethereum", A), 1,
                        world.read_token("ethereum", A, 1), "Some Two Words",
                        tally, strict=True)
    return got, tally


def test_prepare_verify_drops_on_the_content_s_answer():
    got, tally = _strict_verify(ImagePinGone("x"))
    assert got is None and tally.image_kinds == {"pin-morto": 1}


def test_prepare_verify_keeps_on_our_outage():
    with pytest.raises(ReadUnavailable):
        _strict_verify(ImageGatewaysDown("x"))


def _prepare_with(exc, said=None):
    world = World()
    finder = _finder(world)
    larder = Larder()
    finder.fill(larder, want=8, max_draws=200)
    before = larder.size()

    def fetch(url):
        raise exc
    world.fetch_image = fetch
    preparer = _preparer(world, finder, **({"notify": said.append} if said is not None else {}))
    with pytest.raises(PrepareRefused) as e:
        preparer.prepare(larder)
    return str(e.value), before, larder.size()


@pytest.mark.parametrize("exc", [ImageGatewaysDown("x"), ArtworkUnreadable("x"),
                                 TimeoutError("x")])
def test_prepare_keeps_the_larder_when_the_artwork_is_ours(exc):
    msg, before, after = _prepare_with(exc)
    assert "INTACTA" in msg
    assert after == before


@pytest.mark.parametrize("exc", [ImagePinGone("x"), ImageUnusable("video/mp4")])
def test_prepare_spends_candidates_only_on_the_content_s_answer(exc):
    msg, before, after = _prepare_with(exc)
    assert "INTACTA" not in msg
    assert after < before


def test_prepare_lines_name_causes_never_the_candidate():
    said: list[str] = []
    _prepare_with(ImagePinGone("x"), said)
    _prepare_with(ImageGatewaysDown("x"), said)
    text = "\n".join(said)
    assert "pin-morto" in text and "MANTIDO" in text
    for leak in ("Some Two Words", "abab", "ipfs://"):
        assert leak not in text, leak
