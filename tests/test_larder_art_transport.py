"""A arte completa do /prepare tem o seu próprio transporte (30/09).

Desde 16/09 a leitura da arte inteira — a que a visão descreve — passava
pelo transporte do REVEAL (main._http_get_artwork: 10 s, corta aos 5 MB).
Uma arte maior chegava cortada e não abria. Agora:
  · lê até ao tecto da despensa (MAX_IMAGE_BYTES, 24 MB) + 1 byte, para o
    tamanho se recusar pelo tamanho, sem descarregar os 171 MB;
  · 60 s por espera de rede (IMAGE_FETCH_TIMEOUT_S) e um prazo total, para um
    gateway a pingar bytes não prender o /prepare para sempre;
  · segue redirects, como o teste da imagem;
  · o reveal fica exactamente como estava.
"""
from __future__ import annotations

import http.server
import inspect
import threading
import time

import pytest
from test_target_wiring import FakeRepo, rpc_ok, settings

from finding_memeland import main
from finding_memeland.target import wiring
from finding_memeland.target.wiring import MAX_ARTWORK_BYTES, build_target

PNG = b"\x89PNG\r\n\x1a\n"
URI = "ipfs://QmSiuJazyPgzAVqBiW3LMNdjAG4uaZFqzMwzU2kGS2KmCN"


class _Handler(http.server.BaseHTTPRequestHandler):
    size = 0

    def log_message(self, *a):
        pass

    def do_GET(self):
        try:
            if self.path == "/hop":
                self.send_response(302)
                self.send_header("Location", "/big")
                self.end_headers()
                return
            self.send_response(200)
            self.send_header("Content-Type", "application/octet-stream")
            self.end_headers()
            if self.path == "/drip":
                for _ in range(40):
                    self.wfile.write(b"\x00" * 1024)
                    self.wfile.flush()
                    time.sleep(0.05)
                return
            self.wfile.write(PNG + b"\x00" * (self.size - len(PNG)))
        except (BrokenPipeError, ConnectionResetError):
            pass                        # the client gave up — that is the test


@pytest.fixture
def server():
    srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{srv.server_port}"
    srv.shutdown()


def test_an_artwork_over_the_reveal_cap_arrives_whole(server):
    """O defeito: 6 MB pelo transporte do reveal chegavam como 5 MB + 1."""
    _Handler.size = MAX_ARTWORK_BYTES + 1024 * 1024
    got = main._http_get_larder_art(f"{server}/big")               # noqa: SLF001
    assert len(got) == _Handler.size and got.startswith(PNG)


def test_it_reads_at_most_the_larder_ceiling_plus_one(server, monkeypatch):
    monkeypatch.setattr(wiring, "MAX_IMAGE_BYTES", 256 * 1024)
    _Handler.size = 3 * 256 * 1024
    got = main._http_get_larder_art(f"{server}/big")               # noqa: SLF001
    assert len(got) == 256 * 1024 + 1


def test_it_follows_a_gateway_redirect(server):
    _Handler.size = 4096
    assert main._http_get_larder_art(f"{server}/hop").startswith(PNG)  # noqa: SLF001


def test_a_gateway_that_drips_hits_the_total_deadline(server, monkeypatch):
    monkeypatch.setattr(main, "LARDER_ART_DEADLINE_S", 0.2)
    t0 = time.monotonic()
    with pytest.raises(TimeoutError):
        main._http_get_larder_art(f"{server}/drip")                 # noqa: SLF001
    assert time.monotonic() - t0 < 1.5          # the drip would take 2 s


def test_each_network_wait_gets_the_image_timeout(monkeypatch):
    seen = {}

    class _Resp:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def read1(self, n):
            return b""

    def open_(req, timeout):
        seen["timeout"] = timeout
        return _Resp()
    monkeypatch.setattr(main._GATEWAY_OPENER, "open", open_)       # noqa: SLF001
    main._http_get_larder_art("http://gw.example/ipfs/x")           # noqa: SLF001
    assert seen["timeout"] == main.IMAGE_FETCH_TIMEOUT_S == 60


# --------------------------------------------------------------------------- #
# A composição: cada leitura com o seu transporte                              #
# --------------------------------------------------------------------------- #


def test_prepare_reads_the_artwork_through_its_own_transport_never_the_reveal_s():
    seen = []

    def larder_art(url, headers):
        seen.append(url)
        raise TimeoutError("down")

    def reveal(url, headers):
        raise AssertionError("/prepare must not use the reveal's transport")
    w = build_target(settings(), anthropic=object(), repo=FakeRepo(),
                     http_get=lambda u, h: "{}", http_post=rpc_ok,
                     http_get_bytes=lambda u, h: b"",
                     get_artwork_bytes=reveal, http_get_larder_art=larder_art)
    with pytest.raises(Exception) as e:
        w.larder_preparer._fetch_image(URI)                         # noqa: SLF001
    assert not isinstance(e.value, AssertionError)
    assert len(seen) == 3                       # our gateway + gw0 + gw1


def test_the_reveal_keeps_its_own_transport():
    def reveal(url, headers):
        return PNG + b"\x00" * 64

    def larder_art(url, headers):
        raise AssertionError("the reveal must not use the larder's transport")
    w = build_target(settings(), anthropic=object(), repo=FakeRepo(),
                     http_get=lambda u, h: "{}", http_post=rpc_ok,
                     http_get_bytes=lambda u, h: b"",
                     get_artwork_bytes=reveal, http_get_larder_art=larder_art)
    from finding_memeland.target.hunt import SealedTarget
    from finding_memeland.target.selector import Target
    t = Target(chain="ethereum", contract="0x" + "ab" * 20, token_id=1, name="x",
               name_onchain="x", description="", image=URI, metadata_sha256="m",
               epoch="e1", token_uri=URI, content_id="ipfs:x")
    sealed = SealedTarget(target=t, salt="s" * 32, commitment="c" * 64)
    assert w.fetch_artwork(sealed).startswith(PNG)


def test_production_wires_the_larder_transport():
    """O defeito de 16/09 não estava em nenhuma função — estava na ligação.
    Fica fixado aqui."""
    src = inspect.getsource(main.build_agent)
    assert "http_get_larder_art=_http_get_larder_art" in src
    assert "get_artwork_bytes=_http_get_artwork" in src      # the reveal's, untouched
