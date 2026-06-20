"""Persona Dresser (ex-Profile Mutator) — applies/retires an identity.

Takes a warmed, OAuth-authorized account from the pipeline and applies the
generated identity (display name, bio, avatar) via the single developer app.
The @ handle is NEVER changed (X API cannot change handles). After the 1h reveal
window, retires the account: wipes it to a dormant state and schedules deletion.
"""

from __future__ import annotations

from .generator import GeneratedPersona


class PersonaDresser:
    def __init__(self, x_client):
        self._x = x_client

    def dress(self, persona_id: str, oauth_ref: str, identity: GeneratedPersona, claim_code: str) -> None:
        """Apply identity + place the claim code in bio/pinned.

        TODO(step 23): via x_client acting for this persona's OAuth tokens —
          1. update profile name + bio (bio carries the claim code)
          2. upload + set avatar
          3. optionally pin a post that displays the claim code
        Verify each field actually changed (v1.1 profile endpoints — test in the
        scaffold week; fallback = manual profile setup keeping the integrity hash).
        """
        raise NotImplementedError("dress — implemented in step 23")

    def retire(self, persona_id: str, oauth_ref: str) -> None:
        """Wipe to dormant after the reveal window; schedule deletion (+30d).

        TODO(step 23): blank bio/name to neutral, remove pinned, mark state
        'retired' and set delete_after. Never reuse this account.
        """
        raise NotImplementedError("retire — implemented in step 23")
