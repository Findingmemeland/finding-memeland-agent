"""Avatar generation via an image model (OpenAI gpt-image).

The image client is injected (like the Anthropic client for text), so this
module imports no SDK and stays easy to test. The Persona Generator produces an
`avatar_prompt`; this turns it into PNG bytes for the Persona Dresser to upload.

Safety: avatars must never depict a real person's likeness or contain text /
watermarks (text on an avatar reads as botty). We append style constraints to
every prompt to enforce that.
"""

from __future__ import annotations

import base64

_SAFETY = ", no text, no watermark, no logos, not a real living person, no recognizable real-person likeness"
AVATAR_STYLE_SUFFIX = ", profile picture composition" + _SAFETY
BANNER_STYLE_SUFFIX = ", wide header banner composition" + _SAFETY
BANNER_SIZE = "1536x1024"  # closest wide size to X's 3:1 banner; X crops to fit


class AvatarGenerator:
    def __init__(self, client, *, model: str = "gpt-image-1", size: str = "1024x1024"):
        self._client = client
        self._model = model
        self._size = size

    def _generate(self, prompt: str, suffix: str, size: str) -> bytes:
        resp = self._client.images.generate(
            model=self._model, prompt=prompt.strip() + suffix, size=size, n=1
        )
        b64 = getattr(resp.data[0], "b64_json", None)
        if not b64:
            raise RuntimeError(
                "image API returned no b64_json — check the model id / response format"
            )
        return base64.b64decode(b64)

    def generate_png(self, avatar_prompt: str) -> bytes:
        """Generate the profile picture and return PNG bytes."""
        return self._generate(avatar_prompt, AVATAR_STYLE_SUFFIX, self._size)

    def generate_banner_png(self, banner_prompt: str) -> bytes:
        """Generate the header banner and return PNG bytes (wide)."""
        return self._generate(banner_prompt, BANNER_STYLE_SUFFIX, BANNER_SIZE)


def save_png(data: bytes, path: str) -> str:
    """Write PNG bytes to a file; return the path."""
    with open(path, "wb") as fh:
        fh.write(data)
    return path
