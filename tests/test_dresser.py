from finding_memeland.persona.dresser import compose_bio
from finding_memeland.persona.generator import GeneratedPersona
from finding_memeland.persona.dresser import PersonaDresser


def _persona():
    return GeneratedPersona(
        display_name="Celestial Mechanic",
        bio="everything moves, everything pulls",
        avatar_prompt="stern astronomer",
        banner_prompt="a night sky with a faint unknown planet",
        voice="terse",
        backstory="bs",
        archetype="historical figure",
        solution_terms=["Le Verrier"],
        findable_post="auditing the silent arithmetic of the void tonight",
    )


class _FakeX:
    def __init__(self):
        self.calls = []

    def set_avatar(self, t, s, path):
        self.calls.append(("avatar", path))

    def set_banner(self, t, s, path):
        self.calls.append(("banner", path))

    def update_profile(self, t, s, *, name=None, description=None):
        self.calls.append(("profile", name, description))
        return {"name": name, "description": description}

    def post_as_persona(self, t, s, text):
        self.calls.append(("post", text))
        return "tweet-1"


def test_compose_bio_embeds_claim_code_and_sanitizes():
    out = compose_bio("vibes [only] here", "ABCDEFGH")
    assert "[" not in out and "]" not in out
    assert out.endswith("code: ABCDEFGH")
    assert len(out) <= 160


def test_dress_sets_avatar_banner_profile_and_posts_locator():
    x = _FakeX()
    receipt = PersonaDresser(x).dress(
        access_token="t", access_secret="s", identity=_persona(),
        claim_code="ABCDEFGH", avatar_path="/tmp/a.png", banner_path="/tmp/b.png",
    )
    kinds = [c[0] for c in x.calls]
    assert kinds == ["avatar", "banner", "profile", "post"]
    # the locator post published is the persona's findable_post
    assert ("post", "auditing the silent arithmetic of the void tonight") in x.calls
    # R1 receipt: the exact applied strings + the locator post id come back
    assert receipt.applied_name == "Celestial Mechanic"
    assert receipt.applied_bio.endswith("code: ABCDEFGH")
    assert receipt.locator_post_id == "tweet-1"
    assert receipt.avatar_applied is True and receipt.banner_applied is True


def test_dress_skips_images_when_paths_missing():
    x = _FakeX()
    receipt = PersonaDresser(x).dress(
        access_token="t", access_secret="s", identity=_persona(), claim_code="ABCDEFGH",
    )
    kinds = [c[0] for c in x.calls]
    assert "avatar" not in kinds and "banner" not in kinds
    assert kinds == ["profile", "post"]  # still publishes the locator post
    assert receipt.avatar_applied is False and receipt.banner_applied is False
