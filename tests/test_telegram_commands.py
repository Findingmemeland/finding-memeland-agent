"""Every command main.py routes must be REGISTERED with the Telegram handler.

10/09/2026: /scan and /snapshot existed in main.py's `actions` but not in the
CommandHandler list — python-telegram-bot ignores unregistered commands in
silence, so the first live /scan produced no reply and no scan. The dry-run
harness never goes through Telegram, which is why nothing caught it. This
test reads main.py's `actions = {...}` literal statically (build_agent needs
live clients) and pins it against TELEGRAM_COMMANDS.
"""

from __future__ import annotations

import ast
from pathlib import Path

from finding_memeland.telegram.approval_queue import TELEGRAM_COMMANDS, route_command

MAIN = Path(__file__).parent.parent / "src" / "finding_memeland" / "main.py"


def _routed_commands() -> set[str]:
    tree = ast.parse(MAIN.read_text())
    for node in ast.walk(tree):
        if (isinstance(node, ast.Assign) and len(node.targets) == 1
                and isinstance(node.targets[0], ast.Name)
                and node.targets[0].id == "actions"
                and isinstance(node.value, ast.Dict)):
            return {k.value for k in node.value.keys if isinstance(k, ast.Constant)}
    raise AssertionError("main.py: `actions = {...}` literal not found")


def test_every_routed_command_is_registered_with_telegram():
    routed = _routed_commands()
    assert {"scan", "snapshot", "launch", "status"} <= routed
    missing = routed - TELEGRAM_COMMANDS
    assert not missing, f"routed in main.py but the Telegram handler never hears them: {sorted(missing)}"


def test_route_command_dispatches_scan_with_its_argument():
    seen = {}
    reply = route_command("/scan 50", is_admin=True,
                          actions={"scan": lambda arg: seen.setdefault("arg", arg) and "ran"})
    assert reply == "ran" and seen["arg"] == "50"
