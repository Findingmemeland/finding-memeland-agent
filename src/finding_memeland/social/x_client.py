"""X (Twitter) API wrapper — single developer app, many authorized accounts.

One app (on the main account) holds OAuth tokens for the main account AND every
persona. Reads are billed per result returned; design every call to minimize
billed reads (empty inbox = free; never put URLs in posts).
"""

from __future__ import annotations


class XClient:
    def __init__(self, *, api_key: str, api_secret: str, bearer_token: str):
        self._api_key = api_key
        self._api_secret = api_secret
        self._bearer = bearer_token
        # TODO(step 23/26): init tweepy clients; cache per-account user contexts.

    # --- main account ---
    def post(self, text: str, *, as_account: str = "main", long_post: bool = False) -> str:
        """Publish a post; return the tweet id. `as_account` selects OAuth ctx.

        TODO: long_post requires X Premium (main account only).
        """
        raise NotImplementedError("post — implemented in step 24/26")

    def read_dms(self, *, since_id: str | None) -> list[dict]:
        """Inbound DMs on the main account since since_id. Empty => $0."""
        raise NotImplementedError("read_dms — implemented in step 26")

    def send_dm_reply(self, *, recipient_x_id: str, text: str) -> None:
        raise NotImplementedError("send_dm_reply — implemented in step 26")

    # --- persona accounts ---
    def update_profile(
        self, *, oauth_ref: str, name: str | None = None, bio: str | None = None
    ) -> None:
        """Edit a persona's display name / bio (v1.1 endpoints).

        TODO(scaffold week): verify v1.1 profile endpoints work in the current
        tier. Fallback = manual profile setup (keeps integrity hash, loses
        operational blindness on identity).
        """
        raise NotImplementedError("update_profile — verify v1.1, step 23")

    def set_avatar(self, *, oauth_ref: str, image_png: bytes) -> None:
        raise NotImplementedError("set_avatar — implemented in step 23")

    # --- reads used by the validator ---
    def has_reshared(self, *, user_id: str, post_id: str) -> bool:
        """Whether user_id retweeted or quote-tweeted post_id (PAID read)."""
        raise NotImplementedError("has_reshared — implemented in step 26")

    def get_profile(self, user_id: str) -> dict:
        """Profile fields used by bot defences (name, bio, automated label, age)."""
        raise NotImplementedError("get_profile — implemented in step 26")
