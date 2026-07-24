"""DM source adapter — turns the main account's inbound DMs into Submissions.

Implements the Orchestrator's DMSource port (poll(since_id) -> list[Submission]).
The Orchestrator owns the polling loop and the since_id marker (polls every
15-30s while a hunt is live, stops at the winner), so this adapter is a thin map
from x_client.read_dms() output to the Submission type — no duplicated control
flow.
"""

from __future__ import annotations

from ..orchestrator.ports import Submission


def _to_submissions(events) -> list[Submission]:
    return [
        Submission(
            dm_id=e["dm_id"],
            sender_x_id=e["sender_x_id"],
            sender_handle=e.get("sender_handle", ""),
            body=e.get("text", ""),
            created_at=e["created_at"],
        )
        for e in events
    ]


class XDMSource:
    def __init__(self, x_client):
        self._x = x_client

    def poll(self, since_id: str | None) -> list[Submission]:
        """Account-level read — DISCOVERY only (the endpoint suppresses
        follow-ups of replied conversations; Hunt #2 post-mortem)."""
        return _to_submissions(self._x.read_dms(since_id=since_id))

    def poll_conversation(
        self, participant_id: str, since_id: str | None
    ) -> list[Submission]:
        """Per-conversation read — the reliable path for follow-ups."""
        return _to_submissions(
            self._x.read_conversation_dms(participant_id, since_id=since_id)
        )
