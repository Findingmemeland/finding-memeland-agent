"""Orchestrator — the hunt lifecycle state machine (LangGraph).

States (frozen, mirrors db hunt_state):

    idle
      -> preparing      (/launch): pick ready persona, generate identity +
                         claim code + salt, compute integrity hash, dress persona
      -> live           publish Clue 1 (announcement + reshare gate + hash);
                         start DM listener; drop clues on the easing schedule
      -> resolving      first valid DM received; stop accepting submissions
      -> paying         send prize from hot wallet
      -> pending_cleanup publish Winner Announcement; hold persona live for 1h
      -> retiring        wipe + schedule deletion of the persona
      -> idle            ready for the next manual trigger

A platform interruption sends the hunt to `voided` (re-run with original
eligibility snapshot — proposal in Decisões Cruzadas v1.2).

Concrete here: the transition table and guards. Node bodies call the modules
and are filled in step 25.
"""

from __future__ import annotations

from enum import Enum


class HuntState(str, Enum):
    IDLE = "idle"
    PREPARING = "preparing"
    LIVE = "live"
    RESOLVING = "resolving"
    PAYING = "paying"
    PENDING_CLEANUP = "pending_cleanup"
    RETIRING = "retiring"
    DONE = "done"
    VOIDED = "voided"


# Allowed transitions. Any move not listed here is a bug and the supervisor halts.
TRANSITIONS: dict[HuntState, set[HuntState]] = {
    HuntState.IDLE: {HuntState.PREPARING},
    HuntState.PREPARING: {HuntState.LIVE, HuntState.VOIDED},
    HuntState.LIVE: {HuntState.RESOLVING, HuntState.VOIDED},
    HuntState.RESOLVING: {HuntState.PAYING, HuntState.VOIDED},
    HuntState.PAYING: {HuntState.PENDING_CLEANUP, HuntState.VOIDED},
    HuntState.PENDING_CLEANUP: {HuntState.RETIRING},
    HuntState.RETIRING: {HuntState.DONE},
    HuntState.DONE: set(),
    HuntState.VOIDED: {HuntState.RETIRING},
}

CLEANUP_WINDOW_SECONDS = 60 * 60  # 1h reveal window before retiring the persona


def can_transition(src: HuntState, dst: HuntState) -> bool:
    return dst in TRANSITIONS.get(src, set())


class Orchestrator:
    """Wires the modules into the lifecycle. Built with LangGraph in step 25."""

    def __init__(
        self,
        *,
        settings,
        repo,
        persona_generator,
        persona_dresser,
        clue_engine,
        x_client,
        dm_listener,
        payout_engine,
        supervisor,
    ):
        self._settings = settings
        self._repo = repo
        self._persona_generator = persona_generator
        self._persona_dresser = persona_dresser
        self._clue_engine = clue_engine
        self._x = x_client
        self._dm_listener = dm_listener
        self._payout = payout_engine
        self._supervisor = supervisor

    async def launch_hunt(self) -> None:
        """Entry point for /launch. Drives idle -> ... -> done.

        Guard: settings.assert_ready_for_hunt() must pass (token address, hot
        wallet, salt, cap all set) before anything is published.

        TODO(step 25): build the LangGraph graph from TRANSITIONS; implement node
        bodies:
          preparing -> pick persona, generate identity+code+salt, compute hash,
                       dress persona, resolve prize_usd -> prize_fmml at price
          live      -> publish Clue 1, schedule clues (easing + 1-3h), run listener
          resolving -> on winner, stop listener
          paying    -> payout.send_prize (cap-enforced), record winner
          pending_cleanup -> winner announcement (reveals code+salt), wait 1h
          retiring  -> dresser.retire, publish submission log, back to idle
        """
        self._settings.assert_ready_for_hunt()
        raise NotImplementedError("hunt graph — implemented in step 25")
