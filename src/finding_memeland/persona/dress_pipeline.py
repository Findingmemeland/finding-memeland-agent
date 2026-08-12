"""Dress Pipeline — pre-dressing at link time (design 2026-08-12, Fase 1).

Motivation (Hunt #5 post-mortem): renaming + re-avataring an account 24h before
launch resets X's indexing, so exact-phrase search fails during the hunt. The
fix is to dress personas WEEKS ahead (`/dress`), let X index them, and make
`/launch` instantaneous (Fase 3).

The golden rule this module implements (Pedro, 2026-08-11):

    Quando o agente escolhe uma persona pré-vestida, tem de saber EXATAMENTE o
    nome, código, posts e fotos — para não dar pistas para a persona errada.

R1 — ONE source of truth: everything applied to the X account is persisted on
     the persona row (the "descriptor"), taken from the DressReceipt — the
     exact strings the dresser sent — never from pre-application inputs.
R4 — post-dress immutability: after /dress NOBODY touches the account. Any
     correction is a formal re-dress (this pipeline again), which regenerates
     the identity, the claim code and the descriptor.

Operator blindness is preserved: the Telegram report NEVER contains the claim
code (it is applied to the bio and persisted, but the operator never sees it).
"""

from __future__ import annotations

import json
import time
from dataclasses import asdict
from datetime import datetime, timezone

from ..content.integrity import generate_claim_code
from .source import DEFAULT_MIN_WARMUP_DAYS, persona_findability_ready

# States /dress accepts. 'ready' is the normal path; 'dressed' is the formal
# re-dress (R4: corrections regenerate everything, never hand-edit).
_DRESSABLE_STATES = frozenset({"ready", "dressed"})

# Anchor posts published right after dressing (they have weeks to index).
# Spaced a few minutes apart so a fresh dress doesn't burst-post like a bot.
DEFAULT_ANCHOR_POSTS_N = 2
DEFAULT_ANCHOR_SPACING_S = 180


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class DressPipeline:
    """Runs one /dress end to end: generate identity → apply to the account →
    verify the write took → persist the descriptor → publish anchor posts."""

    def __init__(
        self,
        *,
        repo,
        token_resolver,          # callable(oauth_ref) -> (token, secret)
        generator,               # PersonaGenerator
        avatar_generator,        # AvatarGenerator
        dresser,                 # PersonaDresser
        post_engine=None,        # PersonaPostEngine (anchor posts); None skips
        notifier=None,
        avatar_writer=None,      # callable(bytes) -> path
        register: str = "medium",
        anchor_posts_n: int = DEFAULT_ANCHOR_POSTS_N,
        anchor_spacing_s: int = DEFAULT_ANCHOR_SPACING_S,
        min_warmup_days: int = DEFAULT_MIN_WARMUP_DAYS,
        now_fn=_utcnow,
        sleep_fn=time.sleep,
    ):
        self._repo = repo
        self._resolve = token_resolver
        self._generator = generator
        self._avatar_generator = avatar_generator
        self._dresser = dresser
        self._post_engine = post_engine
        self._notifier = notifier
        self._avatar_writer = avatar_writer
        self._register = register
        self._anchor_n = anchor_posts_n
        self._anchor_spacing_s = anchor_spacing_s
        self._min_days = min_warmup_days
        self._now = now_fn
        self._sleep = sleep_fn

    def _notify(self, text: str) -> None:
        if self._notifier is not None:
            self._notifier.notify(text)

    # ------------------------------------------------------------------
    def dress(self, ref: str, handle_hint: str | None = None) -> str:
        """Dress persona `ref` (oauth_ref or @handle). Returns the operator
        report (NEVER contains the claim code). Raises on refusal — every
        refusal happens BEFORE the account is touched (fail-closed)."""
        row = self._repo.persona_by_ref_or_handle(ref)
        if not row:
            raise RuntimeError(f"persona {ref!r} not found — authorize it first")

        state = str(row.get("state") or "")
        if state not in _DRESSABLE_STATES:
            raise RuntimeError(
                f"persona {row.get('handle')} is '{state}' — only "
                f"{sorted(_DRESSABLE_STATES)} can be dressed"
            )
        redress = state == "dressed"

        # Findability gate (same rule as the hunt path): an under-prepared
        # account must never enter the dressed pool.
        if not persona_findability_ready(
            row.get("account_created_at"), row.get("phone_verified"),
            min_days=self._min_days, now=self._now(),
        ):
            raise RuntimeError(
                f"persona {row.get('handle')} not findability-ready "
                f"(needs phone_verified + age >= {self._min_days}d) — not dressing"
            )

        # Handle hint (Pedro supplies it: the decomposable-handle failsafe the
        # clue engine uses as last resort). Fail-closed: no hint, no dress —
        # otherwise the handle clues (8-9 in the new ramp) have nothing to work
        # from at launch time, when it's too late to fix.
        hint = (handle_hint or "").strip() or str(row.get("handle_hint") or "").strip()
        if not hint:
            raise RuntimeError(
                f"persona {row.get('handle')} has no handle_hint — pass it: "
                "/dress <ref> <hint> (e.g. /dress 07 charging = what a phone "
                "does plugged in; capas = capes in PT)"
            )

        # Anti-repetition across BOTH pools: past hunts AND currently-dressed
        # personas (several dressed personas now coexist for weeks — two with
        # the same theme would make clues ambiguous between them).
        avoid: list[str] = []
        try:
            from ..orchestrator.state_machine import _theme_line

            for r in self._repo.recent_persona_identities():
                line = _theme_line(r)
                if line:
                    avoid.append(line)
        except Exception as e:  # noqa: BLE001
            self._notify(f"avoid_recent (hunts) unavailable ({e!r}) — proceeding without.")
        try:
            for r in self._repo.dressed_personas():
                if str(r.get("id")) == str(row.get("id")):
                    continue  # own row handled by the redress block below
                line = _dressed_theme_line(r)
                if line:
                    avoid.append(line)
        except Exception as e:  # noqa: BLE001
            self._notify(f"avoid_recent (dressed pool) unavailable ({e!r}) — proceeding without.")
        if redress:
            old = _dressed_theme_line(row)
            if old:
                avoid.append(old)  # a re-dress must not converge back on itself

        identity = self._generator.generate(register=self._register, avoid_recent=avoid)
        claim_code = generate_claim_code()

        avatar_path = banner_path = None
        png = self._avatar_generator.generate_png(identity.avatar_prompt)
        if png and self._avatar_writer is not None:
            avatar_path = self._avatar_writer(png)
        bpng = self._avatar_generator.generate_banner_png(identity.banner_prompt)
        if bpng and self._avatar_writer is not None:
            banner_path = self._avatar_writer(bpng)

        token, secret = self._resolve(row["oauth_ref"])
        receipt = self._dresser.dress(
            access_token=token,
            access_secret=secret,
            identity=identity,
            claim_code=claim_code,
            avatar_path=avatar_path,
            banner_path=banner_path,
        )

        # Write-took verification (dress-time twin of the launch-time R3):
        # what X reports back must match what we sent. On mismatch the account
        # may be half-dressed — REFUSE to mark it dressed, scream, and let the
        # operator re-dress (R4 path). Never persist a descriptor that doesn't
        # provably match the live profile.
        profile = receipt.profile
        prof_name = str(getattr(profile, "name", "") or "")
        prof_bio = str(getattr(profile, "description", "") or "")
        mismatches = []
        if prof_name != receipt.applied_name:
            mismatches.append(f"display name (X says {prof_name!r})")
        if claim_code not in prof_bio:
            mismatches.append("bio (claim code missing from what X reports)")
        if mismatches:
            self._notify(
                f"🚨 /dress VERIFICATION FAILED for {row.get('handle')}: "
                + "; ".join(mismatches)
                + " — persona NOT marked dressed. The account may be "
                "half-dressed: run /dress again (formal re-dress)."
            )
            raise RuntimeError(
                f"dress verification failed for {row.get('handle')}: "
                + "; ".join(mismatches)
            )

        # R1 — persist the descriptor from the RECEIPT (what was applied),
        # plus the full identity for the clue engine's facets.
        dressed_at = self._now()
        descriptor_fields = dict(
            persona_identity=asdict(identity),
            claim_code=claim_code,
            applied_display_name=receipt.applied_name,
            applied_bio=receipt.applied_bio,
            locator_post_id=receipt.locator_post_id,
            avatar_applied=receipt.avatar_applied,
            banner_applied=receipt.banner_applied,
            handle_hint=hint,
            anchor_posts=json.dumps([]),
            dressed_at=dressed_at,
        )
        try:
            self._repo.set_persona_state(row["id"], "dressed", **descriptor_fields)
        except Exception as e:  # noqa: BLE001
            self._notify(
                f"🚨 /dress applied to {row.get('handle')} but the descriptor "
                f"could NOT be persisted ({e!r}). The account is dressed with an "
                "identity the DB doesn't know — a launch would be refused (R3). "
                "Fix the DB and run /dress again (formal re-dress)."
            )
            raise

        anchors = self._publish_anchor_posts(row, identity, token, secret)

        report = (
            f"persona {row.get('handle')} vestida como "
            f"'{receipt.applied_name}'"
            + (" (re-dress: descritor anterior substituído)" if redress else "")
            + f" — avatar {'✓' if receipt.avatar_applied else '✗'}"
            + f", banner {'✓' if receipt.banner_applied else '✗'}"
            + f", locator post {'✓' if receipt.locator_post_id else '✗'}"
            + f", {len(anchors)} anchor post(s)."
            + " A conta está agora IMUTÁVEL (R4): ninguém lhe toca — correções "
            "= novo /dress."
        )
        self._notify(report)
        return report

    # ------------------------------------------------------------------
    def _publish_anchor_posts(self, row, identity, token, secret) -> list[dict]:
        """Publish the anchor posts (searchable phrases the post-phase clues
        point at), persisting the list after EVERY publish so a crash mid-way
        never loses a published post. Best-effort: a failure is notified and
        the dress stands with fewer anchors (the locator post always exists)."""
        anchors: list[dict] = []
        if self._post_engine is None or self._anchor_n <= 0:
            return anchors
        try:
            texts = self._post_engine.generate(identity, n=self._anchor_n)
        except Exception as e:  # noqa: BLE001
            self._notify(
                f"anchor post generation failed ({e!r}) — dress stands with the "
                "locator post only."
            )
            return anchors
        for i, text in enumerate(texts):
            if i:
                self._sleep(self._anchor_spacing_s)
            try:
                tweet_id = self._dresser.publish_post(
                    access_token=token, access_secret=secret, text=text
                )
            except Exception as e:  # noqa: BLE001
                self._notify(f"anchor post {i + 1} failed ({e!r}) — continuing.")
                continue
            anchors.append(
                {"text": text, "tweet_id": tweet_id, "posted_at": self._now().isoformat()}
            )
            try:
                self._repo.update_persona(row["id"], anchor_posts=json.dumps(anchors))
            except Exception as e:  # noqa: BLE001
                self._notify(
                    f"anchor post {tweet_id} published but not persisted ({e!r}) "
                    "— the descriptor is missing one anchor; re-dress if it matters."
                )
        return anchors


def _dressed_theme_line(row: dict) -> str:
    """avoid_recent line for a dressed persona row (same shape as the hunts')."""
    payload = row.get("persona_identity")
    if isinstance(payload, str):
        try:
            payload = json.loads(payload)
        except ValueError:
            payload = None
    payload = payload or {}
    name = str(payload.get("display_name") or "").strip()
    archetype = str(payload.get("archetype") or "").strip()
    terms = ", ".join(
        str(t) for t in (payload.get("solution_terms") or []) if str(t).strip()
    )
    bits = [b for b in (name, archetype, terms) if b]
    return " / ".join(bits)
