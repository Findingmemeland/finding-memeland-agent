"""Imagens guardadas na cadeia (`data:`) — o caminho que nunca existiu.

O DEFEITO (29/09). `uri_is_content_addressed` aceita `data:`, e bem: é a
forma mais imutável que há, a imagem vive dentro do contrato. Mas o teste da
imagem e o descarregamento da arte só sabiam perguntar a gateways IPFS, e
`gateway_url` devolve None para `data:`. O teste saltava todos os gateways e
respondia "sem bytes".

Resultado: nenhum NFT on-chain conseguiu alguma vez entrar na despensa, e o
relatório dizia "imagem" — pin morto, culpa deles. Na colheita de Base
morreram assim 8 em 8 candidatos numa corrida, 43 em 50 noutra.

O que estes testes fixam:
1. PNG / JPEG / GIF / WebP em base64 passam o teste E chegam à visão.
2. SVG é convertido para PNG (decisão do Pedro, 29/09 — 5 de 9 candidatos
   da primeira colheita morriam aqui), com recusas pelas razões certas:
   referências externas, tamanho ilegível, render de uma só cor.
3. Uma string gigante não vira gigabytes em memória.
(O "único" e o "dono" pela razão: test_rejection_reasons.py.)
"""
from __future__ import annotations

import base64
import struct
import sys
import types
import zlib
from contextlib import contextmanager

import pytest

from finding_memeland.target.adapters import (
    SVG_RENDER_EDGE,
    decode_data_image,
    decode_data_uri,
    inline_artwork,
    probe_inline_image,
    rasterize_svg,
    svg_size,
)
from finding_memeland.target.refresh import image_uri_kind


def _real_png(*pixels: tuple[int, int, int]) -> bytes:
    """Um PNG verdadeiro de N×1, feito à mão — o Pillow (quando existe)
    recusa bytes que só COMEÇAM como PNG, e bem."""
    pixels = pixels or ((255, 0, 0),)

    def chunk(tag: bytes, body: bytes) -> bytes:
        return (struct.pack(">I", len(body)) + tag + body
                + struct.pack(">I", zlib.crc32(tag + body) & 0xFFFFFFFF))
    ihdr = struct.pack(">IIBBBBB", len(pixels), 1, 8, 2, 0, 0, 0)
    idat = zlib.compress(b"\x00" + b"".join(bytes(p) for p in pixels))
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", ihdr)
            + chunk(b"IDAT", idat) + chunk(b"IEND", b""))


PNG = _real_png()
TWO_COLOURS = _real_png((255, 0, 0), (0, 0, 255))
ONE_COLOUR = _real_png((9, 9, 9), (9, 9, 9))
GIF = b"GIF89a" + b"\x00" * 50
SVG = b'<svg xmlns="http://www.w3.org/2000/svg"><rect/></svg>'


def _b64(mime: str, data: bytes) -> str:
    return f"data:{mime};base64,{base64.b64encode(data).decode()}"


# --------------------------------------------------------------------------- #
# Descodificar                                                                  #
# --------------------------------------------------------------------------- #


def test_a_base64_png_decodes():
    assert decode_data_image(_b64("image/png", PNG), max_bytes=10_000) == PNG


def test_a_percent_encoded_svg_decodes():
    uri = "data:image/svg+xml;utf8,%3Csvg%3E%3C%2Fsvg%3E"
    assert decode_data_image(uri, max_bytes=10_000) == b"<svg></svg>"


def test_a_huge_payload_is_refused_before_decoding():
    """Uma string de 200 MB num tokenURI não pode virar 200 MB em memória."""
    huge = "data:image/png;base64," + "A" * 4_000_000
    assert decode_data_image(huge, max_bytes=1_000_000) is None


def test_garbage_is_the_candidate_s_problem_not_a_crash():
    for bad in ["", "ipfs://Qm", "data:image/png;base64", "data:,",
                "data:image/png;base64,@@@@"]:
        assert decode_data_image(bad, max_bytes=10_000) in (None, b""), bad


def test_the_metadata_decoder_is_not_shadowed():
    """O PRIMEIRO RASCUNHO deste arranjo deu à função de imagens o mesmo
    nome que a de metadata JSON. Em Python a segunda definição apaga a
    primeira: toda a metadata `data:` on-chain passaria a rebentar com
    TypeError. Isto prende os dois nomes separados."""
    doc = decode_data_uri('data:application/json,{"name":"Two Words"}')
    assert doc == {"name": "Two Words"}


# --------------------------------------------------------------------------- #
# O teste da imagem                                                             #
# --------------------------------------------------------------------------- #


def test_an_onchain_png_passes_the_probe():
    """O TESTE. Antes de 29/09 isto devolvia None — "pin morto"."""
    got = probe_inline_image(_b64("image/png", PNG), max_bytes=10_000,
                             probe_bytes=64)
    assert got is not None
    head, size = got
    assert head.startswith(b"\x89PNG") and size == len(PNG)


def test_an_onchain_gif_passes_the_probe():
    assert probe_inline_image(_b64("image/gif", GIF), max_bytes=10_000,
                              probe_bytes=64) is not None


def test_without_the_library_an_svg_is_refused_not_a_crash():
    """Se o resvg-py faltar no Railway, o SVG volta a ser recusado — como
    antes de 29/09 — e nada rebenta."""
    with _no_resvg():
        assert probe_inline_image(_b64("image/svg+xml", WIDE), max_bytes=10_000,
                                  probe_bytes=64) is None
        assert inline_artwork(_b64("image/svg+xml", WIDE), max_bytes=10_000) is None


def test_bytes_claiming_to_be_png_but_are_not_are_refused():
    fake = _b64("image/png", b"<html>throttled</html>")
    assert probe_inline_image(fake, max_bytes=10_000, probe_bytes=64) is None


# --------------------------------------------------------------------------- #
# A arte para a visão (/prepare)                                                #
# --------------------------------------------------------------------------- #


def test_an_onchain_png_reaches_the_vision_step():
    """Sem isto, um NFT on-chain que passasse o depósito morria no
    /prepare — mesmo defeito, um passo mais à frente."""
    assert inline_artwork(_b64("image/png", PNG), max_bytes=10_000) is not None


# --------------------------------------------------------------------------- #
# SVG → PNG                                                                     #
# --------------------------------------------------------------------------- #

WIDE = b'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 400 100"><rect/></svg>'
TALL = b'<svg xmlns="http://www.w3.org/2000/svg" width="50" height="300"><rect/></svg>'


@contextmanager
def _no_resvg():
    saved = sys.modules.get("resvg_py", _MISSING)
    sys.modules["resvg_py"] = None           # import → ImportError
    try:
        yield
    finally:
        _restore(saved)


@contextmanager
def _fake_resvg(png: bytes = TWO_COLOURS, *, boom: bool = False):
    calls: list[dict] = []

    def svg_to_bytes(**kw):
        calls.append(kw)
        if boom:
            raise ValueError("bad svg")
        return png
    saved = sys.modules.get("resvg_py", _MISSING)
    sys.modules["resvg_py"] = types.SimpleNamespace(svg_to_bytes=svg_to_bytes)
    try:
        yield calls
    finally:
        _restore(saved)


_MISSING = object()


def _restore(saved):
    if saved is _MISSING:
        sys.modules.pop("resvg_py", None)
    else:
        sys.modules["resvg_py"] = saved


def test_an_onchain_svg_passes_the_probe_as_png():
    """O TESTE de 29/09. Um SVG on-chain chega ao probe como PNG."""
    with _fake_resvg() as calls:
        got = probe_inline_image(_b64("image/svg+xml", WIDE), max_bytes=10_000,
                                 probe_bytes=64)
    assert got is not None and got[0].startswith(b"\x89PNG")
    assert len(calls) == 1


def test_an_onchain_svg_reaches_the_vision_step_as_png():
    with _fake_resvg():
        art = inline_artwork("data:image/svg+xml;utf8," + WIDE.decode(),
                             max_bytes=10_000)
    assert art is not None and art.startswith(b"\x89PNG")


def test_the_long_edge_is_the_one_fixed():
    """Fixar o lado MAIOR garante que o outro nunca passa de 1024 — um SVG
    de 1×100000 não vira gigabytes."""
    with _fake_resvg() as calls:
        rasterize_svg(WIDE)
        rasterize_svg(TALL)
    assert calls[0].get("width") == SVG_RENDER_EDGE and "height" not in calls[0]
    assert calls[1].get("height") == SVG_RENDER_EDGE and "width" not in calls[1]


def test_the_bundled_font_is_passed():
    """Sem fonte, o texto de um SVG desaparece e a visão descreve um vazio."""
    with _fake_resvg() as calls:
        rasterize_svg(WIDE)
    fonts = calls[0].get("font_files") or []
    assert any(f.endswith("DejaVuSans.ttf") for f in fonts)


def test_external_references_are_refused_before_rendering():
    """O renderizador podia ler ficheiros do NOSSO contentor, e uma obra que
    depende de fora não é a que está na cadeia."""
    bad = [
        b'<image href="https://x.io/a.png"/>',
        b'<image xlink:href="/etc/passwd"/>',
        b'<image href="file:///app/secret"/>',
        b'<rect style="fill:url(https://x.io/p.svg#g)"/>',
        b'<style>@import "https://x.io/f.css";</style>',
    ]
    for frag in bad:
        svg = WIDE.replace(b"<rect/>", frag)
        with _fake_resvg() as calls:
            assert rasterize_svg(svg) is None, frag
        assert calls == [], frag


def test_internal_references_are_fine():
    ok = [b'<use href="#a"/>', b'<rect fill="url(#g)"/>',
          b'<image href="data:image/png;base64,AAAA"/>']
    for frag in ok:
        with _fake_resvg():
            assert rasterize_svg(WIDE.replace(b"<rect/>", frag)) is not None, frag


def test_an_svg_without_a_readable_size_is_refused():
    with _fake_resvg() as calls:
        assert rasterize_svg(SVG) is None                 # no viewBox, no size
        assert rasterize_svg(SVG.replace(b"<svg ", b'<svg width="100%" ')) is None
    assert calls == []


def test_a_render_of_one_colour_is_refused():
    """Uma só cor é texto sem fonte, ou arte que não desenhou."""
    with _fake_resvg(ONE_COLOUR):
        assert rasterize_svg(WIDE) is None


def test_a_render_that_throws_is_the_candidate_s_problem():
    with _fake_resvg(boom=True):
        assert rasterize_svg(WIDE) is None


def test_an_oversized_svg_is_refused_before_rendering():
    big = WIDE.replace(b"<rect/>", b"<!--" + b"x" * 3_000_000 + b"-->")
    with _fake_resvg() as calls:
        assert rasterize_svg(big) is None
    assert calls == []


def test_svg_size_reads_viewbox_then_dimensions():
    assert svg_size(WIDE) == (400.0, 100.0)
    assert svg_size(TALL) == (50.0, 300.0)
    assert svg_size(b'<svg viewBox="0,0,24,24"/>') == (24.0, 24.0)
    assert svg_size(SVG) is None


def test_the_real_library_renders_shapes_and_text():
    """Só corre onde o resvg-py está instalado (no teu Mac, depois do
    `pip install -r requirements.txt`). Prova a biblioteca e a fonte."""
    pytest.importorskip("resvg_py")
    shapes = (b'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 40 20">'
              b'<rect width="20" height="20" fill="red"/>'
              b'<rect x="20" width="20" height="20" fill="blue"/></svg>')
    png = rasterize_svg(shapes)
    assert png is not None and png.startswith(b"\x89PNG")
    text_only = (b'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 200 50">'
                 b'<rect width="200" height="50" fill="black"/>'
                 b'<text x="10" y="35" font-size="30" fill="white">Hi there</text></svg>')
    assert rasterize_svg(text_only) is not None, "texto não desenhou: fonte?"


# --------------------------------------------------------------------------- #
# Contar pela razão certa                                                       #
# --------------------------------------------------------------------------- #


def test_image_kinds_separate_svg_from_other_onchain_and_ipfs():
    assert image_uri_kind(_b64("image/svg+xml", SVG)) == "data-svg"
    assert image_uri_kind("data:image/svg+xml;utf8,<svg/>") == "data-svg"
    assert image_uri_kind(_b64("image/png", PNG)) == "data"
    assert image_uri_kind("ipfs://bafyxyz") == "ipfs"


def test_the_deposit_report_names_the_kind_of_dead_image():
    """No Telegram tem de se ver QUAL tipo de imagem falhou — senão uma
    cadeia cheia de SVGs volta a parecer uma cadeia cheia de pins mortos."""
    import random

    from finding_memeland.target.prepare import Larder, Source, TargetFinder
    from finding_memeland.target.refresh import TokenRead

    svg = "data:image/svg+xml;utf8,<svg/>"
    f = TargetFinder(
        sources=[Source("x", "base", "0x" + "cd" * 20)],
        total_supply=lambda c, k: 10, token_by_index=lambda c, k, i: i + 1,
        read_token=lambda c, k, t: TokenRead(
            token_uri="data:application/json,{}",
            metadata={"name": "Some Two Words", "image": svg}),
        probe_image=lambda u: None, owner_is_eoa=lambda *a: True,
        name_is_unique=lambda *a: True, rng=random.Random(0))
    rep = f.deposit(Larder(), ["base:0x" + "ab" * 20 + ":1"],
                    chain_ok=lambda c: True)
    assert rep.added == 0
    assert "imagem 1 (data-svg 1)" in rep.render()
