"""Persona Dresser (ex-Profile Mutator) — applies/retires an identity.

Takes a warmed, OAuth-authorized account from the pipeline and applies the
generated identity (display name, bio, avatar) via the single developer app.
The @ handle is NEVER changed (X API cannot change handles). After the 1h reveal
window, retires the account: wipes it to a dormant state and schedules deletion.

The claim code is embedded in the bio — finding the persona is the only way to
read the code that the winner must DM to the main account.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from ..social.x_text import MAX_BIO_LEN, sanitize_bio, sanitize_name
from .generator import GeneratedPersona

if TYPE_CHECKING:
    from ..social.x_client import Profile, XClient


@dataclass
class DressReceipt:
    """What dress() actually did — R1 (pre-dressing design, 2026-08-12): the
    EXACT applied strings and the locator post id are returned so the caller
    can persist them verbatim. The descriptor is built from THIS, never from
    the pre-application inputs."""
    profile: object                 # Profile X reported back
    applied_name: str               # sanitized display name as sent to X
    applied_bio: str                # composed bio (base + claim code) as sent
    locator_post_id: str | None    # tweet id of the published locator post
    avatar_applied: bool
    banner_applied: bool

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
    ) -> DressReceipt:
        """Apply identity (name, bio+claim code, avatar, banner) and publish the
        findable locator post so the account becomes searchable. Returns a
        DressReceipt with the profile X reported back AND the exact applied
        strings + locator post id, so the caller can verify the write took and
        persist the descriptor verbatim (R1)."""
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
        locator_id = None
        if locator:
            locator_id = self._x.post_as_persona(access_token, access_secret, locator)
        return DressReceipt(
            profile=profile,
            applied_name=name,
            applied_bio=bio,
            locator_post_id=locator_id,
            avatar_applied=bool(avatar_path),
            banner_applied=bool(banner_path),
        )

    def publish_post(self, *, access_token: str, access_secret: str, text: str) -> str:
        """Publish one post AS the persona (P2 prep-window anchor posts)."""
        return self._x.post_as_persona(access_token, access_secret, text)

    def retire(self, *, access_token: str, access_secret: str) -> Profile:
        """Reset the account to a neutral dormant state after the reveal window.
        The DB marks state 'retired' and sets delete_after (+30d) separately."""
        return self._x.update_profile(
            access_token, access_secret, name=DORMANT_NAME, description=DORMANT_BIO
        )
