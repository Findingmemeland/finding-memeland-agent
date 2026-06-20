"""Telegram approval queue — NON-GAME posts only, plus admin commands.

Scope (memory 2026-06-09): approval applies ONLY to pinned rules, filler/teaser
and comms. ALL game posts (Clue 1, clues 2+, Winner Announcement) publish
autonomously with NO approval — operational blindness is part of the integrity
protocol, so the operator must not see clues before they publish.

Admin commands (from the hardcoded admin chat id only):
  /launch   — trigger a new hunt (manual trigger, not cron)
  /silence  — kill switch: pause the agent
  /resume   — resume after a pause
  /status   — current hunt + pipeline state
"""

from __future__ import annotations


class ApprovalQueue:
    def __init__(self, *, bot_token: str, admin_chat_id: str, repo, orchestrator):
        self._token = bot_token
        self._admin_chat_id = admin_chat_id
        self._repo = repo
        self._orchestrator = orchestrator

    def _is_admin(self, chat_id: str) -> bool:
        return str(chat_id) == str(self._admin_chat_id)

    async def submit_for_approval(self, *, kind: str, draft_text: str) -> None:
        """Push a NON-game draft to the admin chat with approve/reject buttons.

        TODO(step 28): send message with inline buttons (approve / reject /
        regenerate / edit); persist to approval_queue; publish on approval.
        Reject this call if `kind` is a game post type — game posts never queue.
        """
        raise NotImplementedError("approval submit — implemented in step 28")

    async def handle_command(self, *, chat_id: str, command: str) -> str:
        """Route /launch /silence /resume /status — admin chat only.

        TODO(step 28/25): authenticate via _is_admin, then call the orchestrator.
        """
        if not self._is_admin(chat_id):
            return "unauthorized"
        raise NotImplementedError("command routing — implemented in step 28")
