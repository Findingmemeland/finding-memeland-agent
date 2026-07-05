"""DBPersonaSource — the Orchestrator's PersonaSource port, backed by Supabase.

Hands the orchestrator the next warmed, OAuth-authorized account from the
pipeline and marks accounts in_play / retired. OAuth tokens are NOT stored in the
DB; they're resolved at use time from Doppler/env by the persona's oauth_ref.

Findability rule (validated empirically 2026-06-25): an account is only
search-findable by name once it is PHONE-VERIFIED and ~7 days old. So a persona
is eligible for a hunt only if phone_verified AND aged >= min_warmup_days. This
is enforced defensively here so an under-prepared account never goes live.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from ..orchestrator.ports import ReadyPersona

DELETE_AFTER_DAYS = 30
DEFAULT_MIN_WARMUP_DAYS = 7


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _as_dt(value) -> datetime | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    try:
        dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
    except ValueError:
        return None


def persona_findability_ready(
    account_created_at, phone_verified, *, min_days: int, now: datetime | None = None
) -> bool:
    """True iff the account is phone-verified AND old enough to be name-searchable.
    Pure; used as the readiness gate."""
    if not phone_verified:
        return False
    created = _as_dt(account_created_at)
    if created is None:
        return False
    now = now or _utcnow()
    return (now - created) >= timedelta(days=min_days)


class DBPersonaSource:
    def __init__(self, repo, token_resolver, *, min_warmup_days: int = DEFAULT_MIN_WARMUP_DAYS, now_fn=_utcnow):
        self._repo = repo
        self._resolve = token_resolver  # callable(oauth_ref) -> (token, secret)
        self._min_days = min_warmup_days
        self._now = now_fn

    def acquire_ready(self) -> ReadyPersona:
        row = self._repo.next_ready_persona()
        if not row:
            raise RuntimeError("no 'ready' persona in the pipeline — warm/authorize more")

        # Defensive findability gate: never send an under-prepared account to a hunt.
        if not persona_findability_ready(
            row.get("account_created_at"), row.get("phone_verified"),
            min_days=self._min_days, now=self._now(),
        ):
            raise RuntimeError(
                f"persona {row.get('handle')} not findability-ready "
                f"(needs phone_verified + age >= {self._min_days}d)"
            )

        token, secret = self._resolve(row["oauth_ref"])
        self._repo.set_persona_state(row["id"], "in_play")
        return ReadyPersona(
            id=row["id"],
            handle=row["handle"],
            x_user_id=row["x_user_id"],
            access_token=token,
            access_secret=secret,
        )

    def acquire_by_id(self, persona_id: str) -> ReadyPersona:
        """Reload a SPECIFIC persona (crash resume): it is already in_play, so no
        readiness gate and no state change — just row + tokens."""
        row = self._repo.get_persona(persona_id)
        if not row:
            raise RuntimeError(f"persona {persona_id!r} not found — cannot resume")
        token, secret = self._resolve(row["oauth_ref"])
        return ReadyPersona(
            id=row["id"],
            handle=row["handle"],
            x_user_id=row["x_user_id"],
            access_token=token,
            access_secret=secret,
        )

    def mark_retired(self, persona_id: str) -> None:
        delete_after = (self._now() + timedelta(days=DELETE_AFTER_DAYS)).isoformat()
        self._repo.set_persona_state(persona_id, "retired", delete_after=delete_after)
