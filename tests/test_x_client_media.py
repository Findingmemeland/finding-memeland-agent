"""The reveal's picture (Opus, dry-run 09/09): `XClient.post(media=…)`
uploads through v1.1 media/upload on the main account and attaches the
media_id; an upload failure never blocks the text. Offline: the v2 client
and the v1.1 API are stand-ins — the real path against X is exercised by
the live test before Hunt #11."""

from __future__ import annotations

import pytest

from finding_memeland.social.publisher import XPublisher
from finding_memeland.social.x_client import XClient, _image_ext

PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 8


class _V2:
    def __init__(self):
        self.calls = []

    def create_tweet(self, **kw):
        self.calls.append(kw)
        return type("R", (), {"data": {"id": 777}})()


class _V11:
    def __init__(self, fail=False, alt_fail=False):
        self.uploads = []
        self.alts = []
        self.fail = fail
        self.alt_fail = alt_fail

    def media_upload(self, filename, *, file):
        if self.fail:
            raise RuntimeError("media/upload 400")
        self.uploads.append((filename, file.read()))
        return type("M", (), {"media_id": 4242})()

    def create_media_metadata(self, media_id, alt_text):
        if self.alt_fail:
            raise RuntimeError("metadata 403")
        self.alts.append((media_id, alt_text))


def client(v11, warns=None):
    xc = XClient(api_key="k", api_secret="s", main_access_token="t", main_access_secret="ts",
                 warn=(warns.append if warns is not None else None))
    xc._client = _V2()
    xc._api_for = lambda tok, sec: v11
    return xc


def test_post_with_media_uploads_once_attaches_the_id_and_sets_alt_text():
    v11 = _V11()
    warns = []
    xc = client(v11, warns)
    assert xc.post("reveal", long_post=True, media=PNG, media_alt="“X”, by Y") == "777"
    assert v11.uploads == [("artwork.png", PNG)]
    assert v11.alts == [("4242", "“X”, by Y")]
    assert xc._client.calls == [{"text": "reveal", "user_auth": True, "media_ids": ["4242"]}]
    assert warns == []


def test_alt_text_failure_keeps_the_picture_and_warns():
    v11 = _V11(alt_fail=True)
    warns = []
    xc = client(v11, warns)
    xc.post("reveal", media=PNG, media_alt="alt")
    assert xc._client.calls[0]["media_ids"] == ["4242"]
    assert len(warns) == 1 and "alt-text" in warns[0]


def test_empty_media_is_never_uploaded():
    v11 = _V11()
    xc = client(v11)
    xc.post("reveal", media=b"")
    assert v11.uploads == [] and xc._client.calls == [{"text": "reveal", "user_auth": True}]
    assert xc._upload_media(b"") is None


def test_post_without_media_is_unchanged():
    v11 = _V11()
    xc = client(v11)
    xc.post("plain")
    assert v11.uploads == []
    assert xc._client.calls == [{"text": "plain", "user_auth": True}]


def test_upload_failure_still_posts_the_text_and_warns_the_operator():
    """Opus: a print on Railway is invisible — the degradation goes to the
    operator's channel (main.py wires notifier.notify as `warn`)."""
    warns = []
    xc = client(_V11(fail=True), warns)
    assert xc.post("reveal", media=PNG) == "777"
    assert xc._client.calls == [{"text": "reveal", "user_auth": True}]
    assert len(warns) == 1 and "media upload failed" in warns[0] and "RuntimeError" in warns[0]


def test_default_warn_prints_when_no_notifier(capsys):
    xc = client(_V11(fail=True))
    xc.post("reveal", media=PNG)
    assert "media upload failed" in capsys.readouterr().out


def test_publisher_forwards_media():
    class _X:
        def __init__(self):
            self.kw = None

        def post(self, text, *, long_post=False, media=None, media_alt=None):
            self.kw = (text, long_post, media, media_alt)
            return "1"
    x = _X()
    XPublisher(x).post("t", long_post=True, media=PNG, media_alt="alt")
    assert x.kw == ("t", True, PNG, "alt")


@pytest.mark.parametrize("head,ext", [
    (PNG, ".png"), (b"\xff\xd8\xff\xe0", ".jpg"), (b"GIF89a", ".gif"),
    (b"RIFF\x00\x00\x00\x00WEBP", ".webp"), (b"<html>", ".png"),
])
def test_image_ext_from_magic_bytes(head, ext):
    assert _image_ext(head) == ext


def test_artwork_transport_refuses_redirects_and_caps_the_read():
    """main._http_get_artwork: a 302 from the gateway is refused (the wiring
    validated the URL it built, not where a redirect would go); the body is
    read to the cap + 1 only, so size is refused, never downloaded whole."""
    import http.server
    import threading

    from finding_memeland.main import _http_get_artwork
    from finding_memeland.target.wiring import MAX_ARTWORK_BYTES

    class H(http.server.BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def do_GET(self):
            if self.path == "/hop":
                self.send_response(302)
                self.send_header("Location", "http://127.0.0.1:1/elsewhere")
                self.end_headers()
                return
            self.send_response(200)
            self.send_header("Content-Type", "application/octet-stream")
            self.end_headers()
            if self.path == "/huge":
                self.wfile.write(PNG + b"\x00" * (MAX_ARTWORK_BYTES + 64))
            else:
                self.wfile.write(PNG)
    srv = http.server.HTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{srv.server_port}"
    try:
        assert _http_get_artwork(f"{base}/ok") == PNG
        with pytest.raises(Exception) as e:
            _http_get_artwork(f"{base}/hop")
        assert "redirect" in str(e.value).lower()
        assert len(_http_get_artwork(f"{base}/huge")) == MAX_ARTWORK_BYTES + 1
    finally:
        srv.shutdown()
