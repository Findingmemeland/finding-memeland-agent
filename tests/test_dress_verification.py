"""R3 — DressedProfileVerifier (Fase 2 do pré-vestir).

The verifier reads the LIVE profile and locator post and diffs them against
the descriptor. Empty list = R3 passed; anything else names the divergence.
"""

import pytest

from finding_memeland.persona.verification import DressedProfileVerifier
from finding_memeland.social.x_client import Profile


def _row(**over):
    row = dict(
        id="p1", handle="@ExpressoTitgo", oauth_ref="07",
        applied_display_name="Cassandra Tired",
        applied_bio="prophecy is a customer-service job\ncode: ABCDEFGH",
        claim_code="ABCDEFGH",
        locator_post_id="tweet-1",
        avatar_applied=True,
        persona_identity={
            "display_name": "Cassandra Tired",
            "findable_post": "nobody books a prophet for good news anymore",
        },
    )
    row.update(over)
    return row


class FakeX:
    def __init__(self, *, name="Cassandra Tired",
                 bio="prophecy is a customer-service job\ncode: ABCDEFGH",
                 custom_avatar=True,
                 locator_text="nobody books a prophet for good news anymore",
                 locator_exists=True, profile_error=None):
        self._name = name
        self._bio = bio
        self._custom_avatar = custom_avatar
        self._locator_text = locator_text
        self._locator_exists = locator_exists
        self._profile_error = profile_error

    def get_profile(self, token, secret):
        if self._profile_error:
            raise self._profile_error
        return Profile(
            user_id="111", screen_name="ExpressoTitgo",
            name=self._name, description=self._bio,
            has_custom_avatar=self._custom_avatar,
        )

    def get_persona_post(self, token, secret, tweet_id):
        if not self._locator_exists:
            return None
        return {"id": tweet_id, "text": self._locator_text}


def test_matching_profile_passes_with_no_mismatches():
    v = DressedProfileVerifier(FakeX())
    assert v.verify(_row(), "t", "s") == []


def test_display_name_divergence_is_flagged():
    v = DressedProfileVerifier(FakeX(name="Someone Else"))
    out = v.verify(_row(), "t", "s")
    assert len(out) == 1 and "display name" in out[0]


def test_missing_code_in_bio_is_flagged():
    v = DressedProfileVerifier(FakeX(bio="prophecy is a customer-service job"))
    out = v.verify(_row(), "t", "s")
    assert len(out) == 1 and "bio" in out[0]


def test_default_avatar_is_flagged():
    v = DressedProfileVerifier(FakeX(custom_avatar=False))
    out = v.verify(_row(), "t", "s")
    assert len(out) == 1 and "avatar" in out[0]


def test_deleted_locator_post_is_flagged():
    v = DressedProfileVerifier(FakeX(locator_exists=False))
    out = v.verify(_row(), "t", "s")
    assert len(out) == 1 and "não existe" in out[0]


def test_locator_text_divergence_is_flagged():
    v = DressedProfileVerifier(FakeX(locator_text="a completely different post"))
    out = v.verify(_row(), "t", "s")
    assert len(out) == 1 and "locator post" in out[0]


def test_locator_text_comparison_is_whitespace_insensitive():
    v = DressedProfileVerifier(
        FakeX(locator_text="nobody books a prophet\nfor good news   anymore")
    )
    assert v.verify(_row(), "t", "s") == []


def test_descriptor_without_code_or_locator_is_flagged():
    v = DressedProfileVerifier(FakeX())
    out = v.verify(_row(claim_code=None, locator_post_id=None), "t", "s")
    joined = " | ".join(out)
    assert "claim_code" in joined and "locator_post_id" in joined


def test_multiple_divergences_are_all_reported():
    v = DressedProfileVerifier(FakeX(name="Wrong Name", locator_exists=False))
    out = v.verify(_row(), "t", "s")
    assert len(out) == 2


def test_profile_io_failure_raises_for_fail_closed_handling():
    v = DressedProfileVerifier(FakeX(profile_error=ConnectionError("X down")))
    with pytest.raises(ConnectionError):
        v.verify(_row(), "t", "s")


def test_persona_identity_as_json_string_still_verifies_locator_text():
    import json

    row = _row(persona_identity=json.dumps({
        "display_name": "Cassandra Tired",
        "findable_post": "nobody books a prophet for good news anymore",
    }))
    v = DressedProfileVerifier(FakeX())
    assert v.verify(row, "t", "s") == []
