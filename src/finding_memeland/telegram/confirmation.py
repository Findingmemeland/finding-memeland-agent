"""Launch confirmation gate (pre-dressing Fase 3, design 2026-08-12).

Since Fase 2, /launch is INSTANT: Clue 1 fires in seconds, with no prep window
and no take-backs. This gate is the one protection that replaces the old 24h
window of regret: /launch stages the hunt and asks; only an explicit "sim"
from the admin fires it, and the staged request expires quickly.

Pure logic, no Telegram SDK (same policy as approval_queue.route_command):
main.py wires it to a plain-text message handler.
"""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass

DEFAULT_EXPIRY_S = 120

_YES = {"sim"}
_NO = {"não", "nao"}


@dataclass(frozen=True)
class Resolution:
    outcome: str            # confirm | cancel | expired | noise | none
    prize_fmml: int | None = None
    expected_handle: str | None = None


class LaunchConfirmation:
    """One staged launch at a time. stage() replaces any previous request;
    resolve() consumes the reply. Thread-safe (the Telegram handlers and the
    hunt thread live in different threads)."""

    def __init__(self, *, expiry_s: int = DEFAULT_EXPIRY_S, now_fn=time.monotonic):
        self._expiry_s = expiry_s
        self._now = now_fn
        self._lock = threading.Lock()
        self._pending: dict | None = None

    def stage(self, prize_fmml: int, expected_handle: str) -> None:
        """Stage a launch request. expected_handle = the persona shown in the
        prompt; the confirmer re-checks it so the operator never confirms one
        persona and launches another."""
        with self._lock:
            self._pending = {
                "prize_fmml": int(prize_fmml),
                "expected_handle": expected_handle,
                "staged_at": self._now(),
            }

    def clear(self) -> None:
        with self._lock:
            self._pending = None

    def resolve(self, text: str) -> Resolution:
        """Interpret a plain-text admin message.

        - 'sim'        -> confirm (consumes the staged launch)
        - 'não'/'nao'  -> cancel (consumes)
        - anything else with a request staged -> noise (request kept)
        - anything with no request staged     -> none (ignore free text)
        - a stale request (past expiry) is consumed and reported as expired,
          whatever the answer — a "sim" minutes later must never fire a hunt.
        """
        norm = str(text or "").strip().lower()
        with self._lock:
            if self._pending is None:
                return Resolution("none")
            if self._now() - self._pending["staged_at"] > self._expiry_s:
                self._pending = None
                return Resolution("expired")
            if norm in _YES:
                pending, self._pending = self._pending, None
                return Resolution(
                    "confirm",
                    prize_fmml=pending["prize_fmml"],
                    expected_handle=pending["expected_handle"],
                )
            if norm in _NO:
                self._pending = None
                return Resolution("cancel")
            return Resolution("noise")
