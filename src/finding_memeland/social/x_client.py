"""X (Twitter) API wrapper — single developer app, many authorized accounts.

Two surfaces:
- OAuth 1.0a API (v1.1) for PERSONA profile read/write (validated working).
- v2 Client (tweepy.Client) for MAIN-account ops: read DMs, post, reply to DMs,
  reshare lookup, user lookup.

Reads are billed per result returned; design every call to minimize billed reads
(empty inbox = free; never put URLs in posts).

The v2 methods (post/reply/has_reshared/lookup_user) are implemented but still
need a live spike to confirm shape/cost in our tier — only DM reading has been
confirmed so far (see scripts/spike_dm_read.py).
"""

from __future__ import annotations

from dataclasses import dataclass

import tweepy

from .x_text import MAX_BIO_LEN, MAX_NAME_LEN

# How many of a user's recent tweets to scan when checking for a reshare.
_RESHARE_SCAN = 100
_DM_FETCH = 50


@dataclass(frozen=True)
class Profile:
    user_id: str
    screen_name: str
    name: str
    description: str


class XClient:
    def __init__(
        self,
        *,
        api_key: str,
        api_secret: str,
        bearer_token: str = "",
        main_access_token: str = "",
        main_access_secret: str = "",
    ):
        self._api_key = api_key
        self._api_secret = api_secret
        self._bearer = bearer_token
        self._main_token = main_access_token
        self._main_secret = main_access_secret
        self._client: tweepy.Client | None = None
        if main_access_token and main_access_secret:
            self._client = tweepy.Client(
                consumer_key=api_key,
                consumer_secret=api_secret,
                access_token=main_access_token,
                access_token_secret=main_access_secret,
            )

    def _v2(self) -> tweepy.Client:
        if self._client is None:
            raise RuntimeError("v2 client needs main_access_token/secret")
        return self._client

    # ------------------------------------------------------------------
    # OAuth 1.0a user context (v1.1) — persona profile read/write
    # ------------------------------------------------------------------
    def _api_for(self, access_token: str, access_secret: str) -> tweepy.API:
        auth = tweepy.OAuth1UserHandler(
            self._api_key, self._api_secret, access_token, access_secret
        )
        return tweepy.API(auth)

    def get_profile(self, access_token: str, access_secret: str) -> Profile:
        me = self._api_for(access_token, access_secret).verify_credentials(
            skip_status=True, include_entities=False
        )
        return Profile(
            user_id=me.id_str, screen_name=me.screen_name,
            name=me.name or "", description=getattr(me, "description", "") or "",
        )

    def update_profile(self, access_token, access_secret, *, name=None, description=None) -> Profile:
        if name is not None and len(name) > MAX_NAME_LEN:
            raise ValueError(f"name exceeds {MAX_NAME_LEN} chars")
        if description is not None and len(description) > MAX_BIO_LEN:
            raise ValueError(f"bio exceeds {MAX_BIO_LEN} chars")
        kwargs = {}
        if name is not None:
            kwargs["name"] = name
        if description is not None:
            kwargs["description"] = description
        if not kwargs:
            raise ValueError("nothing to update")
        u = self._api_for(access_token, access_secret).update_profile(**kwargs)
        return Profile(
            user_id=u.id_str, screen_name=u.screen_name,
            name=u.name or "", description=getattr(u, "description", "") or "",
        )

    def set_avatar(self, access_token: str, access_secret: str, image_path: str) -> None:
        self._api_for(access_token, access_secret).update_profile_image(image_path)

    def set_banner(self, access_token: str, access_secret: str, image_path: str) -> None:
        self._api_for(access_token, access_secret).update_profile_banner(image_path)

    def post_as_persona(self, access_token: str, access_secret: str, text: str) -> str:
        """Post from a PERSONA account (its own OAuth context). Used to publish the
        findable locator post so it becomes searchable."""
        client = tweepy.Client(
            consumer_key=self._api_key, consumer_secret=self._api_secret,
            access_token=access_token, access_token_secret=access_secret,
        )
        resp = client.create_tweet(text=text, user_auth=True)
        return str(resp.data["id"])

    def search_recent(self, query: str, *, max_results: int = 10) -> list[dict]:
        """Search recent tweets (v2) — used for the pre-hunt findability check:
        does the persona's locator post actually surface for a given phrase?"""
        resp = self._v2().search_recent_tweets(
            query=query, max_results=max_results,
            tweet_fields=["author_id", "created_at"], user_auth=True,
        )
        return [
            {"tweet_id": str(t.id), "author_id": str(getattr(t, "author_id", "")), "text": t.text}
            for t in (resp.data or [])
        ]

    # ------------------------------------------------------------------
    # v2 — main-account operations
    # ------------------------------------------------------------------
    def read_dms(self, *, since_id: str | None = None) -> list[dict]:
        """Inbound DM messages on the main account, ascending by time. Each item:
        {dm_id, sender_x_id, sender_handle, text, created_at}. Empty => $0."""
        resp = self._v2().get_direct_message_events(
            max_results=_DM_FETCH,
            dm_event_fields=["id", "text", "created_at", "sender_id", "event_type"],
            expansions=["sender_id"],
            user_auth=True,
        )
        events = resp.data or []
        users = {}
        if resp.includes and resp.includes.get("users"):
            users = {u.id: u for u in resp.includes["users"]}
        out: list[dict] = []
        for ev in events:
            if getattr(ev, "event_type", None) != "MessageCreate":
                continue
            if since_id is not None and int(ev.id) <= int(since_id):
                continue
            sender = users.get(getattr(ev, "sender_id", None))
            out.append({
                "dm_id": str(ev.id),
                "sender_x_id": str(getattr(ev, "sender_id", "")),
                "sender_handle": sender.username if sender else "",
                "text": getattr(ev, "text", "") or "",
                "created_at": ev.created_at,
            })
        out.sort(key=lambda d: d["created_at"])
        return out

    def post(self, text: str, *, long_post: bool = False) -> str:
        """Publish on the main account; returns the tweet id. Long posts require
        X Premium on the account (no extra param needed)."""
        resp = self._v2().create_tweet(text=text, user_auth=True)
        return str(resp.data["id"])

    def reply_dm(self, recipient_x_id: str, text: str) -> None:
        self._v2().create_direct_message(participant_id=recipient_x_id, text=text, user_auth=True)

    def has_reshared(self, *, user_id: str, post_id: str) -> bool:
        """Whether user_id retweeted or quote-tweeted post_id, by scanning their
        recent tweets (cheaper than fetching all resharers). Limitation: only
        recent tweets are scanned."""
        resp = self._v2().get_users_tweets(
            id=user_id, max_results=_RESHARE_SCAN,
            tweet_fields=["referenced_tweets"], user_auth=True,
        )
        for t in resp.data or []:
            for ref in (getattr(t, "referenced_tweets", None) or []):
                if str(ref.id) == str(post_id) and ref.type in ("retweeted", "quoted"):
                    return True
        return False

    def lookup_user(self, user_id: str) -> dict:
        """Profile fields for the bot screen. NOTE: X's 'Automated' label is not
        reliably exposed in v2, so `automated` defaults to False and ambiguous
        cases fall to manual review."""
        resp = self._v2().get_user(
            id=user_id, user_fields=["name", "username", "description"], user_auth=True
        )
        u = resp.data
        return {
            "name": getattr(u, "name", "") or "",
            "handle": getattr(u, "username", "") or "",
            "bio": getattr(u, "description", "") or "",
            "automated": False,
        }
