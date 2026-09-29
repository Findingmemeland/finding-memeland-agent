"""Imagens guardadas na cadeia (`data:`) — o caminho que nunca existiu.

O DEFEITO (30/09). `uri_is_content_addressed` aceita `data:`, e bem: é a
forma mais imutável que há, a imagem vive dentro do contrato. Mas o teste da
imagem e o descarregamento da arte só sabiam perguntar a gateways IPFS, e
`gateway_url` devolve None para `data:`. O teste saltava todos os gateways e
respondia "sem bytes".

Resultado: nenhum NFT on-chain conseguiu alguma vez entrar na despensa, e o
relatório dizia "imagem" — pin morto, culpa deles. Na colheita de Base
morreram assim 8 em 8 candidatos numa corrida, 43 em 50 noutra.

O que estes testes fixam:
1. PNG / JPEG / GIF / WebP em base64 passam o teste E chegam à visão.
2. SVG continua recusado — a visão não o lê — mas conta à parte, porque
   aceitá-lo é a decisão seguinte e o número diz se vale a pena.
3. Uma string gigante não vira gigabytes em memória.
"""
from __future__ import annotations

import base64
import struct
import zlib

from finding_memeland.target.adapters import (
    decode_data_image,
    decode_data_uri,
    inline_artwork,
    probe_inline_image,
)
from finding_memeland.target.refresh import image_uri_kind


def _real_png() -> bytes:
    """Um PNG 1×1 verdadeiro, feito à mão — o Pillow (quando existe)
    recusa bytes que só COMEÇAM como PNG, e bem."""
    def chunk(tag: bytes, body: bytes) -> bytes:
        return (struct.pack(">I", len(body)) + tag + body
                + struct.pack(">I", zlib.crc32(tag + body) & 0xFFFFFFFF))
    ihdr = struct.pack(">IIBBBBB", 1, 1, 8, 2, 0, 0, 0)
    idat = zlib.compress(b"\x00\xff\x00\x00")
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", ihdr)
            + chunk(b"IDAT", idat) + chunk(b"IEND", b""))


PNG = _real_png()
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
    """O TESTE. Antes de 30/09 isto devolvia None — "pin morto"."""
    got = probe_inline_image(_b64("image/png", PNG), max_bytes=10_000,
                             probe_bytes=64)
    assert got is not None
    head, size = got
    assert head.startswith(b"\x89PNG") and size == len(PNG)


def test_an_onchain_gif_passes_the_probe():
    assert probe_inline_image(_b64("image/gif", GIF), max_bytes=10_000,
                              probe_bytes=64) is not None


def test_an_onchain_svg_is_still_refused():
    """A visão não lê SVG. Recusado como antes — agora pela razão certa."""
    assert probe_inline_image(_b64("image/svg+xml", SVG), max_bytes=10_000,
                              probe_bytes=64) is None


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


def test_an_onchain_svg_does_not_reach_vision():
    assert inline_artwork(_b64("image/svg+xml", SVG), max_bytes=10_000) is None


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
