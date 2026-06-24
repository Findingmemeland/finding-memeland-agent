import base64
import os
import tempfile

from finding_memeland.persona.avatar import (
    AVATAR_STYLE_SUFFIX,
    BANNER_SIZE,
    BANNER_STYLE_SUFFIX,
    AvatarGenerator,
    save_png,
)


class _FakeImage:
    def __init__(self, b64):
        self.b64_json = b64


class _FakeResponse:
    def __init__(self, b64):
        self.data = [_FakeImage(b64)]


class _FakeClient:
    """Mimics client.images.generate(...) returning b64_json."""

    def __init__(self, b64):
        self._b64 = b64
        self.images = self
        self.last_prompt = None
        self.last_size = None

    def generate(self, *, model, prompt, size, n):  # noqa: A003
        self.last_prompt = prompt
        self.last_size = size
        return _FakeResponse(self._b64)


def test_generate_png_decodes_b64_and_appends_style():
    raw = b"\x89PNG\r\n fake bytes"
    client = _FakeClient(base64.b64encode(raw).decode())
    gen = AvatarGenerator(client, model="gpt-image-1", size="1024x1024")
    out = gen.generate_png("a calm three-headed dog at a stone gate")
    assert out == raw
    assert client.last_prompt.endswith(AVATAR_STYLE_SUFFIX)


def test_generate_banner_uses_banner_suffix_and_wide_size():
    raw = b"\x89PNG banner"
    client = _FakeClient(base64.b64encode(raw).decode())
    gen = AvatarGenerator(client, model="gpt-image-1")
    out = gen.generate_banner_png("a night sky with a faint planet")
    assert out == raw
    assert client.last_prompt.endswith(BANNER_STYLE_SUFFIX)
    assert client.last_size == BANNER_SIZE


def test_generate_png_raises_without_b64():
    client = _FakeClient(None)
    gen = AvatarGenerator(client)
    try:
        gen.generate_png("x")
    except RuntimeError:
        return
    raise AssertionError("expected RuntimeError when no b64_json returned")


def test_save_png_roundtrip():
    raw = b"\x89PNG fake"
    path = os.path.join(tempfile.mkdtemp(), "a.png")
    save_png(raw, path)
    with open(path, "rb") as fh:
        assert fh.read() == raw
