"""R3 — fail-closed launch verification (pre-dressing design, Fase 2).

Before a single clue is generated, the agent reads the persona's REAL profile
on X and compares it against the persisted descriptor. Any mismatch REFUSES
the launch: clues generated from a descriptor that no longer matches the live
account would send players hunting a dead target.

What is verified (Pedro, 13/08):
- display name == descriptor.applied_display_name (exact)
- live bio contains descriptor.claim_code
- descriptor.locator_post_id still exists (and its text still matches)
- avatar EXISTENCE only (X re-encodes images, so no byte comparison): a
  dressed persona must not be showing the default egg.

Failure semantics: return the list of mismatches; the caller refuses the
launch and alerts the operator. NEVER silently substitute another persona —
a divergence can mean someone touched the account, and the operator must know.
"""

from __future__ import annotations

import json


def _norm(text: str) -> str:
    """Whitespace-insensitive comparison: X normalizes line endings/trailing
    space on round-trip; the words themselves must be identical."""
    return " ".join(str(text or "").split())


class DressedProfileVerifier:
    """Reads the live profile + locator post via XClient and diffs them
    against the descriptor row. Pure I/O + comparison; no state."""

    def __init__(self, x_client):
        self._x = x_client

    def verify(self, row: dict, access_token: str, access_secret: str) -> list[str]:
        """Returns the list of mismatches (empty = R3 passed). Raises on I/O
        failure — the caller treats an unverifiable profile as a refusal
        (fail-closed), never as a pass."""
        mismatches: list[str] = []

        profile = self._x.get_profile(access_token, access_secret)

        expected_name = str(row.get("applied_display_name") or "")
        if str(profile.name or "") != expected_name:
            mismatches.append(
                f"display name: X diz {profile.name!r}, descritor diz {expected_name!r}"
            )

        claim_code = str(row.get("claim_code") or "")
        if not claim_code:
            mismatches.append("descritor sem claim_code — re-dress obrigatório")
        elif claim_code not in str(profile.description or ""):
            mismatches.append("bio: o claim code do descritor não está na bio real")

        if getattr(profile, "has_custom_avatar", True) is False and row.get("avatar_applied"):
            mismatches.append("avatar: a conta mostra o avatar default do X")

        locator_id = str(row.get("locator_post_id") or "")
        if not locator_id:
            mismatches.append("descritor sem locator_post_id — re-dress obrigatório")
        else:
            post = self._x.get_persona_post(access_token, access_secret, locator_id)
            if post is None:
                mismatches.append(f"locator post {locator_id} já não existe no X")
            else:
                expected_text = _locator_text(row)
                if expected_text and _norm(post.get("text")) != _norm(expected_text):
                    mismatches.append(
                        "locator post: o texto no X difere do findable_post do descritor"
                    )

        return mismatches


def _locator_text(row: dict) -> str:
    """The locator's expected text = the identity's findable_post (what the
    dresser published)."""
    payload = row.get("persona_identity")
    if isinstance(payload, str):
        try:
            payload = json.loads(payload)
        except ValueError:
            payload = None
    return str((payload or {}).get("findable_post") or "")
