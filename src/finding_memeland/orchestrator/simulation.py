"""In-memory simulation harness for the Orchestrator.

Wires the Orchestrator with fakes for every external dependency so a full hunt
runs locally and deterministically — no X, no chain, no DB. This is the engine
behind the end-to-end test and the `simulate_hunt.py` dry-run (checklist step 29).

Real components can be injected (e.g. the real PersonaGenerator + ClueEngine in
the dry-run script) while everything with side effects stays faked.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

from ..content.clue_engine import ClueDraft
from ..dm.validator import ValidationResult
from ..persona.generator import GeneratedPersona
from .ports import PayoutReceipt, ReadyPersona, Submission
from .state_machine import Orchestrator


# ---------------------------------------------------------------------------
# Fakes
# ---------------------------------------------------------------------------
class FakeClock:
    def __init__(self, start: datetime | None = None):
        self._t = start or datetime(2026, 8, 1, 12, 0, tzinfo=timezone.utc)

    def now(self) -> datetime:
        return self._t

    def sleep(self, seconds: float) -> None:
        self._t += timedelta(seconds=seconds)


class FakeSettings:
    prize_usd_max = 500

    def assert_ready_for_hunt(self) -> None:
        return None


class FakeRepo:
    def __init__(self):
        self.hunts: dict[int, dict] = {}
        self.clues: list[dict] = []
        self.submissions: list[dict] = []
        self.winners: list[dict] = []
        self.payouts: list[dict] = []
        self._next_id = 1

    def create_hunt(self, **fields) -> int:
        hid = self._next_id
        self._next_id += 1
        self.hunts[hid] = dict(fields)
        return hid

    def set_hunt_state(self, hunt_id, state, **fields) -> None:
        self.hunts[hunt_id]["state"] = state
        self.hunts[hunt_id].update(fields)

    def record_clue(self, **fields) -> None:
        self.clues.append(dict(fields))

    def log_submission(self, **fields) -> None:
        self.submissions.append(dict(fields))

    def submissions_for_hunt(self, hunt_id) -> list:
        return [s for s in self.submissions if s.get("hunt_id") == hunt_id]

    def record_winner(self, **fields) -> None:
        self.winners.append(dict(fields))

    def record_payout(self, **fields) -> None:
        self.payouts.append(dict(fields))

    def latest_claim_code(self) -> str:
        return self.hunts[self._next_id - 1]["claim_code"]


class FakePersonaSource:
    def __init__(self):
        self.retired: list[str] = []

    def acquire_ready(self) -> ReadyPersona:
        return ReadyPersona(
            id="persona-1", handle="@sample_persona", x_user_id="100200300",
            access_token="tok", access_secret="sec",
        )

    def mark_retired(self, persona_id: str) -> None:
        self.retired.append(persona_id)


class FakePersonaGenerator:
    def generate(self, *, register=None, avoid_recent=None) -> GeneratedPersona:
        return GeneratedPersona(
            display_name="burning daylight",
            bio="clocks are a conspiracy and i arrived late to my own funeral",
            avatar_prompt="a sundial dissolving into fog, painterly",
            banner_prompt="a blank map with one coastline drawn, sepia",
            voice="terse, ironic",
            backstory="A famous explorer whose name ended up on two continents.",
            archetype="historical figure (dead 50+ years)",
            solution_terms=["Amerigo", "Vespucci"],
            findable_post="still charting the hum of a coastline nobody named yet",
        )


class FakeAvatarGenerator:
    def generate_png(self, prompt: str) -> bytes:
        return b""  # empty -> orchestrator skips avatar upload

    def generate_banner_png(self, prompt: str) -> bytes:
        return b""  # empty -> orchestrator skips banner upload


class FakeClueEngine:
    def next_clue(self, ctx, clue_index: int, prior_clues) -> ClueDraft:
        return ClueDraft(
            text=f"oblique hint #{clue_index} — read between the lines",
            taunt=None if clue_index == 1 else "c'mon you lazy degens",
        )


class FakeDresser:
    def __init__(self):
        self.dressed = False
        self.retired = False

    def dress(self, **kwargs):
        self.dressed = True
        return None

    def retire(self, **kwargs):
        self.retired = True
        return None


class FakePublisher:
    def __init__(self, verbose: bool = False):
        self.posts: list[str] = []
        self.dm_replies: list[tuple[str, str]] = []
        self._verbose = verbose
        self._n = 0

    def post(self, text: str, *, long_post: bool = False) -> str:
        self._n += 1
        self.posts.append(text)
        if self._verbose:
            print(f"\n>>> POST {self._n} {'(long)' if long_post else ''}\n{text}\n")
        return f"tweet-{self._n}"

    def reply_dm(self, recipient_x_id: str, text: str) -> None:
        self.dm_replies.append((recipient_x_id, text))
        if self._verbose:
            print(f"    [DM reply -> {recipient_x_id}] {text}")


class FakeValidator:
    """Replicates the cost-ordered validation outcome; holding/reshare/bot faked OK."""

    def validate(self, parsed, hunt) -> ValidationResult:
        if not parsed.wallet:
            return ValidationResult(False, "malformed")
        if not parsed.claim_code or parsed.claim_code != hunt.claim_code:
            return ValidationResult(False, "bad_code", check_code=False)
        return ValidationResult(
            True, "won", check_code=True, check_holding=True,
            check_reshare=True, check_bot=True,
        )


class FakePayout:
    def __init__(self):
        self.sent: list[dict] = []

    def send_prize(self, *, hunt_id, to_wallet, amount_fmml) -> PayoutReceipt:
        self.sent.append({"hunt_id": hunt_id, "wallet": to_wallet, "amount": amount_fmml})
        return PayoutReceipt(tx_hash=f"0xtx{hunt_id}", amount_fmml=amount_fmml)


class FakePriceFeed:
    def usd_to_fmml(self, usd: float) -> int:
        return int(usd * 1000)  # arbitrary fixed rate for the sim


class FakeNotifier:
    def __init__(self, verbose: bool = False):
        self.messages: list[str] = []
        self._verbose = verbose

    def notify(self, text: str) -> None:
        self.messages.append(text)
        if self._verbose:
            print(f"[notify] {text}")


class ScriptedDMSource:
    """Emits a losing submission, then a winning one that carries the real claim
    code (read from the repo at poll time)."""

    def __init__(self, repo: FakeRepo, clock: FakeClock, *, win_after_polls: int = 3,
                 winner_handle: str = "sharp_anon", winner_x_id: str = "9001",
                 wallet: str = "0x" + "a" * 40):
        self._repo = repo
        self._clock = clock
        self._win_after = win_after_polls
        self._winner_handle = winner_handle
        self._winner_x_id = winner_x_id
        self._wallet = wallet
        self._polls = 0

    def poll(self, since_id):
        self._polls += 1
        if self._polls == 1:
            return [Submission(
                dm_id="dm-loser", sender_x_id="6660", sender_handle="early_bird",
                body="is the code WRONGCOD and my wallet " + "0x" + "b" * 40,
                created_at=self._clock.now(),
            )]
        if self._polls == self._win_after:
            code = self._repo.latest_claim_code()
            return [Submission(
                dm_id="dm-winner", sender_x_id=self._winner_x_id,
                sender_handle=self._winner_handle,
                body=f"found you. code is {code} and wallet {self._wallet}",
                created_at=self._clock.now(),
            )]
        return []


@dataclass
class SimRig:
    orchestrator: Orchestrator
    repo: FakeRepo
    publisher: FakePublisher
    dresser: FakeDresser
    persona_source: FakePersonaSource
    payout: FakePayout
    notifier: FakeNotifier
    clock: FakeClock


def build_simulation(
    *,
    persona_generator=None,
    clue_engine=None,
    win_after_polls: int = 3,
    poll_interval_s: int = 4000,
    register: str = "medium",
    hunt_number: int = 1,
    verbose: bool = False,
) -> SimRig:
    clock = FakeClock()
    repo = FakeRepo()
    publisher = FakePublisher(verbose=verbose)
    dresser = FakeDresser()
    persona_source = FakePersonaSource()
    payout = FakePayout()
    notifier = FakeNotifier(verbose=verbose)
    dm_source = ScriptedDMSource(repo, clock, win_after_polls=win_after_polls)

    orch = Orchestrator(
        settings=FakeSettings(),
        clock=clock,
        repo=repo,
        persona_source=persona_source,
        persona_generator=persona_generator or FakePersonaGenerator(),
        avatar_generator=FakeAvatarGenerator(),
        dresser=dresser,
        publisher=publisher,
        clue_engine=clue_engine or FakeClueEngine(),
        dm_source=dm_source,
        validator=FakeValidator(),
        payout=payout,
        price_feed=FakePriceFeed(),
        notifier=notifier,
        hunt_number=hunt_number,
        register=register,
        poll_interval_s=poll_interval_s,
    )
    return SimRig(
        orchestrator=orch, repo=repo, publisher=publisher, dresser=dresser,
        persona_source=persona_source, payout=payout, notifier=notifier, clock=clock,
    )
