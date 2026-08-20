"""Fase 1 do pré-vestir (design 2026-08-12): descritor + /dress.

The heart of the design is R1 — the descriptor persisted in the DB must be
EXACTLY what was applied to the X account, by construction. These tests drive
the real PersonaDresser against a fake X client that records what it was told,
then assert the persisted descriptor matches the recording verbatim.
"""

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

import pytest

from finding_memeland.persona.dress_pipeline import DressPipeline
from finding_memeland.persona.dresser import PersonaDresser
from finding_memeland.persona.generator import GeneratedPersona


NOW = datetime(2026, 8, 12, 12, 0, tzinfo=timezone.utc)


def _identity(name="Quantum Toad", terms=("Schrodinger",)):
    return GeneratedPersona(
        display_name=name,
        bio="probably here, probably not",
        avatar_prompt="a toad mid-leap, blurred into two positions",
        banner_prompt="a pond that is also not a pond",
        voice="deadpan physics jokes",
        backstory="a toad that exists in superposition",
        archetype="fully invented fictional character",
        solution_terms=list(terms),
        findable_post="observing my own pond collapses it every single time",
    )


@dataclass
class _Profile:
    user_id: str
    screen_name: str
    name: str
    description: str


class FakeX:
    """Records exactly what the dresser applies; echoes it back like X does."""

    def __init__(self, *, corrupt_name=False):
        self.calls = []
        self.posts = []
        self._corrupt_name = corrupt_name

    def set_avatar(self, t, s, path):
        self.calls.append(("avatar", path))

    def set_banner(self, t, s, path):
        self.calls.append(("banner", path))

    def update_profile(self, t, s, *, name=None, description=None):
        self.calls.append(("profile", name, description))
        reported = "Someone Else" if self._corrupt_name else name
        return _Profile(
            user_id="111", screen_name="ExpressoTitgo",
            name=reported, description=description,
        )

    def post_as_persona(self, t, s, text):
        self.posts.append(text)
        return f"tweet-{len(self.posts)}"


class FakeRepo:
    def __init__(self, rows):
        self.rows = {str(r["id"]): dict(r) for r in rows}
        self.hunt_identities = []

    def persona_by_ref_or_handle(self, key):
        for r in self.rows.values():
            if r.get("oauth_ref") == key or r.get("handle") in (key, f"@{key}"):
                return dict(r)
        return None

    def set_persona_state(self, persona_id, state, **fields):
        self.rows[str(persona_id)].update({"state": state, **fields})

    def update_persona(self, persona_id, **fields):
        self.rows[str(persona_id)].update(fields)

    def recent_persona_identities(self):
        return list(self.hunt_identities)

    def dressed_personas(self):
        return [dict(r) for r in self.rows.values() if r.get("state") == "dressed"]


class FakeGenerator:
    def __init__(self, identity=None):
        self.identity = identity or _identity()
        self.seen_avoid = None

    def generate(self, *, register=None, avoid_recent=None):
        self.seen_avoid = list(avoid_recent or [])
        return self.identity


class FakeAvatarGen:
    def generate_png(self, prompt):
        return b"png-avatar"

    def generate_banner_png(self, prompt):
        return b"png-banner"


class FakePostEngine:
    def __init__(self, texts=("anchor one alpha", "anchor two beta")):
        self.texts = list(texts)

    def generate(self, identity, n=2):
        return self.texts[:n]


class FakeNotifier:
    def __init__(self):
        self.messages = []

    def notify(self, text):
        self.messages.append(text)


def _row(**over):
    row = dict(
        id="p1", handle="@ExpressoTitgo", x_user_id="111", oauth_ref="07",
        state="ready", phone_verified=True,
        account_created_at=NOW - timedelta(days=40),
        handle_hint=None,
    )
    row.update(over)
    return row


def _pipeline(repo, *, x=None, generator=None, notifier=None, post_engine=None):
    x = x or FakeX()
    return (
        DressPipeline(
            repo=repo,
            token_resolver=lambda ref: (f"tok-{ref}", f"sec-{ref}"),
            generator=generator or FakeGenerator(),
            avatar_generator=FakeAvatarGen(),
            dresser=PersonaDresser(x),
            post_engine=post_engine if post_engine is not None else FakePostEngine(),
            notifier=notifier or FakeNotifier(),
            avatar_writer=lambda png: f"/tmp/{len(png)}.png",
            now_fn=lambda: NOW,
            sleep_fn=lambda s: None,
        ),
        x,
    )


# ---------------------------------------------------------------------------
# R1 — the descriptor is EXACTLY what was applied
# ---------------------------------------------------------------------------
def test_descriptor_matches_what_was_applied_verbatim():
    repo = FakeRepo([_row()])
    pipe, x = _pipeline(repo)
    pipe.dress("07", handle_hint="expresso = coffee; titgo = tiny")

    row = repo.rows["p1"]
    applied = next(c for c in x.calls if c[0] == "profile")
    assert row["state"] == "dressed"
    assert row["applied_display_name"] == applied[1]          # exact name sent
    assert row["applied_bio"] == applied[2]                   # exact bio sent
    assert row["claim_code"] in row["applied_bio"]            # code lives in the bio
    assert len(row["claim_code"]) == 8
    assert row["locator_post_id"] == "tweet-1"                # locator captured
    assert row["persona_identity"]["display_name"] == "Quantum Toad"
    assert row["persona_identity"]["solution_terms"] == ["Schrodinger"]
    assert row["avatar_applied"] is True and row["banner_applied"] is True
    assert row["handle_hint"] == "expresso = coffee; titgo = tiny"
    assert row["dressed_at"] == NOW


def test_locator_post_is_the_identitys_findable_post():
    repo = FakeRepo([_row()])
    pipe, x = _pipeline(repo)
    pipe.dress("07", handle_hint="h")
    assert x.posts[0] == _identity().findable_post


# ---------------------------------------------------------------------------
# Operator blindness — the code is NEVER shown
# ---------------------------------------------------------------------------
def test_report_and_notifications_never_contain_the_claim_code():
    repo = FakeRepo([_row()])
    notifier = FakeNotifier()
    pipe, _ = _pipeline(repo, notifier=notifier)
    report = pipe.dress("07", handle_hint="h")
    code = repo.rows["p1"]["claim_code"]
    assert code not in report
    assert all(code not in m for m in notifier.messages)
    assert "Quantum Toad" in report  # the operator does learn the name


# ---------------------------------------------------------------------------
# Fail-closed refusals — all BEFORE the account is touched
# ---------------------------------------------------------------------------
def test_refuses_without_handle_hint_before_touching_x():
    repo = FakeRepo([_row(handle_hint=None)])
    pipe, x = _pipeline(repo)
    with pytest.raises(RuntimeError, match="handle_hint"):
        pipe.dress("07")
    assert x.calls == [] and x.posts == []
    assert repo.rows["p1"]["state"] == "ready"


def test_hint_on_the_row_is_enough():
    repo = FakeRepo([_row(handle_hint="already stored hint")])
    pipe, _ = _pipeline(repo)
    pipe.dress("07")
    assert repo.rows["p1"]["state"] == "dressed"
    assert repo.rows["p1"]["handle_hint"] == "already stored hint"


# 2026-08-20 (Pedro): dressing while under-prepared is ALLOWED by design — the
# account indexes already dressed. Findability only gates the LAUNCH.
def test_dresses_underprepared_account_and_flags_it_in_the_report():
    repo = FakeRepo([_row(phone_verified=False)])
    pipe, x = _pipeline(repo)
    report = pipe.dress("07", handle_hint="h")
    assert repo.rows["p1"]["state"] == "dressed"
    assert "phone NOT verified" in report          # operator must fix the row


def test_dresses_too_young_account_with_launchable_from_date():
    repo = FakeRepo([_row(account_created_at=NOW - timedelta(days=2), state="warmup")])
    pipe, x = _pipeline(repo)
    report = pipe.dress("07", handle_hint="h")
    assert repo.rows["p1"]["state"] == "dressed"
    assert "lançável a partir de" in report        # created+min_days, dd/mm hh:mm
    assert "warmup" in report.lower()


def test_ready_persona_report_has_no_warmup_note():
    repo = FakeRepo([_row()])
    pipe, _ = _pipeline(repo)
    report = pipe.dress("07", handle_hint="h")
    assert "warmup" not in report.lower()


def test_refuses_wrong_state():
    repo = FakeRepo([_row(state="in_play")])
    pipe, x = _pipeline(repo)
    with pytest.raises(RuntimeError, match="in_play"):
        pipe.dress("07", handle_hint="h")
    assert x.calls == []


def test_refuses_unknown_persona():
    repo = FakeRepo([_row()])
    pipe, _ = _pipeline(repo)
    with pytest.raises(RuntimeError, match="not found"):
        pipe.dress("99", handle_hint="h")


# ---------------------------------------------------------------------------
# Write-took verification (dress-time R3 twin)
# ---------------------------------------------------------------------------
def test_verification_mismatch_refuses_to_mark_dressed_and_screams():
    repo = FakeRepo([_row()])
    notifier = FakeNotifier()
    x = FakeX(corrupt_name=True)
    pipe, _ = _pipeline(repo, x=x, notifier=notifier)
    with pytest.raises(RuntimeError, match="verification failed"):
        pipe.dress("07", handle_hint="h")
    assert repo.rows["p1"]["state"] == "ready"          # NOT dressed
    assert repo.rows["p1"].get("claim_code") is None    # descriptor NOT persisted
    assert any("VERIFICATION FAILED" in m for m in notifier.messages)


# ---------------------------------------------------------------------------
# R4 — re-dress replaces the descriptor formally
# ---------------------------------------------------------------------------
def test_redress_replaces_descriptor_with_a_fresh_code():
    repo = FakeRepo([_row()])
    pipe, x = _pipeline(repo)
    pipe.dress("07", handle_hint="h")
    first_code = repo.rows["p1"]["claim_code"]
    first_locator = repo.rows["p1"]["locator_post_id"]

    gen2 = FakeGenerator(_identity(name="Sleepy Volcano", terms=("Vesuvius",)))
    pipe2, _ = _pipeline(repo, generator=gen2, x=x)  # same account, same X
    report = pipe2.dress("07", handle_hint="h")

    row = repo.rows["p1"]
    assert row["state"] == "dressed"
    assert row["claim_code"] != first_code                  # fresh code
    assert row["persona_identity"]["display_name"] == "Sleepy Volcano"
    assert row["locator_post_id"] != first_locator
    assert "re-dress" in report
    # the old theme fed avoid_recent so the generator can't converge back
    assert any("Quantum Toad" in a for a in gen2.seen_avoid)


# ---------------------------------------------------------------------------
# Anchor posts
# ---------------------------------------------------------------------------
def test_anchor_posts_published_and_persisted_with_tweet_ids():
    repo = FakeRepo([_row()])
    pipe, x = _pipeline(repo)
    pipe.dress("07", handle_hint="h")
    import json

    anchors = json.loads(repo.rows["p1"]["anchor_posts"])
    assert [a["text"] for a in anchors] == ["anchor one alpha", "anchor two beta"]
    assert all(a["tweet_id"] for a in anchors)
    # locator + 2 anchors were published as the persona
    assert len(x.posts) == 3


def test_anchor_failure_keeps_the_dress_standing():
    class BrokenPostEngine:
        def generate(self, identity, n=2):
            raise RuntimeError("llm down")

    repo = FakeRepo([_row()])
    notifier = FakeNotifier()
    pipe, _ = _pipeline(repo, notifier=notifier, post_engine=BrokenPostEngine())
    pipe.dress("07", handle_hint="h")
    assert repo.rows["p1"]["state"] == "dressed"            # dress stands
    assert any("anchor post generation failed" in m for m in notifier.messages)


# ---------------------------------------------------------------------------
# Anti-repetition across the dressed pool
# ---------------------------------------------------------------------------
def test_avoid_recent_includes_hunts_and_other_dressed_personas():
    other = _row(
        id="p2", handle="@OtherOne", oauth_ref="06", state="dressed",
        persona_identity={
            "display_name": "Mirrored Ada", "archetype": "historical",
            "solution_terms": ["Lovelace"],
        },
    )
    repo = FakeRepo([_row(), other])
    repo.hunt_identities = [
        {"persona_display_name": "Celestial Mechanic",
         "persona_identity": {"archetype": "historical", "solution_terms": ["Le Verrier"]}},
    ]
    gen = FakeGenerator()
    pipe, _ = _pipeline(repo, generator=gen)
    pipe.dress("07", handle_hint="h")
    joined = " | ".join(gen.seen_avoid)
    assert "Celestial Mechanic" in joined      # past hunts
    assert "Mirrored Ada" in joined            # the dressed pool
