"""Entrypoint — boots the agent.

    doppler run -- python -m finding_memeland.main

Wires settings + clients + modules, starts the Telegram command listener
(manual /launch trigger) and the supervisor. Hunts are NOT scheduled — they fire
on the admin's /launch command (deliberate: aligns hunts with marketing pushes).
"""

from __future__ import annotations

import asyncio

from .config import get_settings


async def amain() -> None:
    settings = get_settings()

    # TODO(step 25): construct clients (Anthropic, Supabase, web3, X, Telegram),
    # build Repo, instantiate modules, inject into Orchestrator, start the
    # Telegram command loop + supervisor. Keep running until interrupted.
    #
    #   from .db.client import make_client, Repo
    #   from .social.x_client import XClient
    #   ... etc
    #
    # The agent idles until /launch.
    print(f"[finding-memeland] boot ok (env={settings.fmml_env}). Wiring in step 25.")


def main() -> None:
    asyncio.run(amain())


if __name__ == "__main__":
    main()
