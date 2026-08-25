"""Relic artwork: image model -> IPFS pin -> `ipfs://<cid>`.

Implements the `RelicImageGen` port from relic_mint.py, which until now had no
real implementation at all.

Why IPFS and not a URL we host: the image is part of the NFT's metadata and has
to outlive us. A relic whose art 404s in two years is a broken collectible, and
the whole point of moving off X personas was to stop depending on someone else's
platform staying up.

Two failure rules, both deliberate and both the opposite of the avatar pipeline's:

1. NO SILENT FALLBACK. `avatar.py` returns b"" when the image model refuses, and
   the hunt continues without a picture. A relic cannot do that — two of the nine
   clues describe the artwork, so a relic minted without art is a hunt with two
   dead clues, permanently, on-chain. We raise instead.
2. NO PRIVATE PINS. Pinata's v3 upload defaults to `network=private`, and a
   private CID does not resolve on any public gateway — the NFT would show a
   broken image on every marketplace, which is exactly where players have to
   recognise it. `network=public` is sent explicitly and is not configurable.
"""

from __future__ import annotations

import json
import mimetypes
import secrets
import urllib.request

PINATA_UPLOAD_URL = "https://uploads.pinata.cloud/v3/files"

# Same safety constraints as persona avatars, plus one specific to relics: text
# in the image would let players read the name off the artwork and skip the
# puzzle entirely.
RELIC_STYLE_SUFFIX = (
    ", no text, no letters, no numbers, no watermark, no logos, "
    "not a real living person, no recognizable real-person likeness, "
    "safe for work, non-sexual, no nudity"
)


class RelicImageError(RuntimeError):
    """Art could not be produced or pinned. Aborts the mint — better no relic
    than a relic with two dead clues."""


class OpenAIRelicImage:
    """image_prompt -> PNG bytes. The client is injected, like everywhere else."""

    def __init__(self, client, *, model: str = "gpt-image-1", size: str = "1024x1024"):
        self._client = client
        self._model = model
        self._size = size

    def generate_png(self, image_prompt: str) -> bytes:
        import base64

        try:
            resp = self._client.images.generate(
                model=self._model,
                prompt=image_prompt.strip() + RELIC_STYLE_SUFFIX,
                size=self._size,
                n=1,
            )
            b64 = getattr(resp.data[0], "b64_json", None)
        except Exception as e:  # noqa: BLE001
            raise RelicImageError(f"image model failed: {e!r}") from e
        if not b64:
            raise RelicImageError("image model returned no image data")
        return base64.b64decode(b64)


class PinataPinner:
    """PNG bytes -> `ipfs://<cid>` via Pinata's v3 upload endpoint.

    `http_post` is injected so tests run offline; the default does a real
    multipart POST with nothing but the standard library."""

    def __init__(self, jwt: str, *, http_post=None, url: str = PINATA_UPLOAD_URL):
        self._jwt = jwt
        self._url = url
        self._post = http_post or self._default_post

    @staticmethod
    def _multipart(fields: dict[str, str], filename: str, data: bytes) -> tuple[bytes, str]:
        """Build a multipart/form-data body. Hand-rolled to avoid adding a
        dependency for one request."""
        boundary = "----FindingMemeland" + secrets.token_hex(16)
        mime = mimetypes.guess_type(filename)[0] or "application/octet-stream"
        parts: list[bytes] = []
        for key, value in fields.items():
            parts.append(
                f"--{boundary}\r\n"
                f'Content-Disposition: form-data; name="{key}"\r\n\r\n'
                f"{value}\r\n".encode()
            )
        parts.append(
            f"--{boundary}\r\n"
            f'Content-Disposition: form-data; name="file"; filename="{filename}"\r\n'
            f"Content-Type: {mime}\r\n\r\n".encode()
        )
        parts.append(data)
        parts.append(f"\r\n--{boundary}--\r\n".encode())
        return b"".join(parts), f"multipart/form-data; boundary={boundary}"

    def _default_post(self, url: str, body: bytes, headers: dict) -> str:  # pragma: no cover - network
        req = urllib.request.Request(url, data=body, headers=headers, method="POST")
        try:
            with urllib.request.urlopen(req, timeout=120) as resp:
                return resp.read().decode("utf-8", "ignore")
        except urllib.error.HTTPError as e:
            # The body says WHY. Without it a 403 is indistinguishable between a
            # wrong scope, an expired key and Cloudflare blocking the client —
            # three different fixes (measured 2026-08-25: a bare "403 Forbidden"
            # cost a round of guessing).
            detail = e.read()[:400].decode("utf-8", "ignore").replace("\n", " ")
            raise RelicImageError(
                f"pinata HTTP {e.code}: {detail or '(empty body)'}"
            ) from e

    def pin(self, data: bytes, *, name: str = "relic.png") -> str:
        if not self._jwt:
            raise RelicImageError("PINATA_JWT not configured — cannot pin artwork")
        if not data:
            raise RelicImageError("refusing to pin an empty file")

        body, content_type = self._multipart(
            # `network=public` is NOT optional and NOT configurable: the v3
            # default is `private`, and a private CID resolves on no public
            # gateway — the NFT would show a broken image on every marketplace.
            {"network": "public", "name": name},
            name,
            data,
        )
        headers = {
            "Authorization": f"Bearer {self._jwt}",
            "Content-Type": content_type,
            "Content-Length": str(len(body)),
            # Pinata sits behind Cloudflare, which blocks the default
            # `Python-urllib/3.x` signature with a bare 403 (measured on another
            # Cloudflare-fronted API earlier the same day — a browser UA was the
            # whole fix there).
            "User-Agent": (
                "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
            ),
            "Accept": "application/json",
        }
        try:
            raw = self._post(self._url, body, headers)
            cid = (json.loads(raw or "{}").get("data") or {}).get("cid")
        except RelicImageError:
            raise                      # already carries the server's reason
        except Exception as e:  # noqa: BLE001
            raise RelicImageError(f"pinata upload failed: {e!r}") from e
        if not cid:
            raise RelicImageError(f"pinata returned no cid: {raw[:200]!r}")
        return f"ipfs://{cid}"


class RelicArtwork:
    """The `RelicImageGen` port: prompt -> pinned `ipfs://` URI."""

    def __init__(self, image_gen: OpenAIRelicImage, pinner: PinataPinner):
        self._image = image_gen
        self._pinner = pinner

    def generate(self, image_prompt: str) -> str:
        return self._pinner.pin(self._image.generate_png(image_prompt))


class FakeRelicArtwork:
    """Deterministic stub for tests and dry runs."""

    def __init__(self, uri: str = "ipfs://bafyfake"):
        self.uri = uri
        self.prompts: list[str] = []

    def generate(self, image_prompt: str) -> str:
        self.prompts.append(image_prompt)
        return self.uri
