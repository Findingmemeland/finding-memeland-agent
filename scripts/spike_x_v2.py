"""Spike — confirm the remaining X v2 methods work in our tier.

Validates post, reply_dm, lookup_user and (optionally) has_reshared, each in
isolation so one failure doesn't hide the others. Side effects are minimised:
  • the test tweet is auto-DELETED right after posting,
  • the test DM reply goes to whoever sent the most recent DM (your test account).

    python scripts/spike_x_v2.py                      # post/reply/lookup
    python scripts/spike_x_v2.py <username> <post_id>  # also test has_reshared

For the has_reshared TRUE path: from <username>, retweet or quote a tweet, then
pass that username and the tweet id.
"""

from __future__ import annotations

import sys
import time

import tweepy

from finding_memeland.config import get_settings
from finding_memeland.social.x_client import XClient


def _run(label: str, fn):
    try:
        out = fn()
        print(f"PASS  {label}: {out}")
    except Exception as e:  # noqa: BLE001
        print(f"FAIL  {label}: {e!r}")


def main() -> int:
    s = get_settings()
    if not (s.x_api_key and s.x_main_access_token):
        print("FAIL — X keys missing in .env.")
        return 2

    x = XClient(
        api_key=s.x_api_key, api_secret=s.x_api_secret,
        main_access_token=s.x_main_access_token, main_access_secret=s.x_main_access_secret,
    )
    raw = tweepy.Client(
        consumer_key=s.x_api_key, consumer_secret=s.x_api_secret,
        access_token=s.x_main_access_token, access_token_secret=s.x_main_access_secret,
    )

    # Use the latest inbound DM to exercise lookup_user + reply_dm realistically.
    dms = x.read_dms()
    sender_id = dms[-1]["sender_x_id"] if dms else None
    sender_handle = dms[-1]["sender_handle"] if dms else None

    if sender_id:
        _run("lookup_user", lambda: x.lookup_user(sender_id))
        _run(
            f"reply_dm (-> @{sender_handle})",
            lambda: x.reply_dm(sender_id, "FMML self-test reply — please ignore 🙂") or "sent",
        )
    else:
        print("SKIP  lookup_user/reply_dm — no DMs in the inbox to use as a target.")

    # post + delete, always cleaning up the test tweet.
    def _post_and_delete():
        tid = x.post(f"fmml self-test {int(time.time())} (auto-deleting)")
        time.sleep(1)
        raw.delete_tweet(id=tid, user_auth=True)
        return f"posted+deleted id={tid}"

    _run("post + delete", _post_and_delete)

    # has_reshared — optional, needs a username + post id you set up.
    if len(sys.argv) >= 3:
        uname, post_id = sys.argv[1], sys.argv[2]

        def _check_reshare():
            u = raw.get_user(username=uname, user_auth=True)
            return x.has_reshared(user_id=str(u.data.id), post_id=post_id)

        _run(f"has_reshared(@{uname}, {post_id})", _check_reshare)
    else:
        print("SKIP  has_reshared — pass <username> <post_id> to test it.")

    return 0


if __name__ == "__main__":
    sys.exit(main())
