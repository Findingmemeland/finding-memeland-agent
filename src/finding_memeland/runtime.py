"""Small runtime adapters that implement the simple Orchestrator ports.

Kept dependency-light (no tweepy/web3/supabase imports) so they're testable; the
heavy clients are built in the composition root (main.py) and injected.
"""

from __future__ import annotations

import math
import os
import tempfile
from datetime import datetime, timezone


def round_sig(n: float, sig: int = 3) -> int:
    """Round to `sig` significant figures and return a clean whole number,
    e.g. 16,260,163 -> 16,300,000. Keeps prize/holding amounts readable."""
    if n <= 0:
        return 0
    digits = sig - int(math.floor(math.log10(n))) - 1
    return int(round(n, digits))


class SystemClock:
    """Real Clock port: wall-clock time + real sleep."""

    def now(self) -> datetime:
        return datetime.now(timezone.utc)

    def sleep(self, seconds: float) -> None:
        import time

        time.sleep(seconds)


def write_temp_png(data: bytes) -> str:
    """Persist image bytes to a temp .png file and return its path (for the
    dresser's avatar/banner upload)."""
    fd, path = tempfile.mkstemp(suffix=".png")
    with os.fdopen(fd, "wb") as fh:
        fh.write(data)
    return path


class ManualPriceFeed:
    """Converts a USD prize/floor to a WHOLE-token amount at a manually-set price.

    Until a live DEX price source is wired (post token launch), the operator sets
    `usd_per_token` (e.g. 0.0001). usd_to_fmml(500) -> 5_000_000 tokens at that price.
    """

    def __init__(self, usd_per_token: float, *, sig_figs: int = 3):
        # Allow 0 at construction so the agent can boot before the token exists;
        # it fails clearly only when a hunt actually tries to price a prize.
        self._price = usd_per_token
        self._sig = sig_figs

    def usd_to_fmml(self, usd: float) -> int:
        if self._price <= 0:
            raise RuntimeError("FMML_USD_PRICE not set — set it before launching a hunt")
        # Round to a clean whole number so the post and payout show e.g.
        # 16,300,000 $FIND, not 16,260,163. Same value used for post AND transfer.
        return round_sig(usd / self._price, self._sig)


class StdoutNotifier:
    """Fallback Notifier: prints. Replaced by the Telegram notifier in prod."""

    def notify(self, text: str) -> None:
        print(f"[notify] {text}")


class TelegramNotifier:
    """Production Notifier: pushes hunt events (LIVE, winner, errors) to the
    admin's Telegram chat via the Bot API. Best-effort BY DESIGN — a notify
    failure must never break the hunt, so it falls back to stdout and never
    raises. Uses plain HTTP (httpx) rather than the python-telegram-bot app,
    which is busy running the command loop in another thread."""

    def __init__(self, bot_token: str, chat_id: str, *, timeout_s: float = 10.0):
        self._url = f"https://api.telegram.org/bot{bot_token}/sendMessage"
        self._chat_id = chat_id
        self._timeout = timeout_s

    def notify(self, text: str) -> None:
        print(f"[notify] {text}")  # always keep the local trail
        try:
            import httpx

            httpx.post(
                self._url,
                json={"chat_id": self._chat_id, "text": f"🏴 {text}"[:4096]},
                timeout=self._timeout,
            )
        except Exception as e:  # noqa: BLE001 — never let a notify kill anything
            print(f"[notify] telegram delivery failed (non-fatal): {e!r}")


class HuntControl:
    """In-MEMORY kill switch — kept for the live test only (ephemeral by design).

    PRODUCTION uses DBHuntPause instead: the Genesis post-mortem showed a
    threading.Event dies with the process, so after a Railway restart a
    deliberately paused hunt silently UN-pauses itself when resume_hunts picks
    it back up.

    /silence -> pause(): the loop idles (no clues, no DM processing, no paying)
    /resume  -> resume(): the loop picks up exactly where it left off.
    Thread-safe (threading.Event); pausing never loses DMs — X buffers them and
    the winner is decided by DM ARRIVAL time, so fairness is unaffected."""

    def __init__(self):
        import threading

        self._paused = threading.Event()

    def pause(self) -> None:
        self._paused.set()

    def resume(self) -> None:
        self._paused.clear()

    def paused(self) -> bool:
        return self._paused.is_set()


class DBHuntPause:
    """Production kill switch, persisted on the hunt row (hunts.paused).

    Post-mortem lesson (Genesis, 2026-07): state critical to a live hunt must
    not live in process memory — a restart loses it, and during a deploy
    overlap (Railway starts the new container before the old one dies) the two
    instances don't share it. The pause is a property of the HUNT, not of the
    agent: it lives on the hunt row, resume_hunts recovers it for free (the
    loop re-reads it every cycle), and it is never inherited by the next hunt.

    Semantics:
    - pause()/resume() write through to the DB and RAISE on failure — the
      operator must know a kill-switch command didn't stick. They return how
      many active hunts were touched (0 = nothing to pause).
    - paused() reads the DB; on a read failure it returns the LAST KNOWN value:
      a transient DB hiccup must neither un-pause a deliberately paused hunt
      nor pause a healthy one.
    """

    def __init__(self, repo):
        self._repo = repo
        self._last = False

    def pause(self) -> int:
        rows = self._repo.active_hunts()
        for r in rows:
            self._repo.set_hunt_paused(r["id"], True)
        self._last = bool(rows)
        return len(rows)

    def resume(self) -> int:
        rows = self._repo.active_hunts()
        for r in rows:
            self._repo.set_hunt_paused(r["id"], False)
        self._last = False
        return len(rows)

    def paused(self) -> bool:
        try:
            self._last = any(r.get("paused") for r in self._repo.active_hunts())
        except Exception:  # noqa: BLE001 — DB hiccup: keep the last known value
            pass
        return self._last


def active_hunt_guard(repo) -> str | None:
    """DB-backed 'one hunt at a time' guard for /launch.

    The DB is the ONLY source of truth shared across restarts and overlapping
    deploy instances: a resumed hunt, or a hunt launched by the other container
    in the overlap window, is invisible to this process's hunt_flag — but not
    to the hunts table. (Genesis post-mortem: after a restart the in-memory
    flag said 'idle' while a hunt was LIVE in the DB, and /launch would have
    happily started a second one.)

    Returns a refusal message, or None when launching is safe. A failed DB
    check refuses too — with money on the line, never launch blind."""
    try:
        rows = repo.active_hunts()
    except Exception as e:  # noqa: BLE001
        return (
            f"⚠️ could not check the DB for active hunts ({e!r}) — "
            "NOT launching blind. Fix the DB connection and try again."
        )
    if rows:
        r = rows[0]
        extra = f" (+{len(rows) - 1} more?!)" if len(rows) > 1 else ""
        return (
            f"⛔ hunt #{r.get('id')} is '{r.get('state')}' in the DB{extra} — "
            "one at a time. If it was already settled manually, close it in "
            "the DB first (state='done')."
        )
    return None


def hunt_status_line(repo, *, local_active: bool) -> str:
    """The /status headline, read from the DB (the truth), not process memory.

    Also surfaces the two inconsistencies that should never happen: a local
    hunt thread without a DB row, and more than one active hunt."""
    try:
        rows = repo.active_hunts()
    except Exception as e:  # noqa: BLE001
        return f"hunt: ⚠️ DB check failed ({e!r}) — trust nothing, check Supabase"
    if not rows:
        line = "hunt: none (idle)"
        if local_active:
            line += " ⚠️ local hunt thread running with NO active DB row — investigate"
        return line
    r = rows[0]
    line = f"hunt: #{r.get('id')} {str(r.get('state', '?')).upper()}"
    if r.get("paused"):
        line += " | ⏸ PAUSED (/resume to continue)"
    if len(rows) > 1:
        line += (
            f" ⚠️ +{len(rows) - 1} MORE active hunt(s) in the DB — "
            "should be impossible, investigate NOW"
        )
    return line


class PollHeartbeat:
    """Liveness sensor for the hunt loop (post-mortem P0 pack).

    The Genesis failure mode: the loop stops completing cycles (hung HTTP call,
    dead thread) and NOTHING says so — errors notify, but silence doesn't. The
    loop calls beat() once per cycle; a supervisor thread calls check() every
    minute and forwards the returned alert (if any) to Telegram.

    - mark_live(True/False) brackets the live loop (set by the orchestrator).
    - Alerts only while live and stalled for > stall_after_s; re-alerts at most
      every realert_s so a real incident keeps screaming without flooding.
    - stall_after_s must exceed the loop's longest legitimate cycle
      (poll_interval + max failure backoff ≈ 75s + 300s), hence the 600s
      default (WATCHDOG_STALL_S to override)."""

    def __init__(self, *, stall_after_s: int = 600, realert_s: int = 900, now_fn=None):
        self._stall = stall_after_s
        self._realert = realert_s
        self._now = now_fn or (lambda: datetime.now(timezone.utc))
        self._live = False
        self._last_beat: datetime | None = None
        self._last_alert: datetime | None = None

    def mark_live(self, live: bool) -> None:
        self._live = live
        self._last_beat = self._now() if live else None
        self._last_alert = None

    def beat(self) -> None:
        self._last_beat = self._now()

    def check(self) -> str | None:
        if not self._live or self._last_beat is None:
            return None
        now = self._now()
        stalled_s = (now - self._last_beat).total_seconds()
        if stalled_s < self._stall:
            return None
        if self._last_alert is not None and (now - self._last_alert).total_seconds() < self._realert:
            return None
        self._last_alert = now
        return (
            f"🚨 hunt is LIVE but the loop hasn't completed a cycle in "
            f"{int(stalled_s // 60)} min — it may be HUNG or DEAD. Players' DMs "
            "are NOT being processed: check the DM inbox BY HAND and the "
            "Railway process now."
        )


def env_token_resolver(oauth_ref: str) -> tuple[str, str]:
    """Resolve a persona's OAuth tokens from env (Doppler injects them) by ref,
    e.g. ref '01' -> X_PERSONA_01_ACCESS_TOKEN / X_PERSONA_01_ACCESS_SECRET."""
    token = os.environ.get(f"X_PERSONA_{oauth_ref}_ACCESS_TOKEN", "")
    secret = os.environ.get(f"X_PERSONA_{oauth_ref}_ACCESS_SECRET", "")
    if not token or not secret:
        raise RuntimeError(f"missing OAuth tokens for persona ref {oauth_ref!r}")
    return token, secret
