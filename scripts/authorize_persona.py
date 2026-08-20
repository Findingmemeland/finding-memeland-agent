"""Authorize a warmed persona account on the single developer app (checklist step 16).

Run this ONCE per persona, AFTER its ~10-day warmup. You must be logged into X as
THAT persona in your browser when you open the authorization link.

Flow (OAuth 1.0a, 3-legged PIN):
  1. open the printed link (logged in as the persona) and approve the app
  2. paste the PIN it shows you
  3. the script fetches the persona's access token/secret + user id/handle,
     prints the two secrets to add to Doppler, and registers the persona in
     Supabase as 'ready'.

    python scripts/authorize_persona.py <oauth_ref>      # e.g. 01

The <oauth_ref> ties the stored secrets (X_PERSONA_<ref>_ACCESS_TOKEN/SECRET in
Doppler) to the DB row, so the agent can resolve a persona's tokens at hunt time.
"""

from __future__ import annotations

import sys

import tweepy

from finding_memeland.config import get_settings
from finding_memeland.db.client import Repo, make_client


def main() -> int:
    if len(sys.argv) < 2:
        print("usage: authorize_persona.py <oauth_ref>   (e.g. 01)")
        return 2
    ref = sys.argv[1].strip()

    s = get_settings()
    if not s.x_api_key or not s.x_api_secret:
        print("FAIL — X_API_KEY / X_API_SECRET missing in .env.")
        return 2

    auth = tweepy.OAuth1UserHandler(s.x_api_key, s.x_api_secret, callback="oob")
    try:
        url = auth.get_authorization_url()
    except Exception as e:  # noqa: BLE001
        print(f"FAIL — could not start OAuth (check app keys + 'oob' callback allowed): {e!r}")
        return 1

    print("\n1) Make sure your browser is logged in AS THE PERSONA you're authorizing.")
    print("2) Open this link and click Authorize:\n")
    print(f"   {url}\n")
    pin = input("3) Paste the PIN shown by X: ").strip()

    try:
        access_token, access_secret = auth.get_access_token(pin)
    except Exception as e:  # noqa: BLE001
        print(f"FAIL — bad PIN / token exchange: {e!r}")
        return 1

    # Identify the persona we just authorized.
    api = tweepy.API(tweepy.OAuth1UserHandler(
        s.x_api_key, s.x_api_secret, access_token, access_secret))
    me = api.verify_credentials()
    handle, x_user_id = me.screen_name, me.id_str
    account_created_at = getattr(me, "created_at", None)
    print(f"\nauthorized @{handle} (user id {x_user_id}, created {account_created_at}).")

    # Findability gate: a persona is only usable if phone-verified AND ~7d old.
    from finding_memeland.persona.source import persona_findability_ready

    phone_verified = input("Is this account phone-verified (eSIM/number added)? (y/n): ").strip().lower() == "y"
    ready = persona_findability_ready(account_created_at, phone_verified, min_days=s.min_warmup_days)
    state = "ready" if ready else "warmup"

    print("\n--- ADD THESE TO DOPPLER (config: dev and prd) ---")
    print(f"X_PERSONA_{ref}_ACCESS_TOKEN={access_token}")
    print(f"X_PERSONA_{ref}_ACCESS_SECRET={access_secret}")
    print("---------------------------------------------------")
    print("(do NOT paste these in chat — straight into Doppler)\n")

    if s.supabase_url and s.supabase_service_role_key:
        repo = Repo(make_client(s.supabase_url, s.supabase_service_role_key))
        pid = repo.create_persona(
            handle=f"@{handle}", x_user_id=x_user_id, oauth_ref=ref, state=state,
            account_created_at=account_created_at, phone_verified=phone_verified,
        )
        print(f"registered in Supabase as '{state}' (persona id {pid}).")
    else:
        print(f"Supabase not configured — add the persona row manually, state='{state}'.")

    if not ready:
        print(
            f"\n⏳ Not findability-ready yet (needs phone-verified + age >= {s.min_warmup_days}d). "
            "Saved as 'warmup' — you CAN /dress it now (it will index dressed, by design); "
            "only /launch waits for findability, automatically, no row edits needed."
        )
    print("\nPASS — persona authorized. Repeat for each persona with its own <ref>.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
