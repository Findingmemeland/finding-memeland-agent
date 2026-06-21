"""Ports — the interfaces the Orchestrator depends on.

The Orchestrator is wired against these Protocols, not concrete classes, so the
same flow runs against real services (X, Base, Supabase) OR against in-memory
fakes for a full local simulation (see simulation.py). Real adapters land with
steps 26 (DM listener) and 27 (payout).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Protocol, runtime_checkable


@dataclass
class ReadyPersona:
    """A warmed, OAuth-authorized account from the pipeline, ready for a hunt."""
    id: str
    handle: str            # neutral @, never changes
    x_user_id: str         # permanent numeric id — ingredient of the integrity hash
    access_token: str
    access_secret: str


@dataclass
class Submission:
    """An inbound DM to the main account."""
    dm_id: str
    sender_x_id: str
    sender_handle: str
    body: str
    created_at: datetime


@dataclass
class Winner:
    submission: Submission
    wallet: str


@dataclass
class PayoutReceipt:
    tx_hash: str
    amount_fmml: int


@runtime_checkable
class Clock(Protocol):
    def now(self) -> datetime: ...
    def sleep(self, seconds: float) -> None: ...


class Publisher(Protocol):
    """Posts on the main @FindingMemeland account."""
    def post(self, text: str, *, long_post: bool = False) -> str: ...
    def reply_dm(self, recipient_x_id: str, text: str) -> None: ...


class DMSource(Protocol):
    def poll(self, since_id: str | None) -> list[Submission]: ...


class Validator(Protocol):
    def validate(self, parsed, hunt): ...  # returns object with .won and .outcome


class PayoutPort(Protocol):
    def send_prize(self, *, hunt_id, to_wallet: str, amount_fmml: int) -> PayoutReceipt: ...


class PriceFeed(Protocol):
    def usd_to_fmml(self, usd: float) -> int: ...


class Notifier(Protocol):
    def notify(self, text: str) -> None: ...


class PersonaSource(Protocol):
    def acquire_ready(self) -> ReadyPersona: ...
    def mark_retired(self, persona_id: str) -> None: ...


class Dresser(Protocol):
    def dress(self, *, access_token: str, access_secret: str, identity, claim_code: str,
              avatar_path: str | None = None): ...
    def retire(self, *, access_token: str, access_secret: str): ...


class HuntRepo(Protocol):
    def create_hunt(self, **fields) -> int: ...
    def set_hunt_state(self, hunt_id, state: str, **fields) -> None: ...
    def record_clue(self, **fields) -> None: ...
    def log_submission(self, **fields) -> None: ...
    def submissions_for_hunt(self, hunt_id) -> list: ...
    def record_winner(self, **fields) -> None: ...
    def record_payout(self, **fields) -> None: ...
