"""O teste da imagem, por causa (30/09).

O /harvest de 30/09 perdeu 13 de 18 candidatos de Base em "gateway-imagem",
logo a seguir a 52 respostas 429 na metadata do mesmo gateway — mas o teste da
imagem não contava por causa, e "provavelmente 429" não é uma medição (R8).
Agora conta como a metadata: por gateway e por resultado. Só medição.
"""
from __future__ import annotations

import pytest
from test_gateway_outcomes import _Reader
from test_harvest_command import _Harv, _Store
from test_image_verdicts import HTML, MP4, PNG, URI, _by_host
from test_target_prepare import World, _finder
from test_target_wiring import FakeRepo, rpc_ok, settings

from finding_memeland.target.adapters import GatewayTally
from finding_memeland.target.sources import ChainUnavailable
from finding_memeland.target.wiring import TargetWiring, build_target

A = "0x" + "a1" * 20


def _wired(**hosts):
    get = _by_host(**hosts)
    w = build_target(settings(), anthropic=object(), repo=FakeRepo(),
                     http_get=lambda u, h: "{}", http_post=rpc_ok,
                     http_get_bytes=lambda u, h: b"",
                     http_get_range=lambda u, h: (get(u, h), 4096),
                     http_get_larder_art=get)
    return w


def test_the_image_test_counts_every_request_by_gateway_and_outcome():
    w = _wired(**{"gateway.pinata.cloud": 429, "gw0": PNG})
    w.finder._probe_image(URI)                                  # noqa: SLF001
    assert w.larder_image.stats["outcomes"] == {1: {"429": 1}, 2: {"serviu": 1}}


@pytest.mark.parametrize("answer, outcome", [
    (TimeoutError(), "timeout"), (404, "404"), (503, "503"),
    (HTML, "html"), (b"", "vazio"), (MP4, "video/mp4"),
])
def test_what_each_gateway_answered_is_named(answer, outcome):
    w = _wired(**{"gateway.pinata.cloud": answer, "gw0": 503, "gw1": 503})
    with pytest.raises(ChainUnavailable):
        w.finder._probe_image(URI)                              # noqa: SLF001
    assert w.larder_image.stats["outcomes"][1] == {outcome: 1}


def test_the_full_artwork_is_counted_too():
    w = _wired(**{"gateway.pinata.cloud": 429, "gw0": 429, "gw1": 429})
    with pytest.raises(ChainUnavailable):
        w.larder_preparer._fetch_image(URI)                     # noqa: SLF001
    assert w.larder_image.stats["outcomes"] == {1: {"429": 1}, 2: {"429": 1},
                                                3: {"429": 1}}


def test_the_tally_adds_up():
    t = GatewayTally()
    for _ in range(3):
        t.note(1, "429")
    t.note(2, "serviu")
    assert t.stats["outcomes"] == {1: {"429": 3}, 2: {"serviu": 1}}


# --------------------------------------------------------------------------- #
# A linha do /harvest                                                           #
# --------------------------------------------------------------------------- #


def _line(meta, image, *, on_probe):
    world = World()
    inner = world.probe_image

    def probe(url):
        on_probe(image)
        return inner(url)
    world.probe_image = probe
    tw = TargetWiring(
        ports=None, pipeline=None, epoch=None, snapshot_store=None,
        scan_blocks=0, writability_rates={}, uniqueness_rates={},
        finder=_finder(world), larder_store=_Store(),
        harvesters={"ethereum": _Harv([f"ethereum:{A}:1"])},
        deposit_chains=frozenset({"ethereum"}),
        larder_meta=meta, larder_image=image)
    return tw.harvest(200)


def test_the_line_shows_the_deposit_s_image_test_per_gateway():
    def probe(image):
        for _ in range(13):
            image.note(1, "429")
        image.note(1, "serviu")
    out = _line(_Reader(), GatewayTally(), on_probe=probe)
    assert "gateway 1 — depósito (imagem): serviu 1, 429 13" in out, out


def test_metadata_and_image_share_the_gateway_s_part():
    meta = _Reader()

    class _H(_Harv):
        def harvest(self, n, **kw):
            meta.note(1, "timeout", 3)
            return super().harvest(n, **kw)

    world = World()
    inner = world.probe_image
    image = GatewayTally()

    def probe(url):
        image.note(1, "serviu")
        return inner(url)
    world.probe_image = probe
    tw = TargetWiring(
        ports=None, pipeline=None, epoch=None, snapshot_store=None,
        scan_blocks=0, writability_rates={}, uniqueness_rates={},
        finder=_finder(world), larder_store=_Store(),
        harvesters={"ethereum": _H([f"ethereum:{A}:1"])},
        deposit_chains=frozenset({"ethereum"}), larder_meta=meta, larder_image=image)
    out = tw.harvest(200)
    assert "gateway 1 — colheita: timeout 3; depósito (imagem): serviu 1" in out, out


def test_without_the_image_counter_the_line_is_as_before():
    out = _line(_Reader(), None, on_probe=lambda image: None)
    assert "(imagem)" not in out and "rotação de gateways" in out, out
