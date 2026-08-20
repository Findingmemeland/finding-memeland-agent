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


def findability_ready_at(
    account_created_at, phone_verified, *, min_days: int
) -> datetime | None:
    """WHEN the account becomes findability-ready (created + min_days), or None
    when it never will on its own (phone not verified / unknown creation date —
    both need operator action, not waiting). Pure; feeds refusal messages."""
    if not phone_verified:
        return None
    created = _as_dt(account_created_at)
    if created is None:
        return None
    return created + timedelta(days=min_days)


def split_dressed_by_findability(
    rows, *, min_days: int, now: datetime | None = None
):
    """Split the dressed pool into (eligible, waiting) keeping dressed_at order.
    `waiting` pairs each row with its ready-at datetime (None = needs operator:
    phone unverified or no creation date). Pure — the launch gate (2026-08-20:
    dressing while in warmup is the DESIGN — the account indexes already
    dressed; findability only ever blocks the LAUNCH, never the dress)."""
    now = now or _utcnow()
    eligible, waiting = [], []
    for row in rows:
        if persona_findability_ready(
            row.get("account_created_at"), row.get("phone_verified"),
            min_days=min_days, now=now,
        ):
            eligible.append(row)
        else:
            waiting.append((row, findability_ready_at(
                row.get("account_created_at"), row.get("phone_verified"),
                min_days=min_days,
            )))
    return eligible, waiting


def _waiting_line(row, ready_at) -> str:
    handle = str(row.get("handle") or "?")
    if ready_at is None:
        return f"{handle} (phone NOT verified / sem data de criação — corrige a row)"
    return f"{handle} (findability a {ready_at.strftime('%d/%m %H:%M')} UTC)"


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

    # ------------------------------------------------------------------
    # Pre-dressed pool (Fase 2, design 2026-08-12)
    # ------------------------------------------------------------------
    def peek_dressed(self) -> tuple[ReadyPersona, dict]:
        """The next persona from the DRESSED pool — the OLDEST dress first
        (Pedro's rule: dressed_at asc = most indexing time), WITHOUT any state
        change: the caller runs the R3 verification first and only then calls
        mark_in_play. If the oldest fails R3 the launch is REFUSED — never
        silently substituted by the next one (a divergence can mean someone
        touched the account; the operator must know).

        Findability gate (2026-08-20, moved here from /dress): personas may be
        dressed while still in warmup — they index dressed, which is the whole
        point of pre-dressing — but an under-prepared account must never carry
        a hunt. Rows not findability-ready are SKIPPED (visible in the /launch
        prompt and /status, so never silent); if none qualifies the launch is
        refused with each persona's ready-at time.

        Returns (ReadyPersona with tokens, the full descriptor row)."""
        rows = self._repo.dressed_personas()
        if not rows:
            raise RuntimeError(
                "no 'dressed' persona in the pool — run /dress first "
                "(the old generate-at-launch flow is retired)"
            )
        eligible, waiting = split_dressed_by_findability(
            rows, min_days=self._min_days, now=self._now()
        )
        if not eligible:
            raise RuntimeError(
                "dressed pool has no findability-ready persona — launch refused. "
                "A aquecer: " + "; ".join(_waiting_line(r, at) for r, at in waiting)
            )
        row = eligible[0]  # repo orders by dressed_at asc — oldest = most indexed
        token, secret = self._resolve(row["oauth_ref"])
        persona = ReadyPersona(
            id=row["id"],
            handle=row["handle"],
            x_user_id=row["x_user_id"],
            access_token=token,
            access_secret=secret,
        )
        return persona, row

    def mark_in_play(self, persona_id: str) -> None:
        """dressed -> in_play, AFTER R3 passed. State only — every descriptor
        field stays intact on the row (the hunt reads them from the DB)."""
        self._repo.set_persona_state(persona_id, "in_play")

    def release_to_pool(self, persona_id: str) -> None:
        """in_play -> dressed: a hunt that died BEFORE going live (resume of a
        stuck PREPARING) returns the persona to the pool untouched — the dress
        and its weeks of indexing are NOT lost (no undress)."""
        self._repo.set_persona_state(persona_id, "dressed")

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
