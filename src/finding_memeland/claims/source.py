"""Claim source adapter — turns X mentions into ClaimPosts.

Implements the Orchestrator's claim-channel port:

    poll(since_id)                    -> list[ClaimPost]   (mentions timeline)
    sweep(conversation_id, since_id)  -> list[ClaimPost]   (search backstop)
    has_reshared(user_id, post_id)    -> bool              (repost OR quote)
    lookup_profile(user_id)           -> dict              (bot screen)

Why mentions: a reply to the Clue 1 post auto-mentions @FindingMemeland, and
GET /2/users/:id/mentions is an OWNED READ ($0.001/resource, deduplicated per
24h UTC day) with a healthy rate limit — the exact opposite of the DM endpoints.

Why the sweep: a reply-to-a-reply inside the Clue 1 thread does NOT mention us
and is invisible to the mentions timeline. The public rule says "reply to THIS
post", but the conversation_id search catches strays in the thread anyway —
run every N cycles by the orchestrator (regular reads, deduped daily).
"""

from __future__ import annotations

from .parser import ClaimPost


def _to_posts(rows) -> list[ClaimPost]:
    return [
        ClaimPost(
            tweet_id=r["tweet_id"],
            author_id=r["author_id"],
            author_handle=r.get("author_handle", ""),
            text=r.get("text", ""),
            created_at=r["created_at"],
            conversation_id=r.get("conversation_id"),
            replied_to_id=r.get("replied_to_id"),
        )
        for r in rows
    ]


class XClaimSource:
    def __init__(self, x_client):
        self._x = x_client

    def poll(self, since_id: str | None) -> list[ClaimPost]:
        return _to_posts(self._x.read_mentions(since_id=since_id))

    def sweep(self, conversation_id: str, since_id: str | None) -> list[ClaimPost]:
        return _to_posts(
            self._x.search_conversation(conversation_id, since_id=since_id)
        )

    def has_reshared(self, user_id: str, post_id: str) -> bool:
        return self._x.has_reshared(user_id=user_id, post_id=post_id)

    def lookup_profile(self, user_id: str) -> dict:
        return self._x.lookup_user(user_id)
