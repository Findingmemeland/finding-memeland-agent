"""SVG em IPFS passa a PNG, como o on-chain (30/09, Pedro).

O /harvest de 30/09 perdeu 4 candidatos de Base (e 2 de Ethereum antes) por
"svg": a arte estava lá, num formato que a visão não lê. O SVG on-chain é
desenhado desde 29/09 (resvg, sem rede, com limites); o de IPFS passa pelo
mesmo desenho. Duas cautelas:
  · o teste da imagem lê só os primeiros KB, e um SVG só se desenha inteiro —
    uma leitura a mais, no mesmo gateway, CONTADA (gasta quota);
  · só é SVG um documento cuja raiz é <svg>: uma página de erro HTML com um
    logótipo SVG não pode passar por arte e culpar o candidato.
"""
from __future__ import annotations

import pytest
from test_dedicated_gateway import DEDICATED, KEY, Net, _build, _with_dedicated
from test_image_verdicts import URI
from test_target_wiring import FakeRepo, rpc_ok, settings

from finding_memeland.target.adapters import GATEWAY_KEY_HEADER, sniff_media_type
from finding_memeland.target.sources import ImageGatewaysDown, ImageUnusable
from finding_memeland.target.wiring import _not_a_still_image, build_target

pytest.importorskip("resvg_py")

SVG = (b'<?xml version="1.0" encoding="UTF-8"?>\n'
       b'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 100 60">'
       b'<rect width="100" height="60" fill="#123"/>'
       b'<circle cx="50" cy="30" r="20" fill="#e84"/></svg>')
SVG_EXTERNAL = (b'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 10 10">'
                b'<image href="https://elsewhere.example/a.png" width="10" height="10"/></svg>')
HTML_WITH_LOGO = (b'<!DOCTYPE html><html><body><svg viewBox="0 0 10 10">'
                  b'<rect width="10" height="10"/></svg> 429 Too Many Requests</body></html>')
XHTML_WITH_LOGO = (b'<?xml version="1.0"?><!DOCTYPE html><html><svg viewBox="0 0 1 1">'
                   b'</svg></html>')
SVG_WITH_DOCTYPE = (b'<?xml version="1.0"?><!-- drawn by hand -->'
                    b'<!DOCTYPE svg PUBLIC "-//W3C//DTD SVG 1.1//EN" '
                    b'"http://www.w3.org/Graphics/SVG/1.1/DTD/svg11.dtd">' + SVG[39:])


def _wired(**hosts):
    net = Net(**hosts)
    w = build_target(settings(), anthropic=object(), repo=FakeRepo(),
                     http_get=net.get, http_post=rpc_ok, http_get_bytes=lambda u, h: b"",
                     http_get_range=net.ranged, http_get_larder_art=net.get)
    return w, net


# --------------------------------------------------------------------------- #
# 1. O que é um SVG                                                             #
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("data, kind", [
    (SVG, "svg"), (SVG_WITH_DOCTYPE, "svg"), (SVG_EXTERNAL, "svg"),
    (HTML_WITH_LOGO, None), (XHTML_WITH_LOGO, None),
    (b'<?xml version="1.0"?><Error><Code>SlowDown</Code></Error>', None),
])
def test_only_a_document_whose_root_is_svg_is_an_svg(data, kind):
    assert _not_a_still_image(data) == kind


# --------------------------------------------------------------------------- #
# 2. O teste da imagem desenha-o                                                #
# --------------------------------------------------------------------------- #


def test_an_ipfs_svg_passes_the_image_test_as_png():
    w, net = _wired(**{"gateway.pinata.cloud": SVG})
    head, size = w.finder._probe_image(URI)                         # noqa: SLF001
    assert sniff_media_type(head) == "image/png" and size > 0
    # two requests on the same gateway, both counted: the head, then the whole
    assert [h for h, _ in net.seen] == ["gateway.pinata.cloud"] * 2
    assert w.larder_image.stats["outcomes"] == {1: {"svg": 1, "svg→png": 1}}


def test_an_svg_that_will_not_draw_is_still_theirs():
    w, _ = _wired(**{"gateway.pinata.cloud": SVG_EXTERNAL})
    with pytest.raises(ImageUnusable) as e:
        w.finder._probe_image(URI)                                  # noqa: SLF001
    assert e.value.kind == "svg"
    assert w.larder_image.stats["outcomes"][1] == {"svg": 1, "svg-não-desenha": 1}


def test_our_failed_read_of_the_whole_svg_is_ours_and_the_next_gateway_is_asked():
    calls = {"n": 0}

    def flaky(url, headers):
        calls["n"] += 1
        if calls["n"] == 2:                  # the whole-file read on gateway 1
            raise TimeoutError("slow")
        return SVG
    w = build_target(settings(), anthropic=object(), repo=FakeRepo(),
                     http_get=lambda u, h: "{}", http_post=rpc_ok,
                     http_get_bytes=lambda u, h: b"",
                     http_get_range=lambda u, h: (flaky(u, h), 4096),
                     http_get_larder_art=flaky)
    head, _ = w.finder._probe_image(URI)                            # noqa: SLF001
    assert sniff_media_type(head) == "image/png"
    assert w.larder_image.stats["outcomes"][1] == {"svg": 1, "timeout": 1}
    assert w.larder_image.stats["outcomes"][2] == {"svg": 1, "svg→png": 1}


def test_when_every_whole_read_fails_it_is_ours_not_an_unusable_svg():
    """Pela regra de 29/09: uma leitura nossa que falhou não culpa o
    candidato — e o /prepare mantém-no."""
    def head_only(url, headers):
        raise TimeoutError("whole file never arrives")
    w = build_target(settings(), anthropic=object(), repo=FakeRepo(),
                     http_get=lambda u, h: "{}", http_post=rpc_ok,
                     http_get_bytes=lambda u, h: b"",
                     http_get_range=lambda u, h: (SVG, 4096),
                     http_get_larder_art=head_only)
    with pytest.raises(ImageGatewaysDown):
        w.finder._probe_image(URI)                                  # noqa: SLF001


@pytest.mark.parametrize("page", [HTML_WITH_LOGO, XHTML_WITH_LOGO])
def test_an_error_page_with_an_svg_logo_is_ours_not_art(page):
    w, net = _wired(**{"gateway.pinata.cloud": page})       # gw0, gw1 time out
    with pytest.raises(ImageGatewaysDown):
        w.finder._probe_image(URI)                                  # noqa: SLF001
    assert len(net.seen) == 3                    # no whole-file read was made


def test_the_whole_svg_read_carries_the_dedicated_key_to_the_dedicated_only():
    net = Net(**{"dedicado.example": SVG})
    w = _build(_with_dedicated(), net)
    w.finder._probe_image(URI)                                      # noqa: SLF001
    assert [h for h, _ in net.seen] == ["dedicado.example"] * 2
    assert all(hd.get(GATEWAY_KEY_HEADER) == KEY for _, hd in net.seen)
    assert DEDICATED.startswith("https://dedicado.example")


# --------------------------------------------------------------------------- #
# 3. A arte completa do /prepare também                                         #
# --------------------------------------------------------------------------- #


def test_prepare_draws_the_whole_svg_for_vision_without_another_read():
    w, net = _wired(**{"gateway.pinata.cloud": SVG})
    art = w.larder_preparer._fetch_image(URI)                       # noqa: SLF001
    assert sniff_media_type(art) is not None and art[:8] == b"\x89PNG\r\n\x1a\n"
    assert len(net.seen) == 1                    # the artwork read IS the whole file


def test_prepare_refuses_an_svg_that_will_not_draw_as_theirs():
    w, _ = _wired(**{"gateway.pinata.cloud": SVG_EXTERNAL})
    with pytest.raises(ImageUnusable):
        w.larder_preparer._fetch_image(URI)                         # noqa: SLF001
