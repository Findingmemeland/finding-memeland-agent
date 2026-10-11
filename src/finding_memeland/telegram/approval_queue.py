"""Telegram approval queue (NON-GAME posts only) + admin commands.

Scope (memory 2026-06-09): approval applies ONLY to pinned rules, filler/teaser
and comms. ALL game posts (Clue 1, clues, Winner Announcement) publish
autonomously with NO approval — operational blindness is part of the integrity
protocol, so the operator must not see clues before they publish.

Admin commands (from the hardcoded admin chat id only):
  /launch   — trigger a new hunt (manual trigger, not cron)
  /dress    — pre-dress a persona (identity + code + avatar + posts), weeks ahead
  /silence  — kill switch: pause the agent
  /resume   — resume after a pause
  /status   — current hunt + pipeline state

This module keeps the LOGIC (auth, routing, approve/reject) free of the telegram
SDK so it is unit-testable; the python-telegram-bot wiring (TelegramAdmin) imports
the SDK lazily.
"""

from __future__ import annotations

import asyncio
import logging
import time

# Post kinds that may go through approval. Game posts are deliberately excluded.
NON_GAME_KINDS = frozenset({"pinned_rules", "filler", "comms"})
_GAME_KINDS = frozenset({"clue_one", "clue", "winner_announcement"})


# Every admin command the Telegram handler listens to. python-telegram-bot
# IGNORES unregistered commands in silence — /scan and /snapshot were routed
# in main.py but missing here, and the first live /scan (10/09) got no reply
# at all. test_telegram_commands pins this set against main.py's actions.
TELEGRAM_COMMANDS = frozenset({
    "launch", "dress", "silence", "resume", "status", "post",
    "abort_prep", "delay_golive",
    "tease", "approve", "reject", "relic_new", "relic_mint",
    "scan", "snapshot", "fill", "harvest", "prepare", "probe",
})


# COMMANDS THAT RUN OFF THE ADMIN LOOP (10/10, Pedro: "o /status não pode
# bloquear os outros comandos — sobretudo o /silence, que é o travão de
# emergência e nunca pode esperar").
#
# Read in the code that day: the handler called the command's action INLINE,
# inside the Telegram event loop. While one command ran, the next update was
# not even read — and /status asks the network: the X API for the anchor
# when a hunt is live, and since that day the marketplace search, which
# takes its whole time limit when it does not answer (up to 50 s). A /silence
# sent meanwhile waited for all of it.
#
# A command listed here runs in a worker thread instead, and its reply is
# sent when it finishes: nothing waits for it. Only commands that READ may
# be listed — they run at the same time as whatever comes next. /silence is
# not one of them and must never be: the brake runs inline, at once.
OFF_LOOP_COMMANDS = frozenset({"status"})
# While one of those runs, a second one is told to wait instead of starting
# another (each /status costs requests to the search). After this long the
# first one is taken for stuck and no longer keeps a new one out — a /status
# nobody can run again until the next deploy would be its own outage.
OFF_LOOP_STALE_S = 120.0

_LOG = logging.getLogger(__name__)


def split_command(command: str) -> tuple[str, str]:
    """"/launch 200" -> ("launch", "200"). The one reading of a command's
    text: what is routed and what runs off the loop cannot disagree."""
    parts = command.lstrip("/").strip().split(maxsplit=1)
    name = parts[0].lower() if parts else ""
    arg = parts[1].strip() if len(parts) > 1 else ""
    return name, arg


def route_command(command: str, *, is_admin: bool, actions: dict) -> str:
    """Authenticate + route an admin command to an action callback.

    `actions` maps "launch"/"silence"/"resume"/"status" to callables that take the
    argument string (text after the command, e.g. "200" for "/launch 200") and
    return a response string. Pure and side-effect-free except via the callbacks.
    """
    if not is_admin:
        return "unauthorized"
    name, arg = split_command(command)
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

    def __init__(
        self, *, bot_token: str, admin_chat_id: str, approval: ApprovalQueue,
        actions: dict, on_text=None, clock=time.monotonic,
    ):
        self._token = bot_token
        self._admin_chat_id = str(admin_chat_id)
        self._approval = approval
        self._actions = actions
        # Optional plain-text handler (Fase 3: the sim/não launch confirmation).
        # callable(text) -> reply string, or None/"" to stay silent.
        self._on_text = on_text
        # OFF_LOOP_COMMANDS: which are running — since when, and a mark
        # that is that run's alone; and their tasks, held here because the
        # loop itself keeps only a weak reference to a task (one nobody
        # holds can vanish mid-flight).
        self._clock = clock
        self._running: dict[str, tuple[float, object]] = {}
        self._tasks: set = set()

    def _is_admin(self, chat_id) -> bool:
        return str(chat_id) == self._admin_chat_id

    async def handle_command(self, text: str, chat_id, reply) -> None:
        """One command, from its text to its reply. `reply` is an async
        callable taking the text to send.

        Every command runs INLINE and is answered before this returns, as it
        always was — except an admin's OFF_LOOP_COMMANDS: those start in a
        worker thread and this returns AT ONCE, so the next command (the
        brake) is read while they run. Their reply is sent when they finish.
        Touches `_running` and `_tasks` from the event loop only."""
        name, _arg = split_command(text)
        is_admin = self._is_admin(chat_id)
        if not is_admin or name not in OFF_LOOP_COMMANDS:
            await reply(route_command(text, is_admin=is_admin, actions=self._actions))
            return
        running = self._running.get(name)
        if running is not None and self._clock() - running[0] < OFF_LOOP_STALE_S:
            await reply(f"⏳ já há um /{name} a correr — a resposta chega "
                        "quando terminar")
            return
        mark = object()
        self._running[name] = (self._clock(), mark)
        task = asyncio.get_running_loop().create_task(
            self._run_off_loop(name, text, reply, mark, is_admin))
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    async def _run_off_loop(self, name: str, text: str, reply, mark: object,
                            is_admin: bool) -> None:
        try:
            try:
                # the flag the check above read, never a literal True: the
                # router still authenticates, whatever becomes of that check
                answer = await asyncio.to_thread(
                    route_command, text, is_admin=is_admin, actions=self._actions)
            except Exception as e:  # noqa: BLE001 — the type only: a message can cite a URL
                answer = f"⚠️ /{name} falhou ({type(e).__name__}) — tenta outra vez"
            try:
                await reply(answer)
            except Exception as e:  # noqa: BLE001 — nobody awaits this task: say it here
                _LOG.error("reply to /%s failed: %s", name, type(e).__name__)
        finally:
            running = self._running.get(name)
            if running is not None and running[1] is mark:   # not a newer run's
                del self._running[name]

    async def drain(self) -> None:
        """Wait for the off-loop commands still running. What a test awaits
        to read their replies; production never needs to."""
        while self._tasks:
            await asyncio.gather(*list(self._tasks), return_exceptions=True)

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
            await self.handle_command(
                text, chat_id, lambda answer: update.message.reply_text(answer))

        app.add_handler(CommandHandler(sorted(TELEGRAM_COMMANDS), _handle))

        if self._on_text is not None:
            from telegram.ext import MessageHandler, filters

            async def _handle_text(update, context):  # noqa: ANN001
                chat_id = update.effective_chat.id if update.effective_chat else None
                if not self._is_admin(chat_id):
                    return  # non-admin free text: total silence
                text = update.message.text if update.message else ""
                reply = self._on_text(text)
                if reply:
                    await update.message.reply_text(reply)

            app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, _handle_text))
        return app

    def run(self) -> None:
        """Block, polling Telegram for admin commands. This is the live run loop."""
        self.build_application().run_polling()
