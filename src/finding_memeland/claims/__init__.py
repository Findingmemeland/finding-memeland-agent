"""Claim-by-post — the public submission channel (2026-07-25).

X's DM REST API only delivers "virgin" conversations (proven in production,
Hunt #2/#3 investigation: once we reply — or the app touches the conversation
and XChat encrypts it — inbound messages become invisible to the API). DMs can
therefore carry NO critical piece of the game flow.

The claim channel moved to PUBLIC POSTS: players reply to the Clue 1 post with
the claim code; ordering is decided by the tweet's own created_at (snowflake id
as millisecond tiebreak) — publicly auditable, sniping-proof by construction.
The winner is asked for the wallet in a public reply and only the SAME author_id
can answer. Wrong codes get oracle taunts (rate-capped, clue-free).
"""
