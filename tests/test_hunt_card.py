"""O cartão do post de abertura (Pedro, 24/11): "HUNT #N" em destaque e o
logo, gerado em cada hunt.

  · O cartão nunca pode conter nada sobre o alvo, as pistas ou o nome.
    `render_hunt_card` recebe o NÚMERO e mais nada. O mesmo número dá
    sempre os mesmos bytes, seja qual for o alvo.
  · Se o desenho ou o upload falharem, o post sai na mesma só com texto. Uma
    falha na imagem nunca impede uma hunt de arrancar: o desenho falha à
    parte com um aviso, o upload já degradava (XClient.post), e agora também
    a recusa do X a um post COM imagem (4xx: nada foi criado) volta a
    publicar só o texto. Um 5xx sobe como antes: o post pode existir, e um
    gémeo seria pior.
  · O reveal continua sem imagem (decisão de 10/09): a única imagem de uma
    hunt é este cartão.
"""
from __future__ import annotations

import hashlib
import inspect
import io

import pytest
import tweepy

from finding_memeland.social.x_client import XClient
from finding_memeland.target import hunt_card, wiring
from finding_memeland.target.dryrun import TargetWorld
from finding_memeland.target.hunt_card import (
    LOGO_PATH,
    card_alt_text,
    render_hunt_card,
)

PNG = b"\x89PNG\r\n\x1a\n"


# --------------------------------------------------------------------------- #
# O cartão                                                                     #
# --------------------------------------------------------------------------- #


def test_a_png_x_takes_deterministic_and_one_per_number():
    from PIL import Image
    a = render_hunt_card(42)
    assert a[:8] == PNG and len(a) < 5 * 1024 * 1024        # tweet_image: até 5 MB
    im = Image.open(io.BytesIO(a))
    assert (im.format, im.size, im.mode) == ("PNG", (1600, 900), "RGB")
    assert render_hunt_card(42) == a                         # os mesmos bytes, sempre
    assert render_hunt_card(43) != a and render_hunt_card(142) != a


def test_it_is_drawn_from_the_number_and_nothing_else():
    """Pela assinatura: não há por onde entrar o alvo, as pistas ou o nome."""
    assert list(inspect.signature(render_hunt_card).parameters) == ["hunt_n"]
    assert list(inspect.signature(card_alt_text).parameters) == ["hunt_n"]
    assert card_alt_text(42) == ("Finding Memeland — hunt #42: the skull logo "
                                 "and the hunt number")


def test_the_same_number_on_two_different_targets_gives_the_same_card():
    cards, targets = [], []
    for seed in (0, 1, 2):
        w = TargetWorld(seed=seed)
        hunt = w.launch()
        targets.append(hunt.target.target.id())
        cards.append(w.rig.publisher.media[hunt.reshare_post_id])
    assert len(set(targets)) > 1                              # alvos diferentes
    assert cards[0] == cards[1] == cards[2] == render_hunt_card(1)


def test_the_logo_is_the_avatar_byte_for_byte():
    digest = hashlib.sha256(LOGO_PATH.read_bytes()).hexdigest()
    assert digest == "ee4edbb0b1002c2053b3281d0d13e68a0a2a324b0b0ad3c05330b7ef7109d747"


def test_the_logo_keeps_its_insides_and_loses_its_background():
    from PIL import Image
    logo, mask = hunt_card._logo()
    assert mask.getpixel((0, 0)) == 0 and mask.getpixel((639, 639)) == 0    # o fundo
    assert mask.getpixel((320, 320)) == 255                                   # a caveira
    card = Image.open(io.BytesIO(render_hunt_card(42))).convert("RGB")
    assert card.getpixel((1590, 10)) == hunt_card._BG                         # o canto


def test_a_big_number_still_fits():
    from PIL import Image, ImageChops
    base = hunt_card._base()
    for n in (7, 1042, 99999):
        card = Image.open(io.BytesIO(render_hunt_card(n))).convert("RGB")
        box = ImageChops.difference(card, base).getbbox()
        assert box is not None and box[2] <= hunt_card._TEXT_RIGHT + 2, (n, box)


def _column_bands(png: bytes) -> list[tuple[int, int]]:
    """As linhas da coluna do texto com algo claro, em faixas (primeira,
    última). O brilho do fundo e a linha fina ficam abaixo do limiar; a
    etiqueta, o número e o @ ficam acima."""
    from PIL import Image, ImageChops
    col = Image.open(io.BytesIO(png)).convert("RGB").crop(
        (hunt_card._TEXT_X - 10, 0, hunt_card.W, hunt_card.H))
    r, g, b = col.split()
    bright = ImageChops.lighter(ImageChops.lighter(r, g), b).point(
        lambda v: 255 if v > 100 else 0)
    bands, start = [], None
    for y in range(bright.height):
        lit = bright.crop((0, y, bright.width, y + 1)).getbbox() is not None
        if lit and start is None:
            start = y
        elif not lit and start is not None:
            bands.append((start, y - 1))
            start = None
    if start is not None:
        bands.append((start, bright.height - 1))
    return bands


@pytest.mark.parametrize("n", [1, 42, 142, 1042, 99999])
def test_the_column_says_the_label_the_number_and_the_handle_and_nothing_else(n):
    """Pedro, 25/11: sem o "live now.". Na coluna do texto ficam a etiqueta,
    "HUNT #N" e o @FINDINGMEMELAND, por esta ordem, e mais nada; a linha
    fina passa entre o número e o @ sem tocar em nenhum."""
    bands = _column_bands(render_hunt_card(n))
    assert len(bands) == 3, bands
    label, number, handle = bands
    assert label[0] == hunt_card._LABEL_Y
    assert label[1] < number[0] and number[1] < hunt_card._RULE_Y < handle[0]
    assert handle[0] >= hunt_card._HANDLE_Y


# --------------------------------------------------------------------------- #
# No arranque da hunt                                                          #
# --------------------------------------------------------------------------- #


def test_the_opening_post_carries_the_card_and_its_alt_text():
    w = TargetWorld()
    hunt = w.launch()
    pub = w.rig.publisher
    assert pub.media == {hunt.reshare_post_id: render_hunt_card(hunt.number)}
    assert pub.media_alt[hunt.reshare_post_id] == card_alt_text(hunt.number)
    assert w.rig.repo.hunts[hunt.id]["state"] == "live"


def test_a_card_that_cannot_be_drawn_never_stops_the_hunt():
    w = TargetWorld()

    def broken(_n):
        raise OSError("cannot open resource")

    w.ports.opening_card = broken
    hunt = w.launch()
    assert w.rig.repo.hunts[hunt.id]["state"] == "live"
    assert len(w.posts()) == 1 and w.rig.publisher.media == {}
    notes = " | ".join(w.notices())
    assert "opening card not drawn (OSError) — posting the text alone" in notes
    assert "cannot open resource" not in notes               # o tipo, nunca o texto


def test_no_card_wired_posts_the_text_and_says_nothing():
    w = TargetWorld()
    w.ports.opening_card = None
    hunt = w.launch()
    assert w.rig.publisher.media == {} and w.rig.repo.hunts[hunt.id]["state"] == "live"
    assert not [m for m in w.notices() if "card" in m]


def test_production_wires_the_card():
    assert "opening_card=render_hunt_card" in inspect.getsource(wiring)


# --------------------------------------------------------------------------- #
# O X                                                                          #
# --------------------------------------------------------------------------- #


def _http_error(cls, status):
    resp = type("R", (), {"status_code": status, "reason": "x",
                          "json": lambda self: {"errors": [{"message": "x"}]}})()
    return cls(resp)


class _V2:
    def __init__(self, refuse_media=None):
        self.calls = []
        self.refuse_media = refuse_media

    def create_tweet(self, **kw):
        self.calls.append(kw)
        if self.refuse_media is not None and kw.get("media_ids"):
            raise self.refuse_media
        return type("R", (), {"data": {"id": 777}})()


class _V11:
    def media_upload(self, filename, *, file):
        return type("M", (), {"media_id": 4242})()

    def create_media_metadata(self, media_id, alt_text):
        pass


def _client(v2, warns):
    xc = XClient(api_key="k", api_secret="s", main_access_token="t",
                 main_access_secret="ts", warn=warns.append)
    xc._client = v2
    xc._api_for = lambda tok, sec: _V11()
    return xc


@pytest.mark.parametrize("cls,status", [(tweepy.errors.BadRequest, 400),
                                        (tweepy.errors.Forbidden, 403)])
def test_x_refusing_the_post_with_the_picture_posts_the_text_alone(cls, status):
    warns = []
    v2 = _V2(refuse_media=_http_error(cls, status))
    assert _client(v2, warns).post("hunt #42", media=PNG + b"\0" * 8) == "777"
    assert v2.calls == [{"text": "hunt #42", "user_auth": True, "media_ids": ["4242"]},
                        {"text": "hunt #42", "user_auth": True}]
    assert len(warns) == 1 and f"refused the post with the picture ({cls.__name__})" in warns[0]


def test_a_server_error_is_raised_as_before_never_a_twin(monkeypatch):
    monkeypatch.setattr("finding_memeland.social.x_client.time.sleep", lambda s: None)
    v2 = _V2(refuse_media=_http_error(tweepy.errors.TwitterServerError, 503))
    with pytest.raises(tweepy.errors.TwitterServerError):
        _client(v2, []).post("hunt #42", media=PNG + b"\0" * 8)
    assert all(c.get("media_ids") for c in v2.calls)          # nunca o texto à parte


def test_a_refusal_without_a_picture_is_raised_as_before():
    class _Refuses(_V2):
        def create_tweet(self, **kw):
            self.calls.append(kw)
            raise _http_error(tweepy.errors.Forbidden, 403)

    v2 = _Refuses()
    with pytest.raises(tweepy.errors.Forbidden):
        _client(v2, []).post("hunt #42")
    assert len(v2.calls) == 1
