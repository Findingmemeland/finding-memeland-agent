"""The opening post's card: "HUNT #N" and the logo (Pedro, 24/11).

A picture makes the opening post stand out in the timeline. It is drawn from
the HUNT NUMBER and nothing else. `render_hunt_card` takes no other argument,
so nothing about the target, the clues or the name can ever reach it. The
same number always gives the same bytes.

The look is the one of Pedro's hand-made cards (Hunt18_Card_TODAY.png): near
black, a warm glow behind the skull, the orange "FINDING MEMELAND" label, the
headline in cream, the handle at the foot. 1600×900. Nothing else is written
on it (Pedro, 25/11: the label, "HUNT #N", the logo and the handle).

  · LOGO: assets/logo.png is the current avatar, byte for byte (site/
    avatar.png, sha256 ee4edbb0…d747). Its black background is cut out from
    the corners, and a black ring is put back around the silhouette, so the
    outer outline that the cut takes away still shows against the glow.
  · FONT: DejaVu Sans, already in the repository (fonts/, for the SVG
    rendering), thickened with a stroke where Pedro's cards use the bold.
    No font is downloaded and none comes from the system: the card looks the
    same on this machine and on Railway.

A failure here never stops a hunt. The caller posts the text alone and warns
the operator (orchestrator._go_live).
"""

from __future__ import annotations

import functools
import io
from pathlib import Path

_HERE = Path(__file__).parent
LOGO_PATH = _HERE / "assets" / "logo.png"
FONT_PATH = _HERE / "fonts" / "DejaVuSans.ttf"

W, H = 1600, 900
_BG = (10, 8, 6)
_GLOW = (78, 42, 8)
_ORANGE = (240, 160, 32)
_INK = (20, 14, 6)
_CREAM = (240, 235, 224)
_GREY = (150, 146, 138)
_LOGO_CENTRE = (420, 450)
_TEXT_X = 810
_TEXT_RIGHT = 1540
# the text column, top to bottom. Without "live now." (25/11) the label and
# the headline came down 65 px and the rule went up 65, so no hole is left
# under the number; the handle stays at the foot.
_LABEL_Y = 297
_HEADLINE_Y = 365
_RULE_Y = 550
_HANDLE_Y = 790


def card_alt_text(hunt_n: int) -> str:
    """Alt-text for the card (R9) — what is on it, nothing more."""
    return f"Finding Memeland — hunt #{int(hunt_n)}: the skull logo and the hunt number"


def _logo():
    """The avatar with its black background cut out from the corners, and the
    mask of the silhouette (for the black ring)."""
    from PIL import Image, ImageDraw
    logo = Image.open(LOGO_PATH).convert("RGBA")
    # 512 → 640, NEAREST: hard edges, as pixel art wants. The avatar is not
    # on an exact grid (measured 24/11: most colour steps fall off any 8-px
    # grid), so the blocks are not all equal — at this size it does not show.
    logo = logo.resize((640, 640), Image.Resampling.NEAREST)
    w, h = logo.size
    for corner in ((0, 0), (w - 1, 0), (0, h - 1), (w - 1, h - 1)):
        ImageDraw.floodfill(logo, corner, (0, 0, 0, 0), thresh=24)
    return logo, logo.getchannel("A")


def _font(size: int):
    from PIL import ImageFont
    return ImageFont.truetype(str(FONT_PATH), size)


def _spaced(draw, xy, text, font, fill, spacing, *, measure_only=False):
    """Text with letter spacing (Pillow has none): one glyph at a time.
    Returns where it ends."""
    x, y = xy
    for ch in text:
        if not measure_only:
            draw.text((x, y), ch, font=font, fill=fill)
        x += draw.textlength(ch, font=font) + spacing
    return x - spacing


@functools.lru_cache(maxsize=1)
def _base():
    """Everything but the number — drawn once per process (the cut of the
    logo is the slow part: about a third of a second)."""
    from PIL import Image, ImageDraw, ImageFilter

    card = Image.new("RGB", (W, H), _BG)

    # the glow behind the skull: a radial gradient, warm at the centre
    # (radial_gradient: 0 at the centre, the distance in px of its 256 × 256;
    # zero at 128 = the inscribed circle, so the square never shows an edge)
    glow = Image.radial_gradient("L").point(lambda v: max(0, 255 - 2 * v))
    glow = glow.resize((1200, 1200), Image.Resampling.BICUBIC)
    cx, cy = _LOGO_CENTRE
    card.paste(Image.new("RGB", glow.size, _GLOW), (cx - 600, cy - 600), glow)

    # the skull, with its outline put back as a black ring
    logo, mask = _logo()
    box = (cx - logo.width // 2, cy - logo.height // 2)
    card.paste((0, 0, 0), (*box, box[0] + logo.width, box[1] + logo.height),
               mask.filter(ImageFilter.MaxFilter(9)))
    card.paste(logo, box, logo)

    d = ImageDraw.Draw(card)

    # the label: FINDING MEMELAND on orange
    label_font = _font(30)
    lx, ly = _TEXT_X, _LABEL_Y
    end = _spaced(d, (lx + 16, ly + 9), "FINDING MEMELAND", label_font, _INK, 3,
                  measure_only=True)
    d.rectangle((lx, ly, end + 16, ly + 52), fill=_ORANGE)
    _spaced(d, (lx + 16, ly + 9), "FINDING MEMELAND", label_font, _INK, 3)

    # a thin rule under the headline, and the handle at the foot
    d.line((_TEXT_X, _RULE_Y, _TEXT_RIGHT, _RULE_Y), fill=(70, 64, 56), width=2)
    _spaced(d, (_TEXT_X, _HANDLE_Y), "@FINDINGMEMELAND", _font(28), _GREY, 3)
    return card


def render_hunt_card(hunt_n: int) -> bytes:
    """PNG bytes of the card for hunt `hunt_n`. Deterministic."""
    from PIL import ImageDraw

    n = int(hunt_n)
    card = _base().copy()
    d = ImageDraw.Draw(card)

    # the headline: HUNT #N, as large as fits the column
    headline = f"HUNT #{n}"
    size = 200
    while size > 60:
        if d.textlength(headline, font=_font(size)) + 8 <= _TEXT_RIGHT - _TEXT_X:
            break
        size -= 6
    d.text((_TEXT_X - 4, _HEADLINE_Y), headline, font=_font(size), fill=_CREAM,
           stroke_width=max(2, size // 40), stroke_fill=_CREAM)

    out = io.BytesIO()
    card.save(out, format="PNG")
    return out.getvalue()
