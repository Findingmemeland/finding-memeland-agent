"""Pre-hunt findability check.

Before a hunt goes live, confirm the persona is actually LOCATABLE: search recent
tweets for the persona's distinctive locator phrase and check the persona's post
shows up. If it doesn't, the account isn't indexed/findable yet — delay the hunt.

This directly guards against the failure Pedro hit in testing (a generic persona
that no search could surface).

    python scripts/check_findability.py "<distinctive phrase from findable_post>" [persona_handle]
"""

from __future__ import annotations

import sys

from finding_memeland.config import get_settings
from finding_memeland.social.x_client import XClient


def main() -> int:
    if len(sys.argv) < 2:
        print('usage: check_findability.py "<phrase>" [persona_handle]')
        return 2
    phrase = sys.argv[1]
    persona_handle = sys.argv[2].lstrip("@").lower() if len(sys.argv) > 2 else None

    s = get_settings()
    x = XClient(
        api_key=s.x_api_key, api_secret=s.x_api_secret,
        main_access_token=s.x_main_access_token, main_access_secret=s.x_main_access_secret,
    )
    try:
        hits = x.search_recent(f'"{phrase}"', max_results=20)
    except Exception as e:  # noqa: BLE001
        print(f"FAIL — recent search error: {e!r}")
        return 1

    print(f"{len(hits)} recent tweet(s) match the phrase:")
    for h in hits:
        print(f"  - tweet {h['tweet_id']} by author {h['author_id']}: {h['text'][:80]}")

    if not hits:
        print("\nNOT FINDABLE yet — the locator post doesn't surface. Warm/wait or "
              "make the phrase more distinctive before launching this hunt.")
        return 1
    print("\nFINDABLE — the locator phrase surfaces in search. Good to launch.")
    if persona_handle:
        print(f"(Confirm one of the hits is the persona @{persona_handle}.)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
