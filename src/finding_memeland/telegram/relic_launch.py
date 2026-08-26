"""Relic launch staging — what the operator sees (and, crucially, does NOT see)
before confirming a relic hunt.

BLIND-MODE INTERFACE RULE (Fable review of package 1, gravado): `peek_launchable`
returns the DECRYPTED identity because the bot needs it to write clues — but the
/launch prompt must NEVER show the relic's name, lore, artwork or code to the
operator. If the operator can read the name off Telegram, blind mode is dead at
the interface, and the "not even the game master knows" claim becomes false.

So this module builds the confirmation text from PUBLIC/STRUCTURAL facts only:
relic id, commitment hash, mint age, findability check result, prize, ladder
exemption. `assert_no_identity_leak()` is a hard backstop: it inspects the text
for the identity's own strings and raises rather than let a leak reach Telegram.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone


class IdentityLeak(RuntimeError):
    """Raised when operator-facing text would expose the hidden identity."""


# Shorter than this, a "secret" is noise: it would match almost any text and make
# the backstop fire constantly (a stub lore of "d" flags every prompt). Real
# names, codes, lore and art prompts are all comfortably longer.
_MIN_SECRET_LEN = 4


def assert_no_identity_leak(text: str, identity) -> None:
    """Backstop for every operator-facing string in a relic hunt. Checks the
    identity's own values (name, code, lore, image prompt) against the text.

    Deliberately strict and dumb: substring, case-insensitive. A false positive is
    a nuisance; a false negative kills the blind-mode claim.

    Values shorter than _MIN_SECRET_LEN are skipped: a 1-3 char "secret" matches
    almost any text (a stub lore of "d" would flag every prompt), and no real
    name, code, lore or art prompt is that short. Without this the check is
    useless-by-noise rather than strict."""
    low = (text or "").lower()
    secrets_ = {
        "name": getattr(identity, "name", ""),
        "claim code": getattr(identity, "claim_code", ""),
        "lore": getattr(identity, "description", ""),
        "artwork prompt": getattr(identity, "image_prompt", ""),
    }
    for label, value in secrets_.items():
        v = (value or "").strip().lower()
        if len(v) >= _MIN_SECRET_LEN and v in low:
            raise IdentityLeak(
                f"operator-facing text would leak the relic's {label} — blind mode "
                "forbids showing the hidden identity to the operator."
            )


@dataclass(frozen=True)
class RelicLaunchSummary:
    """Structural facts only — nothing here identifies the relic."""

    relic_id: str
    commitment: str
    minted_at: datetime | None
    contract: str | None
    prize_fmml: int
    ladder_exempt: bool
    findability_ok: bool
    findability_surface: str
    hunt_number: int | str = "?"
    # Eligibility floor (auditoria 2026-08-26, P1-7). O caminho das personas
    # mostra isto ao operador desde o susto do Hunt #4; o do relic não mostrava
    # nada — e é na hunt de 1B que um floor mal configurado custa mais caro.
    # Não revelam nada sobre o relic.
    holding_floor_fmml: int = 0
    non_holder_prize_pct: int = 100

    def age_days(self, now: datetime | None = None) -> int | None:
        if not self.minted_at:
            return None
        now = now or datetime.now(timezone.utc)
        return (now - self.minted_at).days


def build_launch_prompt(summary: RelicLaunchSummary, identity=None) -> str:
    """The Telegram confirmation text for a relic hunt.

    Shows the commitment (which reveals nothing but proves WHICH relic is
    committed) and the aging/findability facts the operator actually needs to
    decide. Never the name. If `identity` is passed, the leak backstop runs."""
    age = summary.age_days()
    age_line = f", aged {age}d in the pool" if age is not None else ""
    exempt_line = (
        "🎁 SURPRISE hunt: exempt from the jackpot ladder (won't raise or reset it).\n"
        if summary.ladder_exempt else ""
    )
    find_line = (
        f"findability: ✅ indexed on {summary.findability_surface}\n"
        if summary.findability_ok
        else f"findability: ⛔ NOT indexed on {summary.findability_surface}\n"
    )
    floor = int(summary.holding_floor_fmml or 0)
    if floor:
        floor_line = (
            f"floor: {floor:,} $FIND no claim para 100% — non-holders ganham "
            f"{summary.non_holder_prize_pct}%.\n"
        )
    else:
        floor_line = (
            "🚨 floor ZERO — qualquer wallet ganha 100% do pote.\n"
        )
    text = (
        f"Hunt #{summary.hunt_number}: {summary.prize_fmml:,} $FIND on a RELIC.\n"
        f"relic {summary.relic_id} (blind — the name is not shown, by design)"
        f"{age_line}.\n"
        f"commitment: {summary.commitment[:16]}…\n"
        + (f"contract: {summary.contract}\n" if summary.contract else "")
        + find_line
        + floor_line
        + exempt_line
        + "⚠️ The launch is INSTANT — Clue 1 goes out in seconds, no take-backs.\n"
        "Confirm? reply 'sim' or 'não' (expires in 2 min)."
    )
    if identity is not None:
        assert_no_identity_leak(text, identity)
    return text


def stage_relic_launch(
    *,
    pool,                    # relic_pool.RelicPool
    prize_fmml: int,
    ladder_exempt: bool,
    canonical_findability,   # relic_findability.FindabilityCheck (BaseScan)
    secondary_findability: tuple = (),
    hunt_number: int | str = "?",
    holding_floor_fmml: int = 0,
    non_holder_prize_pct: int = 100,
):
    """Pick the oldest launchable relic, run the FAIL-CLOSED findability gate, and
    build the (identity-free) confirmation prompt.

    Returns (summary, prompt, relic, identity). The identity travels in memory to
    the clue engine ONLY — never into the prompt (enforced by the backstop) and
    never into a log or notification."""
    from ..persona.relic_findability import assert_findable_or_refuse

    relic, identity = pool.peek_launchable()

    # Fail-closed: if the canonical surface doesn't index it, this raises and the
    # hunt does not start (same discipline as R3).
    report = assert_findable_or_refuse(
        identity.name,
        canonical=canonical_findability,
        secondary=secondary_findability,
        contract=relic.contract,
    )

    summary = RelicLaunchSummary(
        relic_id=relic.id,
        commitment=relic.commitment or "",
        minted_at=relic.minted_at,
        contract=relic.contract,
        prize_fmml=prize_fmml,
        ladder_exempt=ladder_exempt,
        findability_ok=report.canonical_ok,
        findability_surface=report.canonical_surface,
        hunt_number=hunt_number,
        holding_floor_fmml=holding_floor_fmml,
        non_holder_prize_pct=non_holder_prize_pct,
    )
    prompt = build_launch_prompt(summary, identity)  # backstop runs here
    return summary, prompt, relic, identity
