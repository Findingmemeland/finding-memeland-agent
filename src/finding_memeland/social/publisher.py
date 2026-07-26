"""XPublisher — the Orchestrator's Publisher port, backed by the main account.

Thin adapter over x_client: posts game content on @FindingMemeland and sends the
canned DM replies to non-winning submissions. The x_client is duck-typed (not
imported), so this module stays import-light and testable.
"""

from __future__ import annotations


class XPublisher:
    def __init__(self, x_client):
        self._x = x_client

    def post(self, text: str, *, long_post: bool = False) -> str:
        return self._x.post(text, long_post=long_post)

    def reply_dm(self, recipient_x_id: str, text: str) -> None:
        self._x.reply_dm(recipient_x_id, text)

    def reply_post(self, text: str, *, in_reply_to: str) -> str:
        """Claim-by-post public replies (taunts, system messages, wallet ask)."""
        return self._x.reply_post(text, in_reply_to=in_reply_to)
