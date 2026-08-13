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

import functools
import time
from dataclasses import dataclass

import tweepy

from .x_text import MAX_BIO_LEN, MAX_NAME_LEN

# Every HTTP call gets a hard deadline. tweepy.Client exposes no timeout and
# `requests` defaults to NONE — a single hung socket froze the Genesis hunt
# loop silently (post-mortem P0, candidate #2): no exception, no log, /status
# still 'LIVE'. With a timeout the call raises, the loop's error handling
# notifies ("DM poll failed ...Timeout..."), backs off and retries.
_HTTP_TIMEOUT_S = 30


def _with_timeout(client: tweepy.Client) -> tweepy.Client:
    """Bind the timeout at the session level so EVERY v2 call inherits it."""
    client.session.request = functools.partial(
        client.session.request, timeout=_HTTP_TIMEOUT_S
    )
    return client

# How many of a user's recent tweets to scan when checking for a reshare.
_RESHARE_SCAN = 100
_DM_FETCH = 50
# Pagination cap per poll: 10 pages x 50 = 500 DMs. Bounds the cost of a viral
# spike while never silently dropping the oldest (= first-arrived) submissions.
_DM_MAX_PAGES = 10
# Mentions timeline (claim-by-post): 100/page, 5 pages = 500 mentions per poll.
# Owned read ($0.001/resource, deduped per 24h UTC day) — polling frequency does
# not multiply cost; only UNIQUE mentions are billed.
_MENTIONS_FETCH = 100
_MENTIONS_MAX_PAGES = 10
_TWEET_FIELDS = ["author_id", "created_at", "conversation_id", "referenced_tweets"]


def _retry_server_error(fn, *, tries: int = 3, delay: float = 4.0):
    """Retry a call on transient X 5xx errors. The v1.1 profile endpoints and
    create_tweet are known to be flaky (e.g. '131 - Internal error'); a short
    retry rides over the hiccup. Re-raises the last error after `tries` attempts."""
    last = None
    for attempt in range(tries):
        try:
            return fn()
        except tweepy.errors.TwitterServerError as e:
            last = e
            if attempt < tries - 1:
                time.sleep(delay)
    raise last


def _arrival_key(d: dict) -> tuple:
    """created_at first, snowflake id as tiebreak — guarded so one malformed
    id can never blind every subsequent poll."""
    tid = d["tweet_id"]
    return (d["created_at"], int(tid) if str(tid).isdigit() else 0)


@dataclass(frozen=True)
class Profile:
    user_id: str
    screen_name: str
    name: str
    description: str
    # R3 (pre-dressing): the avatar can't be verified byte-for-byte (X
    # re-encodes images), so launch verification checks EXISTENCE only —
    # a dressed persona must not be showing X's default egg.
    has_custom_avatar: bool = True


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
        self._self_user_id: str | None = None  # main account id, cached lazily
        if main_access_token and main_access_secret:
            self._client = _with_timeout(tweepy.Client(
                consumer_key=api_key,
                consumer_secret=api_secret,
                access_token=main_access_token,
                access_token_secret=main_access_secret,
            ))

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
        return tweepy.API(auth, timeout=_HTTP_TIMEOUT_S)

    def get_profile(self, access_token: str, access_secret: str) -> Profile:
        me = self._api_for(access_token, access_secret).verify_credentials(
            skip_status=True, include_entities=False
        )
        return Profile(
            user_id=me.id_str, screen_name=me.screen_name,
            name=me.name or "", description=getattr(me, "description", "") or "",
            # v1.1 exposes this directly: True = the account still shows X's
            # default avatar (never a dressed persona).
            has_custom_avatar=not bool(getattr(me, "default_profile_image", False)),
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
        u = _retry_server_error(
            lambda: self._api_for(access_token, access_secret).update_profile(**kwargs)
        )
        return Profile(
            user_id=u.id_str, screen_name=u.screen_name,
            name=u.name or "", description=getattr(u, "description", "") or "",
        )

    def set_avatar(self, access_token: str, access_secret: str, image_path: str) -> None:
        _retry_server_error(
            lambda: self._api_for(access_token, access_secret).update_profile_image(image_path)
        )

    def set_banner(self, access_token: str, access_secret: str, image_path: str) -> None:
        _retry_server_error(
            lambda: self._api_for(access_token, access_secret).update_profile_banner(image_path)
        )

    def _persona_v2(self, access_token: str, access_secret: str) -> tweepy.Client:
        """A v2 client in a PERSONA's OAuth context — with the same hard HTTP
        timeout as the main client."""
        return _with_timeout(tweepy.Client(
            consumer_key=self._api_key, consumer_secret=self._api_secret,
            access_token=access_token, access_token_secret=access_secret,
        ))

    def post_as_persona(self, access_token: str, access_secret: str, text: str) -> str:
        """Post from a PERSONA account (its own OAuth context). Used to publish the
        findable locator post so it becomes searchable."""
        client = self._persona_v2(access_token, access_secret)
        resp = _retry_server_error(lambda: client.create_tweet(text=text, user_auth=True))
        return str(resp.data["id"])

    def delete_as_persona(self, access_token: str, access_secret: str, tweet_id: str) -> None:
        """Delete a post from a PERSONA account (used by the live-test cleanup)."""
        self._persona_v2(access_token, access_secret).delete_tweet(
            id=tweet_id, user_auth=True
        )

    def get_persona_post(
        self, access_token: str, access_secret: str, tweet_id: str
    ) -> dict | None:
        """Fetch ONE post in the PERSONA's own OAuth context (R3 launch
        verification: does the locator post still exist?). Returns
        {'id', 'text'} or None if the post is gone/inaccessible."""
        client = self._persona_v2(access_token, access_secret)
        resp = _retry_server_error(
            lambda: client.get_tweet(tweet_id, user_auth=True)
        )
        if resp is None or resp.data is None:
            return None
        return {"id": str(resp.data.id), "text": resp.data.text or ""}

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
    def _self_id(self) -> str:
        """The main account's own user id, fetched once and cached. Used to drop
        our own sent DMs from read_dms — the v2 dm_events endpoint returns BOTH
        directions, and without this filter every canned reply we send comes back
        on the next poll as a fake 'submission' (self-DM echo loop)."""
        if self._self_user_id is None:
            resp = self._v2().get_me(user_auth=True)
            self._self_user_id = str(resp.data.id)
        return self._self_user_id

    def read_dms(self, *, since_id: str | None = None) -> list[dict]:
        """Inbound DM messages on the main account, ascending by time. Each item:
        {dm_id, sender_x_id, sender_handle, text, created_at}. Empty => $0.
        Events SENT by the main account itself are filtered out.

        ⚠️ KNOWN X BUG (Hunt #2 post-mortem, confirmed in production logs +
        devcommunity /t/254508): this account-level endpoint STOPS delivering
        subsequent inbound events of a conversation after we reply in it. Use
        it for DISCOVERY of new conversations only; follow-ups must be read
        with read_conversation_dms (per-conversation endpoint, reliable).

        PAGINATED: a viral spike can bring >50 DMs between polls; without
        pagination the oldest — the true first-arrived submissions — would be
        silently dropped. We keep fetching pages until we reach already-seen
        events (since_id), run out, or hit the page cap."""
        return self._read_events(since_id=since_id)

    def read_conversation_dms(
        self, participant_id: str, *, since_id: str | None = None
    ) -> list[dict]:
        """Inbound DMs of ONE 1-1 conversation (dm_conversations/with/:id) —
        the reliable path for follow-ups the account-level endpoint suppresses.
        Rate limit: 15/15min per user for THIS endpoint, shared across ALL
        participant_ids — the orchestrator budgets 1 call per poll cycle."""
        return self._read_events(since_id=since_id, participant_id=participant_id)

    def _read_events(
        self, *, since_id: str | None = None, participant_id: str | None = None
    ) -> list[dict]:
        me: str | None = None
        out: list[dict] = []
        page_token: str | None = None
        # Per-poll flight recorder (post-mortem P0): a healthy-but-empty inbox
        # and an API that stopped returning the inbox used to produce IDENTICAL
        # logs — none. One line per poll makes the difference visible.
        pages = raw = non_msg = self_skipped = since_skipped = 0
        for _ in range(_DM_MAX_PAGES):
            kwargs = dict(
                max_results=_DM_FETCH,
                dm_event_fields=["id", "text", "created_at", "sender_id", "event_type"],
                expansions=["sender_id"],
                user_auth=True,
            )
            if participant_id is not None:
                kwargs["participant_id"] = participant_id
            if page_token:
                kwargs["pagination_token"] = page_token
            resp = self._v2().get_direct_message_events(**kwargs)
            events = resp.data or []
            pages += 1
            raw += len(events)
            users = {}
            if resp.includes and resp.includes.get("users"):
                users = {u.id: u for u in resp.includes["users"]}
            if events and me is None:
                # Pay for the get_me lookup once, only if there's anything to filter.
                me = self._self_id()
            reached_seen = False
            for ev in events:
                if getattr(ev, "event_type", None) != "MessageCreate":
                    non_msg += 1
                    continue
                if since_id is not None and int(ev.id) <= int(since_id):
                    reached_seen = True  # older than the marker: page limit found
                    since_skipped += 1
                    continue
                if me is not None and str(getattr(ev, "sender_id", "")) == me:
                    self_skipped += 1
                    continue  # our own outbound reply — not a submission
                sender = users.get(getattr(ev, "sender_id", None))
                out.append({
                    "dm_id": str(ev.id),
                    "sender_x_id": str(getattr(ev, "sender_id", "")),
                    "sender_handle": sender.username if sender else "",
                    "text": getattr(ev, "text", "") or "",
                    "created_at": ev.created_at,
                })
            meta = getattr(resp, "meta", None) or {}
            page_token = meta.get("next_token")
            if reached_seen or not page_token:
                break
        out.sort(key=lambda d: d["created_at"])
        scope = f"conv={participant_id} " if participant_id else ""
        print(
            f"[dm-poll] {scope}pages={pages} raw={raw} non_message={non_msg} "
            f"self={self_skipped} since_skipped={since_skipped} "
            f"returned={len(out)} since={since_id or 'start'}"
        )
        return out

    # ------------------------------------------------------------------
    # Claim-by-post reads (2026-07-25): the DM endpoints only deliver "virgin"
    # conversations, so submissions moved to public replies on the Clue 1 post.
    # ------------------------------------------------------------------
    def _tweet_rows(self, tweets, includes, me: str) -> list[dict]:
        users = {}
        if includes and includes.get("users"):
            users = {str(u.id): u for u in includes["users"]}
        rows: list[dict] = []
        for t in tweets or []:
            author = str(getattr(t, "author_id", "") or "")
            if author == me:
                continue  # our own posts/replies are not submissions
            replied_to = None
            for ref in (getattr(t, "referenced_tweets", None) or []):
                if getattr(ref, "type", "") == "replied_to":
                    replied_to = str(ref.id)
                    break
            u = users.get(author)
            rows.append({
                "tweet_id": str(t.id),
                "author_id": author,
                "author_handle": (u.username if u else ""),
                "text": getattr(t, "text", "") or "",
                "created_at": t.created_at,
                "conversation_id": str(getattr(t, "conversation_id", "") or "") or None,
                "replied_to_id": replied_to,
            })
        return rows

    def read_mentions(self, *, since_id: str | None = None) -> list[dict]:
        """Posts mentioning the main account (which includes every reply to our
        posts), ascending by (created_at, id). Owned read — the cheap, healthy
        endpoint the claim channel is built on. Paginated with a cap, like
        read_dms: a viral spike must never silently drop the oldest replies."""
        me = self._self_id()
        out: list[dict] = []
        page_token: str | None = None
        pages = raw = 0
        for _ in range(_MENTIONS_MAX_PAGES):
            kwargs = dict(
                id=me, max_results=_MENTIONS_FETCH,
                tweet_fields=_TWEET_FIELDS, expansions=["author_id"],
                user_auth=True,
            )
            if since_id:
                kwargs["since_id"] = since_id
            if page_token:
                kwargs["pagination_token"] = page_token
            resp = self._v2().get_users_mentions(**kwargs)
            tweets = resp.data or []
            pages += 1
            raw += len(tweets)
            out += self._tweet_rows(tweets, resp.includes, me)
            meta = getattr(resp, "meta", None) or {}
            page_token = meta.get("next_token")
            if not page_token:
                break
        out.sort(key=_arrival_key)
        # Flight recorder (post-mortem doctrine): one line per poll, so a
        # healthy-but-quiet thread and a broken read never look identical.
        # TRUNCATED means the page cap was hit with more pages available: the
        # OLDEST (= first-arrived) mentions were NOT fetched this poll and the
        # marker must not be trusted to have covered them — operator alert.
        print(
            f"[claim-poll] pages={pages} raw={raw} returned={len(out)} "
            f"since={since_id or 'start'}"
            + (" TRUNCATED" if page_token else "")
        )
        return out

    def search_conversation(
        self, conversation_id: str, *, since_id: str | None = None
    ) -> list[dict]:
        """Backstop sweep: EVERY post in the Clue 1 thread, including
        replies-to-replies that don't mention us (invisible to the mentions
        timeline). Regular read ($0.005/resource) — the orchestrator runs it
        every N cycles, and the 24h dedup makes repeats near-free. Single page
        on purpose: the sweep is a safety net, not the primary stream."""
        me = self._self_id()
        kwargs = dict(
            query=f"conversation_id:{conversation_id}",
            max_results=_MENTIONS_FETCH,
            tweet_fields=_TWEET_FIELDS, expansions=["author_id"],
            user_auth=True,
        )
        if since_id:
            kwargs["since_id"] = since_id
        resp = self._v2().search_recent_tweets(**kwargs)
        out = self._tweet_rows(resp.data or [], resp.includes, me)
        out.sort(key=_arrival_key)
        print(
            f"[claim-sweep] conv={conversation_id} raw={len(resp.data or [])} "
            f"returned={len(out)} since={since_id or 'start'}"
        )
        return out

    def reply_post(self, text: str, *, in_reply_to: str) -> str:
        """Public reply on the main account (taunts, system messages, the
        winner ask-wallet). Returns the reply's tweet id."""
        resp = _retry_server_error(lambda: self._v2().create_tweet(
            text=text, in_reply_to_tweet_id=in_reply_to, user_auth=True
        ))
        return str(resp.data["id"])

    def post(self, text: str, *, long_post: bool = False) -> str:
        """Publish on the main account; returns the tweet id. Long posts require
        X Premium on the account (no extra param needed)."""
        resp = _retry_server_error(lambda: self._v2().create_tweet(text=text, user_auth=True))
        return str(resp.data["id"])

    def delete_post(self, tweet_id: str) -> None:
        """Delete a post on the main account (used by the live-test cleanup)."""
        self._v2().delete_tweet(id=tweet_id, user_auth=True)

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

    def verify(self) -> str:
        """Cheap auth check for the main account (used by pre-flight). Returns the
        handle; raises if the key is invalid / account unreachable."""
        resp = self._v2().get_me(user_auth=True)
        return getattr(resp.data, "username", "") or ""

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
