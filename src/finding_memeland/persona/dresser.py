"""Persona Dresser (ex-Profile Mutator) — applies/retires an identity.

Takes a warmed, OAuth-authorized account from the pipeline and applies the
generated identity (display name, bio, avatar) via the single developer app.
The @ handle is NEVER changed (X API cannot change handles). After the 1h reveal
window, retires the account: wipes it to a dormant state and schedules deletion.

The claim code is embedded in the bio — finding the persona is the only way to
read the code that the winner must DM to the main account.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from ..social.x_text import MAX_BIO_LEN, sanitize_bio, sanitize_name
from .generator import GeneratedPersona

if TYPE_CHECKING:
    from ..social.x_client import Profile, XClient

# Neutral state a retired account is reset to (no game identity).
DORMANT_NAME = "—"
DORMANT_BIO = "just here for the vibes"


def compose_bio(base_bio: str, claim_code: str) -> str:
    """Sanitize the base bio (X-safe chars) and append the claim code, fitting
    the 160-char limit. Finding this code is how a player proves they found the
    persona."""
    suffix = f"\ncode: {claim_code}"
    room = MAX_BIO_LEN - len(suffix)
    if room < 0:
        raise ValueError("claim code too long for a bio")
    safe_base = sanitize_bio(base_bio, reserve_for_claim_code=False)[:room].rstrip()
    return f"{safe_base}{suffix}"


class PersonaDresser:
    def __init__(self, x_client: XClient):
        self._x = x_client

    def dress(
        self,
        *,
        access_token: str,
        access_secret: str,
        identity: GeneratedPersona,
        claim_code: str,
        avatar_path: str | None = None,
        banner_path: str | None = None,
    ) -> Profile:
        """Apply identity (name, bio+claim code, avatar, banner) and publish the
        findable locator post so the account becomes searchable. Returns the
        profile X reports back, so the orchestrator can verify the write took."""
        bio = compose_bio(identity.bio, claim_code)
        name = sanitize_name(identity.display_name)
        if avatar_path:
            self._x.set_avatar(access_token, access_secret, avatar_path)
        if banner_path:
            self._x.set_banner(access_token, access_secret, banner_path)
        profile = self._x.update_profile(
            access_token, access_secret, name=name, description=bio
        )
        # Publish the locator anchor (distinctive searchable post) as the persona.
        locator = getattr(identity, "findable_post", "")
        if locator:
            self._x.post_as_persona(access_token, access_secret, locator)
        return profile

    def retire(self, *, access_token: str, access_secret: str) -> Profile:
        """Reset the account to a neutral dormant state after the reveal window.
        The DB marks state 'retired' and sets delete_after (+30d) separately."""
        return self._x.update_profile(
            access_token, access_secret, name=DORMANT_NAME, description=DORMANT_BIO
        )
