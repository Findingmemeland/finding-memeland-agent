"""Telegram approval queue (NON-GAME posts only) + admin commands.

Scope (memory 2026-06-09): approval applies ONLY to pinned rules, filler/teaser
and comms. ALL game posts (Clue 1, clues, Winner Announcement) publish
autonomously with NO approval — operational blindness is part of the integrity
protocol, so the operator must not see clues before they publish.

Admin commands (from the hardcoded admin chat id only):
  /launch   — trigger a new hunt (manual trigger, not cron)
  /silence  — kill switch: pause the agent
  /resume   — resume after a pause
  /status   — current hunt + pipeline state

This module keeps the LOGIC (auth, routing, approve/reject) free of the telegram
SDK so it is unit-testable; the python-telegram-bot wiring (TelegramAdmin) imports
the SDK lazily.
"""

from __future__ import annotations

# Post kinds that may go through approval. Game posts are deliberately excluded.
NON_GAME_KINDS = frozenset({"pinned_rules", "filler", "comms"})
_GAME_KINDS = frozenset({"clue_one", "clue", "winner_announcement"})


def route_command(command: str, *, is_admin: bool, actions: dict) -> str:
    """Authenticate + route an admin command to an action callback.

    `actions` maps "launch"/"silence"/"resume"/"status" to callables that take the
    argument string (text after the command, e.g. "200" for "/launch 200") and
    return a response string. Pure and side-effect-free except via the callbacks.
    """
    if not is_admin:
        return "unauthorized"
    parts = command.lstrip("/").strip().split(maxsplit=1)
    name = parts[0].lower() if parts else ""
    arg = parts[1].strip() if len(parts) > 1 else ""
    fn = actions.get(name)
    if fn is None:
        return f"unknown command: /{name}" if name else "no command"
    return fn(arg) or "ok"


class ApprovalQueue:
    """Persist non-game drafts and publish them on approval."""

    def __init__(self, *, repo, publisher):
        self._repo = repo
        self._publisher = publisher

    def submit_for_approval(self, *, kind: str, draft_text: str, telegram_msg_id: str | None = None) -> int:
        if kind in _GAME_KINDS:
            raise ValueError(f"game post '{kind}' must publish autonomously, never queued")
        if kind not in NON_GAME_KINDS:
            raise ValueError(f"unknown post kind: {kind}")
        return self._repo.create_approval(
            kind=kind, draft_text=draft_text, telegram_msg_id=telegram_msg_id
        )

    def decide(self, approval_id: int, decision: str, *, edited_text: str | None = None) -> str:
        """Apply an admin decision. approve -> publish; reject -> discard.
        'approve' with edited_text publishes the edited version."""
        record = self._repo.get_approval(approval_id)
        if record is None:
            return "not found"
        if decision == "approve":
            text = edited_text or record["draft_text"]
            self._publisher.post(text)
            self._repo.set_approval_status(approval_id, "approved")
            return "published"
        if decision == "reject":
            self._repo.set_approval_status(approval_id, "rejected")
            return "rejected"
        raise ValueError(f"unknown decision: {decision}")


class TelegramAdmin:
    """python-telegram-bot wiring. Only the hardcoded admin chat may command it.

    The SDK is imported lazily so this module stays importable without it.
    Wiring is I/O — confirm live before production.
    """

    def __init__(self, *, bot_token: str, admin_chat_id: str, approval: ApprovalQueue, actions: dict):
        self._token = bot_token
        self._admin_chat_id = str(admin_chat_id)
        self._approval = approval
        self._actions = actions

    def _is_admin(self, chat_id) -> bool:
        return str(chat_id) == self._admin_chat_id

    def build_application(self):
        """Build the telegram Application with the admin command handlers.

        SDK imported lazily. Approve/reject inline-button wiring (for non-game
        posts) is a follow-up; the core admin commands are wired here. Confirm
        live before production.
        """
        from telegram.ext import Application, CommandHandler

        app = Application.builder().token(self._token).build()

        async def _handle(update, context):  # noqa: ANN001
            chat_id = update.effective_chat.id if update.effective_chat else None
            text = update.message.text if update.message else ""
            reply = route_command(text, is_admin=self._is_admin(chat_id), actions=self._actions)
            await update.message.reply_text(reply)

        app.add_handler(CommandHandler(["launch", "silence", "resume", "status"], _handle))
        return app

    def run(self) -> None:
        """Block, polling Telegram for admin commands. This is the live run loop."""
        self.build_application().run_polling()
