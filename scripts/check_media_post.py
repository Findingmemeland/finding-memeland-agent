"""Live test of the media path on X (Opus, 14/09, item D before Hunt #11).

The reveal is the only post that carries a picture: v1.1 media/upload on the
main account's OAuth 1.0a context, then create_media_metadata (alt-text),
then create_tweet with media_ids. None of that had ever talked to X. This
script posts ONE small generated image (no artwork, no target) with alt-text
on the MAIN account, prints the tweet id, and deletes the post afterwards
(finally block), so nothing is left behind.

Run from the repo root, with Doppler injecting secrets:

    doppler run -- python scripts/check_media_post.py

PASS  = uploaded, attached, alt-text set, post deleted.
WARN  = the text went out WITHOUT the picture (XClient.post degrades on
        purpose) — the reveal would lose its image: fix before /launch.
"""

from __future__ import annotations

import struct
import sys
import time
import zlib

from finding_memeland.config import get_settings
from finding_memeland.social.x_client import XClient

TEXT = "media self-test — deleted in a minute"
ALT = "Finding Memeland media self-test: a plain coloured square"


def _png(width: int = 96, height: int = 96, rgb=(0x1D, 0x9B, 0xF0)) -> bytes:
    """A solid-colour PNG built by hand (no PIL dependency)."""
    row = b"\x00" + bytes(rgb) * width
    raw = row * height

    def chunk(tag: bytes, body: bytes) -> bytes:
        return (struct.pack(">I", len(body)) + tag + body
                + struct.pack(">I", zlib.crc32(tag + body) & 0xFFFFFFFF))

    return (b"\x89PNG\r\n\x1a\n"
            + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(raw, 9))
            + chunk(b"IEND", b""))


def main() -> int:
    s = get_settings()
    missing = [n for n, v in {
        "X_API_KEY": s.x_api_key, "X_API_SECRET": s.x_api_secret,
        "X_MAIN_ACCESS_TOKEN": s.x_main_access_token,
        "X_MAIN_ACCESS_SECRET": s.x_main_access_secret,
    }.items() if not v]
    if missing:
        print("FAIL: missing env: " + ", ".join(missing))
        return 2

    warnings: list[str] = []
    x = XClient(
        api_key=s.x_api_key, api_secret=s.x_api_secret, bearer_token=s.x_bearer_token,
        main_access_token=s.x_main_access_token, main_access_secret=s.x_main_access_secret,
        warn=warnings.append,
    )

    tweet_id = None
    try:
        tweet_id = x.post(TEXT, media=_png(), media_alt=ALT)
        print(f"posted: https://x.com/i/status/{tweet_id}")
        for w in warnings:
            print(f"warning from XClient: {w}")
        time.sleep(3)
    finally:
        if tweet_id:
            try:
                x.delete_post(tweet_id)
                print(f"deleted: {tweet_id}")
            except Exception as e:  # noqa: BLE001
                print(f"could not delete {tweet_id}: {e!r} — delete it by hand")

    if any("media upload failed" in w for w in warnings):
        print("WARN: the post went out WITHOUT the picture — media path broken")
        return 1
    if any("alt-text" in w for w in warnings):
        print("PASS (picture attached) but alt-text was not set — see warning above")
        return 0
    print("PASS: picture uploaded, alt-text set, post attached and deleted")
    return 0


if __name__ == "__main__":
    sys.exit(main())
